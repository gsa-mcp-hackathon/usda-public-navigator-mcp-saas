"""Validate or persist one versioned Eligibility ruleset seed through Data API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from clusters.eligibility_persistence import (
    HttpCriteriaRulesetRepository,
    read_source_dataset,
)
from scripts.ingest_public_catalog_seed import (
    CatalogSeedError,
    catalog_record_matches_seed,
    load_public_catalog_seed_record,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_ROOT = (REPO_ROOT / "configs/eligibility/criteria-mappings").resolve()
CATALOG_SEED_ROOT = (REPO_ROOT / "configs/eligibility/catalog-seeds").resolve()
MAX_SEED_BYTES = 256_000
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RulesetSeedError(ValueError):
    """A checked-in ruleset seed failed validation or safe application."""


def load_catalog_preflight(
    preflight: object,
    *,
    program_id: str,
    source_id: str,
    source_url: str,
    source_hash: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Load and bind an exact checked-in catalog record to the ruleset source."""
    expected_keys = {"seed_path", "dataset_id", "source_record_id"}
    if not isinstance(preflight, dict) or set(preflight) != expected_keys:
        raise RulesetSeedError("Catalog preflight target is required and exact.")
    seed_path_value = preflight.get("seed_path")
    dataset_id = preflight.get("dataset_id")
    source_record_id = preflight.get("source_record_id")
    if (
        not isinstance(seed_path_value, str)
        or not isinstance(dataset_id, str)
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", dataset_id)
        or not isinstance(source_record_id, str)
        or not source_record_id
    ):
        raise RulesetSeedError("Catalog preflight identity is invalid.")
    catalog_path = (REPO_ROOT / seed_path_value).resolve()
    if not catalog_path.is_relative_to(CATALOG_SEED_ROOT):
        raise RulesetSeedError("Catalog preflight seed must be repository-scoped.")
    try:
        record = load_public_catalog_seed_record(
            catalog_path, dataset_id, source_record_id
        )
    except (CatalogSeedError, OSError, ValueError) as exc:
        raise RulesetSeedError("Catalog preflight seed record is invalid.") from exc

    canonical = record.get("canonical_attributes")
    metadata = record.get("source_metadata")
    semantic_context = record.get("semantic_context")
    if (
        not isinstance(canonical, dict)
        or not isinstance(metadata, dict)
        or not isinstance(semantic_context, dict)
    ):
        raise RulesetSeedError("Catalog preflight record fields are invalid.")
    criteria = canonical.get("eligibility.program.criteria")
    program_sources = canonical.get("eligibility.program.source_references")
    primary_criteria = [
        criterion
        for criterion in (criteria if isinstance(criteria, list) else [])
        if isinstance(criterion, dict) and criterion.get("source_id") == source_id
    ]
    if (
        canonical.get("eligibility.program.program_id") != program_id
        or record.get("source_url") != source_url
        or metadata.get("source_id") != source_id
        or metadata.get("source_content_sha256") != source_hash
        or len(primary_criteria) != 1
        or not isinstance(program_sources, list)
        or any(
            isinstance(item, dict) and item.get("source_id") == source_id
            for item in program_sources
        )
        or semantic_context.get("criteria_source_links") != []
    ):
        raise RulesetSeedError(
            "Catalog primary source must exactly match the ruleset; "
            "no duplicate crosswalk is allowed."
        )
    return (
        {
            "dataset_id": dataset_id,
            "source_record_id": source_record_id,
            "source_id": source_id,
            "source_url": source_url,
            "content_hash": source_hash,
        },
        record,
    )


def verify_catalog_preflight_readback(
    base_url: str,
    token_file: Path,
    target: dict[str, str],
    expected_record: dict[str, Any],
    *,
    catalog_reader: Any = read_source_dataset,
) -> None:
    """Require one exact active catalog record before ruleset compare-and-set."""
    try:
        result = catalog_reader(
            base_url,
            token_file,
            target["dataset_id"],
            source_record_id=target["source_record_id"],
        )
    except Exception as exc:
        raise RulesetSeedError("Catalog preflight read is unavailable.") from exc
    dataset = result.get("dataset") if isinstance(result, dict) else None
    records = result.get("records") if isinstance(result, dict) else None
    pagination = result.get("pagination") if isinstance(result, dict) else None
    if (
        not isinstance(dataset, dict)
        or dataset.get("dataset_id") != target["dataset_id"]
        or dataset.get("source_id") != target["source_id"]
        or not isinstance(records, list)
        or len(records) != 1
        or not isinstance(pagination, dict)
        or pagination.get("total_count") != 1
        or not isinstance(records[0], dict)
        or not catalog_record_matches_seed(records[0], expected_record)
    ):
        raise RulesetSeedError(
            "Active catalog preflight does not match the exact checked-in HCV record."
        )


def canonical_sha256(value: object) -> str:
    """Hash canonical JSON using stable key ordering and UTF-8 bytes."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_ruleset_seed(seed_path: Path) -> tuple[dict[str, Any], str]:
    """Load one repository-scoped seed and verify its source/rule identities."""
    resolved = seed_path.resolve()
    if (
        not resolved.is_relative_to(SEED_ROOT)
        or not resolved.is_file()
        or resolved.stat().st_size > MAX_SEED_BYTES
    ):
        raise RulesetSeedError(
            "Ruleset seed must be a small checked-in file in criteria-mappings."
        )
    try:
        seed = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RulesetSeedError("Ruleset seed is not readable JSON.") from exc
    if not isinstance(seed, dict) or seed.get("manifest_version") != (
        "eligibility-criteria-ruleset-seed-v1"
    ):
        raise RulesetSeedError("Ruleset seed manifest version is unsupported.")

    target = seed.get("target")
    snapshot = seed.get("source_snapshot")
    ruleset = seed.get("ruleset")
    if not isinstance(target, dict) or not isinstance(snapshot, dict):
        raise RulesetSeedError("Ruleset target and source snapshot are required.")
    if not isinstance(ruleset, dict):
        raise RulesetSeedError("Ruleset content is required.")
    program_id = target.get("program_id")
    source_id = target.get("source_id")
    source_url = snapshot.get("url")
    source_hash = snapshot.get("source_content_sha256")
    source_version = snapshot.get("source_version")
    if (
        seed.get("cluster_id") != "eligibility"
        or not isinstance(program_id, str)
        or ruleset.get("program_id") != program_id
        or not isinstance(source_id, str)
        or not isinstance(source_url, str)
        or not isinstance(source_hash, str)
        or SHA256.fullmatch(source_hash) is None
        or not isinstance(source_version, str)
    ):
        raise RulesetSeedError("Ruleset target or source snapshot is invalid.")

    sources = ruleset.get("source_references")
    requirements = ruleset.get("requirements")
    compiled_rules = ruleset.get("compiled_rules")
    program_record = ruleset.get("program_record")
    if not all(
        isinstance(value, list) and value
        for value in (sources, requirements, compiled_rules)
    ) or not isinstance(program_record, dict):
        raise RulesetSeedError(
            "Ruleset source, requirement, and rule lists are required."
        )

    matching_sources = [
        item
        for item in sources
        if isinstance(item, dict)
        and item.get("source_id") == source_id
        and item.get("url") == source_url
        and item.get("source_content_sha256") == source_hash
    ]
    record_sources = program_record.get("sources")
    record_matches = [
        item
        for item in record_sources or []
        if isinstance(item, dict)
        and item.get("source_id") == source_id
        and item.get("url") == source_url
    ]
    if len(matching_sources) != 1 or len(record_matches) != 1:
        raise RulesetSeedError(
            "Ruleset source identity must match the exact cited program source."
        )
    source = matching_sources[0]
    record_source = record_matches[0]
    expected_approval = (
        "approved"
        if source.get("source_verification_status") == "verified"
        else "unverified"
    )
    if (
        source.get("source_type") != "public_source"
        or source.get("source_verification_status") not in {"verified", "unverified"}
        or source.get("approval_status") != expected_approval
        or record_source.get("approval_status") != expected_approval
        or source.get("retrieved_at") != snapshot.get("retrieved_at")
        or record_source.get("retrieved_at") != snapshot.get("retrieved_at")
        or record_source.get("publisher_version") != source_version
        or (
            source.get("source_verification_status") == "unverified"
            and (source.get("policy_entry_id") or source.get("verified_at_utc"))
        )
        or (
            source.get("source_verification_status") == "verified"
            and (
                not isinstance(source.get("policy_entry_id"), str)
                or not source.get("verified_at_utc")
            )
        )
    ):
        raise RulesetSeedError("Ruleset source verification lineage is invalid.")

    criterion_ids = [
        rule.get("requirement_id") for rule in compiled_rules if isinstance(rule, dict)
    ]
    record_criteria = program_record.get("criteria")
    if (
        len(criterion_ids) != len(compiled_rules)
        or not isinstance(record_criteria, list)
        or record_criteria
        != [rule.get("criterion") for rule in compiled_rules if isinstance(rule, dict)]
    ):
        raise RulesetSeedError("Program criteria must exactly match compiled rules.")
    if program_record.get("program_id") != program_id:
        raise RulesetSeedError("Program record identity does not match the target.")

    citations = [
        item.get("citation") for item in requirements if isinstance(item, dict)
    ]
    if len(citations) != len(requirements) or any(
        not isinstance(citation, dict)
        or citation.get("source_id") != source_id
        or citation.get("source_url") != source_url
        or citation.get("source_content_sha256") != source_hash
        or citation.get("source_version") != source_version
        for citation in citations
    ):
        raise RulesetSeedError(
            "Every requirement must cite this exact source snapshot."
        )

    load_catalog_preflight(
        seed.get("catalog_preflight"),
        program_id=program_id,
        source_id=source_id,
        source_url=source_url,
        source_hash=source_hash,
    )

    return ruleset, canonical_sha256(ruleset)


def persist_ruleset_seed(
    seed_path: Path,
    *,
    apply: bool,
    base_url: str | None = None,
    reader_token_file: Path | None = None,
    writer_token_file: Path | None = None,
    repository_factory: Any = HttpCriteriaRulesetRepository,
    catalog_reader: Any = read_source_dataset,
) -> dict[str, object]:
    """Dry-run by default; on apply use the Data API CAS writer and readback."""
    ruleset, digest = load_ruleset_seed(seed_path)
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    target, catalog_record = load_catalog_preflight(
        seed.get("catalog_preflight"),
        program_id=ruleset["program_id"],
        source_id=ruleset["source_references"][0]["source_id"],
        source_url=ruleset["source_references"][0]["url"],
        source_hash=ruleset["source_references"][0]["source_content_sha256"],
    )
    result: dict[str, object] = {
        "mode": "apply" if apply else "dry_run",
        "program_id": ruleset["program_id"],
        "criteria_package_id": ruleset["criteria_package_id"],
        "content_sha256": digest,
        "catalog_preflight_required": True,
        "readback_verified": False,
    }
    if not apply:
        return result
    if not base_url or reader_token_file is None or writer_token_file is None:
        raise RulesetSeedError(
            "Apply requires the Data API URL and separate reader/writer token files."
        )
    verify_catalog_preflight_readback(
        base_url,
        reader_token_file,
        target,
        catalog_record,
        catalog_reader=catalog_reader,
    )
    repository = repository_factory(
        base_url,
        reader_token_file,
        writer_token_file,
        cluster_id="eligibility",
    )
    stored = repository.register(ruleset)
    if (
        stored.get("content_sha256") != digest
        or stored.get("readback_verified") is not True
    ):
        raise RulesetSeedError("Data API did not confirm the exact ruleset readback.")
    result.update(
        {
            "status": stored.get("status"),
            "catalog_preflight_verified": True,
            "readback_verified": True,
        }
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--reader-token-file", type=Path)
    parser.add_argument("--writer-token-file", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    """Run the criteria ruleset seed persistence from the command line."""
    args = _parser().parse_args()
    try:
        result = persist_ruleset_seed(
            args.seed,
            apply=args.apply,
            base_url=os.environ.get("PERSISTENCE_API_URL"),
            reader_token_file=args.reader_token_file,
            writer_token_file=args.writer_token_file,
        )
    except RulesetSeedError as exc:
        raise SystemExit(f"Ruleset seed operation failed: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
