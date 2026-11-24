"""在独立审计会话内连接选定的原生 MCP 服务。"""

import asyncio
import json
import os
import shutil
import sys

from mtcode.config import MCPServerConfig
from mtcode.mcp.manager import MCPManager
from mtcode.mcp.tool_wrapper import _build_params_model
from mtcode.tools.base import Tool


class ConnectionFailure(ValueError):
    """可公开的连接阶段错误。"""


class BoundMCPTool(Tool):
    category = "command"

    def __init__(self, schema):
        self.schema = schema
        self.name = schema["name"]
        self.description = schema["description"]
        self.params_model = _build_params_model(self.name, schema["input_schema"])

    def get_schema(self):
        return self.schema

    async def execute(self, params):
        raise RuntimeError("MCP 调用必须经过会话动作许可")


async def invoke(spec):
    os.chdir(spec["cwd"])
    cfg = MCPServerConfig(**spec["config"])
    if cfg.is_stdio:
        command = shutil.which(cfg.command)
        if command is None:
            raise ConnectionFailure("配置的启动命令不可用")
        cfg.args = ["-p", spec["profile"], command, *cfg.args]
        cfg.command = "/usr/bin/sandbox-exec"
    manager = MCPManager()
    manager.load_configs([cfg])
    try:
        connected = await manager.connect_all()
        if connected.errors:
            raise ConnectionFailure("服务握手或工具列表请求失败")
        if spec["operation"] == "discover":
            return {"tools": [tool.get_schema() for tool in connected.tools]}
        tool = next(
            t
            for t in connected.tools
            if t.name == spec["tool"] and t.get_schema() == spec["schema"]
        )
        result = await tool.execute_raw(
            {k: v for k, v in spec["args"].items() if v is not None}
        )
        if result.is_error:
            return {"is_error": True, "output": "MCP 服务返回调用错误"}
        output = result.output
        for secret in (*cfg.env.values(), *cfg.headers.values()):
            if secret:
                output = output.replace(secret, "[redacted]")
        return {"is_error": False, "output": output}
    finally:
        await manager.shutdown()


def main():
    try:
        print(json.dumps(asyncio.run(invoke(json.load(sys.stdin)))))
    except ConnectionFailure as exc:
        print(json.dumps({"error": str(exc)}))
    except Exception:  # noqa: BLE001 — 服务端原始错误可能包含认证信息
        print(json.dumps({"error": "MCP 服务连接或调用失败"}))
