"""Design sketch for the mapper provider pointer. Not imported by the platform.

See MAPPER-PROVIDER.md. A provider drafts criteria from source text; validation
and scoring stay in the existing deterministic code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Protocol


class MapperProvider(Protocol):
    """Drafts candidate criteria for one source. Never decides eligibility."""

    def map_source(self, source_text: str, schema: dict) -> dict:
        """Return candidate criteria that match the schema."""


# Adapter factories by provider type. Real adapters register here; the existing
# event-platform adapter would be wrapped, not rewritten.
ADAPTERS: dict[str, Callable[[dict], MapperProvider]] = {}


def load_provider(config_path: Path) -> MapperProvider | None:
    """Return the active provider, or None when the client AI should map.

    None means the source is returned as "needs mapping" and the calling agent
    maps it and writes it back through program.criteria.persist.
    """
    config = json.loads(config_path.read_text())
    entry = config["providers"][config["active"]]
    if entry["type"] == "none":
        return None
    return ADAPTERS[entry["type"]](entry)
