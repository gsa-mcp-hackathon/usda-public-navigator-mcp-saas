"""Activate the reviewed shared and Eligibility schemas without tenant writes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import SecretStr, ValidationError

from example_server.registry import (
    PostgresRegistryRepository,
    SchemaProjectionManifest,
)
from example_server.storage import StorageUnavailable


def main() -> None:
    """Apply one schema-only projection without printing database credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("configs/registry/vs8-schema-projection.v4.json"),
    )
    parser.add_argument("--database-url-file", type=Path, required=True)
    parser.add_argument("--git-revision", required=True)
    args = parser.parse_args()

    try:
        manifest = SchemaProjectionManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
        schemas = manifest.load_schemas(Path(__file__).resolve().parents[1])
        database_url = args.database_url_file.read_text(encoding="utf-8").strip()
        if not database_url:
            raise ValueError("Database URL file is empty.")
        repository = PostgresRegistryRepository(SecretStr(database_url))
        repository.reconcile_schemas_only(manifest, schemas, args.git_revision)
    except (OSError, ValueError, ValidationError, StorageUnavailable) as exc:
        print(
            f"Schema reconciliation failed ({type(exc).__name__}); "
            "review the schema manifest and protected database configuration.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    print(
        f"Schema-only reconciliation succeeded: {len(schemas)} schemas; "
        f"revision {args.git_revision}."
    )


if __name__ == "__main__":
    main()
