"""Call a Gmail MCP server's `send_email` tool from Python (Task 4).

The server is started as a child process and spoken to over stdio with the official MCP
Python SDK (`mcp`). Which server to start comes from an `mcpServers` config in the same
shape as `mcp_config.json.example`; without one, the pinned default below is used.
Decisions: notes/notify_decisions.md (N1, N2).
"""
from __future__ import annotations

import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEFAULT_SERVER = {
    "command": "npx",
    "args": ["-y", "@artymclabin/gmail-mcp@1.2.3"],
    "env": {
        "GMAIL_OAUTH_PATH": "${HOME}/.gmail-mcp/gcp-oauth.keys.json",
        "GMAIL_CREDENTIALS_PATH": "${HOME}/.gmail-mcp/credentials.json",
    },
}
SERVER_NAME = "gmail"
TOOL_NAME = "send_email"
CALL_TIMEOUT_SECONDS = 60.0
_SENT_RE = re.compile(r"Email sent successfully with ID:\s*(\S+)")


@dataclass
class SendResult:
    status: str  # "sent", "failed" or "unconfirmed"
    gmail_message_id: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "sent"


def _expand(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))


def load_server_config(path: str | Path | None) -> dict:
    """The `gmail` entry of an mcpServers config file, or the pinned default if the file
    doesn't exist. `${HOME}` and `~` in args/env are expanded."""
    cfg = DEFAULT_SERVER
    if path and Path(path).is_file():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
        if SERVER_NAME not in servers:
            raise ValueError(f"{path}: no mcpServers.{SERVER_NAME} entry")
        cfg = servers[SERVER_NAME]
    return {
        "command": cfg["command"],
        "args": [_expand(a) for a in cfg.get("args", [])],
        "env": {k: _expand(v) for k, v in (cfg.get("env") or {}).items()},
    }


def interpret(result) -> SendResult:
    """Turn a CallToolResult into a SendResult. Success needs no error flag AND a Gmail id."""
    text = " ".join(getattr(c, "text", "") for c in (result.content or [])).strip()
    if getattr(result, "is_error", False):
        return SendResult("failed", error=text or "tool reported an error")
    m = _SENT_RE.search(text)
    if m:
        return SendResult("sent", gmail_message_id=m.group(1))
    return SendResult("unconfirmed", error=f"no message id in server response: {text[:200]}")


class GmailMcpSender:
    """Async context manager: starts the server once, then `send()` per notice.

        async with GmailMcpSender(config) as sender:
            result = await sender.send(to=[...], subject=..., body=..., in_reply_to=...)
    """

    def __init__(self, server_config: dict, errlog: TextIO | None = None):
        self.config = server_config
        self.errlog = errlog
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "GmailMcpSender":
        params = StdioServerParameters(
            command=self.config["command"], args=self.config["args"],
            env={**os.environ, **self.config["env"]},  # the server needs PATH etc. to start
        )
        self._stack = AsyncExitStack()
        try:
            kwargs = {"errlog": self.errlog} if self.errlog is not None else {}
            read, write = await self._stack.enter_async_context(stdio_client(params, **kwargs))
            self.session = await self._stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            tools = [t.name for t in (await self.session.list_tools()).tools]
            if TOOL_NAME not in tools:
                raise RuntimeError(f"server has no {TOOL_NAME!r} tool (tools: {tools})")
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack:
            await self._stack.aclose()

    async def send(self, to: list[str], subject: str, body: str, in_reply_to: str | None = None) -> SendResult:
        args = {"to": to, "subject": subject, "body": body, "mimeType": "text/plain"}
        if in_reply_to:
            args["inReplyTo"] = in_reply_to  # the server writes In-Reply-To and References from this
        try:
            result = await self.session.call_tool(TOOL_NAME, args, read_timeout_seconds=CALL_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - one failed call must not crash live mode
            return SendResult("failed", error=f"{type(exc).__name__}: {exc}")
        return interpret(result)
