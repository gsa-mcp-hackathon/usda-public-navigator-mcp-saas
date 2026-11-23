"""Load the Data API's non-secret container configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_PATH_ENV = "MCP_SAAS_DATA_API_CONFIG_FILE"


def load_config(default_path: Path) -> dict[str, object]:
    """Load JSON configuration, allowing a protected external file for a VM."""
    configured_path = os.environ.get(CONFIG_PATH_ENV)
    path = Path(configured_path) if configured_path else default_path
    if configured_path and not path.is_absolute():
        raise ValueError(f"{CONFIG_PATH_ENV} must be an absolute path.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Data API configuration must be a JSON object.")
    return payload
