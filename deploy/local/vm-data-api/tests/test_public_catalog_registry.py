from __future__ import annotations

import importlib.util
import json
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "public_catalog_registry", API_DIR / "public_catalog_registry.py"
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def valid_registration() -> dict[str, object]:
    return {
        "contract_version": "public-source-registration-v1",
        "source": {
            "source_id": "federal.example",
            "source_name": "Example Federal Programs",
            "source_url": "https://example.gov/programs",
            "access_status": "available",
            "source_context": {
                "authority": {
                    "level": "federal",
                    "owner_name": "Example Federal Agency",
                    "verification_status": "verified",
                    "evidence_url": "https://example.gov/about",
                }
            },
        },
        "datasets": [
            {
                "dataset_id": "federal-example-programs",
                "source_dataset_id": "program-index",
                "title": "Federal program index",
                "landing_page": "https://example.gov/programs",
                "schema_version": "public-data-v1",
                "namespace_ids": ["public"],
                "metadata": {
                    "catalog_namespace": "public",
                    "criteria_coverage": "not_assessed",
                    "record_contract_version": "normalized-public-record-v1",
                    "visibility_scope": "cluster_public",
                    "schema_registry_ids": {"public": "public-schema"},
                    "schema_versions": {"public": "public-data-v1"},
                },
            }
        ],
    }


def test_registration_contract_accepts_verified_federal_source_and_stable_digest():
    registration = valid_registration()

    assert module.validate_source_registration(registration) == registration
    assert module.canonical_registration_digest(
        registration
    ) == module.canonical_registration_digest(registration)


def test_registration_contract_accepts_existing_underscore_source_ids():
    registration = valid_registration()
    registration["source"]["source_id"] = "sam.gov.assistance_listings"

    assert module.validate_source_registration(registration) == registration


def test_fsa_criteria_seed_registration_matches_active_contract():
    seed_path = (
        Path(__file__).resolve().parents[4]
        / "configs/eligibility/catalog-seeds/usda-fsa-farm-loan-criteria.json"
    )
    registration = json.loads(seed_path.read_text(encoding="utf-8"))["registration"]

    assert module.validate_source_registration(registration) == registration


def test_catalog_namespace_is_independent_of_schema_namespaces():
    registration = valid_registration()
    dataset = registration["datasets"][0]
    dataset["namespace_ids"] = ["core", "listing"]
    dataset["metadata"]["catalog_namespace"] = "programs"
    dataset["metadata"]["schema_registry_ids"] = {
        "core": "core-schema-registry",
        "listing": "eligibility-public-listing-schema",
    }
    dataset["metadata"]["schema_versions"] = {
        "core": "core-v1",
        "listing": "public-listing-v1",
    }
    dataset["schema_version"] = "public-listing-v1"

    assert module.validate_source_registration(registration) == registration

    dataset["metadata"]["catalog_namespace"] = "  "
    assert module.validate_source_registration(registration) is None


def test_registration_accepts_versioned_criteria_profile_schema_references():
    registration = valid_registration()
    registration["datasets"][0]["metadata"]["criteria_profile_schema_refs"] = [
        {"registry_id": "core-schema-registry", "schema_version": "core-v4"},
        {
            "registry_id": "eligibility-profile-extension-schema",
            "schema_version": "eligibility-profile-v1",
        },
    ]

    assert module.validate_source_registration(registration) == registration

    registration["datasets"][0]["metadata"]["criteria_profile_schema_refs"][1][
        "schema_version"
    ] = "invalid version"
    assert module.validate_source_registration(registration) is None


def test_registration_contract_rejects_unverified_authority_and_identity_duplicates():
    registration = valid_registration()
    registration["source"]["source_context"]["authority"]["verification_status"] = (
        "unverified"
    )
    assert module.validate_source_registration(registration) is None

    registration = valid_registration()
    registration["datasets"].append(dict(registration["datasets"][0]))
    assert module.validate_source_registration(registration) is None
