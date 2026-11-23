"""Reconcile a reviewed registry manifest into the active runtime projection."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pydantic import SecretStr, ValidationError

from example_server.registry import PostgresRegistryRepository, RegistryManifest
from example_server.storage import StorageUnavailable


def main() -> None:
    """Apply one manifest without printing secrets or database errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database-url-file", type=Path, required=True)
    parser.add_argument("--git-revision", required=True)
    args = parser.parse_args()

    try:
        manifest = RegistryManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
        schemas = manifest.load_schemas(Path(__file__).resolve().parents[1])
        database_url = args.database_url_file.read_text(encoding="utf-8").strip()
        if not database_url:
            raise ValueError("Database URL file is empty.")
        endpoints = manifest.resolve_endpoints(os.environ)
        repository = PostgresRegistryRepository(SecretStr(database_url))
        repository.reconcile(manifest, endpoints, args.git_revision, schemas)
    except (OSError, ValueError, ValidationError, StorageUnavailable) as exc:
        print(
            f"Registry reconciliation failed ({type(exc).__name__}); "
            "review manifest, endpoint, and database configuration.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    print(
        "Registry reconciliation succeeded: "
        f"{len(manifest.patterns)} patterns, "
        f"{len(manifest.use_cases)} use cases, "
        f"{len(manifest.tenants)} tenants; revision {args.git_revision}."
    )


if __name__ == "__main__":
    main()
