"""Shared HTTP identity for Persistence Access API clients."""

PERSISTENCE_API_USER_AGENT = "MCP-SaaS-Core/0.2.0"


def persistence_api_headers(token: str) -> dict[str, str]:
    """Build authenticated headers accepted by the sandbox tunnel policy."""
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": PERSISTENCE_API_USER_AGENT,
    }
