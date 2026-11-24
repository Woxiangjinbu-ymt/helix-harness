"""轮次受理时固定非秘密配置。凭据变更后旧引用立即失效。"""

import hashlib
import hmac
import os
import secrets
from dataclasses import asdict

from webdemo import providers


def credential_version(root, secret):
    keyfile = root / "credential-salt"
    if not keyfile.exists():
        private = root / (".salt-" + secrets.token_hex(12))
        try:
            with open(
                os.open(private, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb"
            ) as stream:
                stream.write(os.urandom(32))
            try:
                os.link(private, keyfile)
            except FileExistsError:
                pass
        finally:
            private.unlink(missing_ok=True)
    return hmac.new(keyfile.read_bytes(), secret.encode(), hashlib.sha256).hexdigest()


def command_hooks(config):
    from mewcode.hooks.loader import load_hooks

    from .store import digest

    # 阶段一接入原生同步命令 Hook。快照只保留绑定标识和内容摘要。
    selected = [
        raw
        for raw in config.raw_hooks
        if isinstance(raw, dict)
        and raw.get("event") in ("pre_tool_use", "post_tool_use")
        and not raw.get("async")
        and raw.get("action", {}).get("type") == "command"
    ]
    hooks = load_hooks(selected)
    if len({hook.id for hook in hooks}) != len(hooks):
        raise ValueError("Coding Hook ID 重复")
    return selected, [
        {"id": hook.id, "digest": digest(raw)} for hook, raw in zip(hooks, selected)
    ]


def snapshot(profile, root):
    config, provider = providers.select(profile)
    return {
        "schema_version": 1,
        "provider": provider.name,
        "protocol": provider.protocol,
        "endpoint": provider.base_url,
        "model": provider.model,
        "thinking": provider.thinking,
        "context_window": provider.get_context_window(),
        "max_output_tokens": provider.get_max_output_tokens(),
        "credential_ref": credential_version(root, provider.resolve_api_key()),
        "permission_mode": config.permission_mode,
        "sandbox": asdict(config.sandbox),
        "extensions": {"skills": [], "mcp": [], "hooks": command_hooks(config)[1]},
        "harness_ref": None,
        "budget": {"max_iterations": 40},
    }
