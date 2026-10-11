"""A stand-in Gmail MCP server for tests: same `send_email` tool shape as
@artymclabin/gmail-mcp, but it only records calls; nothing is ever sent.

Environment:
    FAKE_GMAIL_LOG   file to append one JSON line per call (required)
    FAKE_GMAIL_FAIL  if set, calls whose subject contains this text fail ("*" = every call)
"""
import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

server = MCPServer("fake-gmail")


@server.tool()
def send_email(to: list[str], subject: str, body: str, inReplyTo: str | None = None,
               mimeType: str = "text/plain", cc: list[str] | None = None,
               bcc: list[str] | None = None) -> str:
    """Records the call instead of sending."""
    fail = os.environ.get("FAKE_GMAIL_FAIL")
    if fail and (fail == "*" or fail in subject):
        raise ToolError("simulated Gmail API error: quota exceeded")
    log = os.environ["FAKE_GMAIL_LOG"]
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"to": to, "subject": subject, "body": body, "inReplyTo": inReplyTo,
                             "mimeType": mimeType, "cc": cc, "bcc": bcc}) + "\n")
    with open(log, encoding="utf-8") as fh:
        n = sum(1 for _ in fh)
    return f"Email sent successfully with ID: fake{n:04d}"


if __name__ == "__main__":
    server.run("stdio")
