"""Validated request context shared by tenant adapters and tools."""

from contextvars import ContextVar

MCP_CORRELATION_ID: ContextVar[str | None] = ContextVar(
    "mcp_correlation_id", default=None
)
