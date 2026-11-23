from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest


@pytest.fixture(scope="session")
def database_url() -> str:
    """Initialize the disposable PostgreSQL integration database once."""
    value = os.getenv("MCP_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set MCP_TEST_DATABASE_URL to run PostgreSQL integration checks.")

    repository_root = Path(__file__).resolve().parents[4]
    migrations = sorted((repository_root / "db/migrations").glob("*.sql"))
    with psycopg.connect(value) as connection:
        for migration in migrations:
            connection.execute(migration.read_text(encoding="utf-8"))
    return value
