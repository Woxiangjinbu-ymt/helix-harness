"""Adapt one Coding worker's SDK requests while retaining the frozen MewCode agent loop."""

from contextvars import ContextVar
from urllib.parse import urlsplit

from mewcode.client import _mark_last_user_tail_for_cache, _supports_adaptive_thinking
from mewcode.conversation import ThinkingBlock
from mewcode.conversation_pairing import ensure_tool_pairing
from mewcode.memory.session import (
    SessionRecord,
    make_compact_boundary,
    records_to_messages,
)
from mewcode.serialization import build_messages

from .attachments import hydrate


def replay(records, data, snapshot):
    messages = []
    for record in records:
        restored = records_to_messages([record])
        if record.is_compact_boundary() and isinstance(record.content, dict):
            for item, msg in zip(record.content.get("keep", []), restored[1:]):
                if "web_input" in item:
                    hydrate(msg, item["web_input"], data, snapshot)
                elif "web_reply" in item:
                    restore_reply(msg, item["web_reply"], snapshot)
        elif record.type == "web_input":
            hydrate(restored[0], record.content, data, snapshot)
        elif record.type == "web_reply":
            restore_reply(restored[0], record.content, snapshot)
        messages.extend(restored)
    return messages


def compact_record(summary, keep):
    record = make_compact_boundary(summary, keep)
    for item, message in zip(record.content["keep"], keep):
        if hasattr(message, "web_input"):
            item.update(content=message.web_input["text"], web_input=message.web_input)
        elif hasattr(message, "web_reply"):
            item["web_reply"] = message.web_reply
    return record


def restore_reply(message, reply, snapshot):
    message.content = reply["text"]
    if (
        reply["profile"] == snapshot["profile_id"]
        and reply["model"] == snapshot["model"]
    ):
        message.thinking_blocks = [ThinkingBlock(**b) for b in reply["blocks"]]
        message.web_reply = reply


def user_record(text, attachments, stamp):
    if attachments:
        return SessionRecord(
            "user",
            {"text": text, "ids": [a["id"] for a in attachments]},
            stamp,
            type="web_input",
        )
    return SessionRecord("user", text, stamp)


def wire_messages(conversation, protocol):
    """Keep native tool pairing, message grouping and cache markers for each protocol."""
    output, plain = [], []
    for message in ensure_tool_pairing(conversation.get_messages()):
        parts = getattr(message, "web_parts", None)
        if not parts:
            plain.append(message)
            continue
        output.extend(build_messages(plain, protocol))
        plain.clear()
        blocks = []
        for part in parts:
            if isinstance(part, str):
                blocks.append(
                    {
                        "type": "input_text" if protocol == "openai" else "text",
                        "text": part,
                    }
                )
                continue
            mime, encoded = part
            if protocol == "anthropic":
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": encoded,
                        },
                    }
                )
            else:
                url = "data:" + mime + ";base64," + encoded
                if protocol == "openai":
                    blocks.append({"type": "input_image", "image_url": url})
                else:
                    blocks.append({"type": "image_url", "image_url": {"url": url}})
        output.append({"role": "user", "content": blocks})
    output.extend(build_messages(plain, protocol))
    if protocol == "anthropic":
        _mark_last_user_tail_for_cache(output)
    return output


class CodingModel:
    """Install request adapters on the SDK instance owned by this single worker."""

    def __init__(self, supplied, snapshot, channel):
        from helix_harness import providers

        self.channel = channel
        self.protocol = supplied.protocol
        self.snapshot = snapshot
        self.cached = 0
        self.latest = None
        self.conversation = ContextVar("coding_model_input")
        self.inner = (
            None if snapshot.get("source") == "demo" else providers.client(supplied)
        )
        if self.inner is None:
            return
        sdk = self.inner._client
        if self.protocol == "anthropic":
            stream = sdk.messages.stream
            sdk.messages.stream = lambda **kw: stream(**self.request(kw))
        elif self.protocol == "openai":
            create = sdk.responses.create
            sdk.responses.create = lambda **kw: create(**self.request(kw))
        else:
            create = sdk.chat.completions.create

            async def chat(**kw):
                response = await create(**self.request(kw))
                return self.normalized_chunks(response)

            sdk.chat.completions.create = chat

    def request(self, kw):
        conversation = self.conversation.get()
        if any(getattr(m, "web_parts", None) for m in conversation.get_messages()):
            messages = wire_messages(conversation, self.protocol)
            if self.protocol == "openai":
                kw["input"] = messages
            else:
                system = [m for m in kw["messages"] if m["role"] == "system"]
                kw["messages"] = system + messages
        level = self.snapshot.get("thinking_level", "default")
        if self.protocol == "anthropic":
            if self.snapshot["thinking"] and _supports_adaptive_thinking(
                self.snapshot["model"]
            ):
                kw["thinking"] = {"type": "adaptive"}
        elif level != "default":
            if self.protocol == "openai":
                kw["reasoning"] = {"effort": level}
            else:
                if urlsplit(self.snapshot["endpoint"]).hostname == "api.deepseek.com":
                    kw["extra_body"] = {
                        "thinking": {
                            "type": "disabled" if level == "off" else "enabled"
                        }
                    }
                if level != "off":
                    kw["reasoning_effort"] = level
        return kw

    @staticmethod
    async def normalized_chunks(response):
        # Some gateways report usage alongside the final choice. The native parser
        # consumes it on a separate chunk. Deliver the last report exactly once.
        usage_chunk = None
        try:
            async for chunk in response:
                if chunk.usage:
                    usage_chunk = chunk.model_copy(update={"choices": []})
                if chunk.choices:
                    yield chunk
            if usage_chunk is not None:
                yield usage_chunk
        finally:
            await response.close()

    def set_max_output_tokens(self, tokens):
        if self.inner:
            self.inner.set_max_output_tokens(tokens)

    async def stream(self, conversation, *args, **kwargs):
        from mewcode.tools.base import StreamEnd, TextDelta

        await self.channel.request("model_permission")
        if self.inner is None:
            yield TextDelta(text="Helix Demo · local demonstration")
            yield StreamEnd(stop_reason="end_turn")
            return
        token = self.conversation.set(conversation)
        try:
            async for event in self.inner.stream(conversation, *args, **kwargs):
                if isinstance(event, StreamEnd):
                    self.latest = event
                    self.cached += event.cache_read + event.cache_creation
                yield event
        finally:
            self.conversation.reset(token)
