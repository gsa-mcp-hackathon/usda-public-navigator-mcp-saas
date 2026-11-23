"""Dispatch tenant tool registration to the requested versioned cluster."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol

from fastmcp import FastMCP

from clusters.content_authorization import RuntimePrincipal
from clusters.eligibility_candidate import (
    register_program_candidate_normalizer,
    register_program_criteria_package_compiler,
    register_program_requirement_extractor,
)
from clusters.eligibility_candidate_evaluation import (
    register_request_scoped_candidate_tools,
)
from clusters.eligibility_inventory import (
    CRITERIA_EXTRACTION_VERSION,
    CRITERIA_NORMALIZED_V1_VERSION,
    CRITERIA_NORMALIZED_VERSION,
    PROGRAM_CANDIDATE_VERSION,
    PUBLIC_CATALOG_INVENTORY_VERSION,
    register_public_catalog_inventory_tools,
    register_search_program_inventory,
)
from clusters.eligibility_persistence import HttpCriteriaRulesetRepository
from clusters.eligibility_profile_projection import ProfileFixtureReader
from clusters.eligibility_public_search import PublicSearchProvider
from clusters.eligibility_v0 import register_tools as register_eligibility_tools
from clusters.eligibility_versions import (
    CRITERIA_PERSISTENCE_VERSION,
    REQUEST_SCOPED_CANDIDATE_VERSION,
    parse_eligibility_version,
    supports_eligibility_feature,
)
from clusters.sample_directory import register_tools as register_directory_tools
from example_server.models import TenantProgram
from example_server.storage import CatalogRepository


class ClusterToolRegistrar(Protocol):
    """Register one supported cluster version on a tenant MCP application."""

    def __call__(
        self,
        app: FastMCP,
        *,
        tenant_id: str,
        cluster_id: str,
        pattern_version: str,
        programs: list[TenantProgram],
        catalog_repository: CatalogRepository | None,
        profile_fixture_reader: ProfileFixtureReader | None = None,
        public_search_provider: PublicSearchProvider | None = None,
        cluster_mcp_endpoints: Mapping[str, str] | None = None,
        criteria_ruleset_repository: HttpCriteriaRulesetRepository | None = None,
        cluster_config: Mapping[str, object],
    ) -> None:
        """Register the cluster's tools on a tenant application."""


CLUSTER_TOOL_REGISTRARS: dict[tuple[str, str], ClusterToolRegistrar] = {
    ("sample-directory", "1.0.0"): register_directory_tools,
}


def llm_mapper_enabled() -> bool:
    """Return whether mapping is routed to the optional LLM mapper component."""
    return os.environ.get("MCP_LLM_MAPPER_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def register_cluster_tools(
    app: FastMCP,
    *,
    tenant_id: str,
    cluster_id: str,
    pattern_version: str,
    programs: list[TenantProgram],
    catalog_repository: CatalogRepository | None,
    profile_fixture_reader: ProfileFixtureReader | None = None,
    public_search_provider: PublicSearchProvider | None = None,
    cluster_config: Mapping[str, object],
    cluster_mcp_endpoints: Mapping[str, str] | None = None,
    criteria_ruleset_repository: HttpCriteriaRulesetRepository | None = None,
) -> None:
    """Register tools only for an explicitly supported cluster/version pair."""
    registrar = (
        register_eligibility_tools
        if cluster_id == "eligibility"
        and parse_eligibility_version(pattern_version) is not None
        else CLUSTER_TOOL_REGISTRARS.get((cluster_id, pattern_version))
    )
    if registrar is None:
        raise ValueError(f"Unsupported cluster module: {cluster_id}@{pattern_version}.")
    registration = {
        "tenant_id": tenant_id,
        "cluster_id": cluster_id,
        "pattern_version": pattern_version,
        "programs": programs,
        "catalog_repository": catalog_repository,
        "profile_fixture_reader": profile_fixture_reader,
        "cluster_config": cluster_config,
    }
    policy_reader = getattr(
        catalog_repository, "read_active_public_search_policy", None
    )
    policy_reader = policy_reader if callable(policy_reader) else None
    if cluster_id == "eligibility":
        registrar(
            app,
            **registration,
            public_search_provider=public_search_provider,
            cluster_mcp_endpoints=cluster_mcp_endpoints or {},
            criteria_ruleset_repository=criteria_ruleset_repository,
        )
        if supports_eligibility_feature(
            pattern_version, PUBLIC_CATALOG_INVENTORY_VERSION
        ):
            register_public_catalog_inventory_tools(
                app,
                catalog_repository=catalog_repository,
                pattern_version=pattern_version,
            )
        if supports_eligibility_feature(
            pattern_version, REQUEST_SCOPED_CANDIDATE_VERSION
        ):
            register_request_scoped_candidate_tools(
                app,
                catalog_repository=catalog_repository,
                tenant_id=tenant_id,
                cluster_id=cluster_id,
                pattern_version=pattern_version,
            )
    else:
        registrar(app, **registration)


def register_cluster_inventory_tools(
    app: FastMCP,
    *,
    cluster_id: str,
    pattern_version: str,
    catalog_repository: CatalogRepository | None,
    public_search_provider: PublicSearchProvider | None = None,
    criteria_ruleset_repository: HttpCriteriaRulesetRepository | None = None,
) -> None:
    """Expose only the approved read-only inventory operation for a cluster."""
    if cluster_id != "eligibility" or not supports_eligibility_feature(
        pattern_version, "0.1.6"
    ):
        raise ValueError(
            f"Unsupported cluster inventory module: {cluster_id}@{pattern_version}."
        )
    policy_reader = getattr(
        catalog_repository, "read_active_public_search_policy", None
    )
    policy_reader = policy_reader if callable(policy_reader) else None
    register_search_program_inventory(
        app,
        cluster_id=cluster_id,
        pattern_version=pattern_version,
        catalog_repository=catalog_repository,
        public_search_provider=public_search_provider,
        criteria_ruleset_repository=criteria_ruleset_repository,
    )
    if supports_eligibility_feature(pattern_version, PROGRAM_CANDIDATE_VERSION):
        register_program_candidate_normalizer(app)
    if supports_eligibility_feature(pattern_version, PUBLIC_CATALOG_INVENTORY_VERSION):
        register_public_catalog_inventory_tools(
            app,
            catalog_repository=catalog_repository,
            pattern_version=pattern_version,
        )
    if supports_eligibility_feature(pattern_version, CRITERIA_EXTRACTION_VERSION):
        normalized_contract_version = (
            "v1"
            if pattern_version == CRITERIA_NORMALIZED_V1_VERSION
            else "v2"
            if supports_eligibility_feature(
                pattern_version, CRITERIA_NORMALIZED_VERSION
            )
            else None
        )
        register_program_requirement_extractor(
            app, normalized_contract_version=normalized_contract_version
        )
        register_program_criteria_package_compiler(app)
    if supports_eligibility_feature(pattern_version, CRITERIA_PERSISTENCE_VERSION):
        if criteria_ruleset_repository is None:
            raise ValueError(
                "The active criteria persistence operation requires its repository."
            )
        from clusters.eligibility_criteria_persistence import (
            register_criteria_ruleset_persistence,
        )

        register_criteria_ruleset_persistence(
            app,
            repository=criteria_ruleset_repository,
            policy_reader=policy_reader,
            runtime_principal=RuntimePrincipal("cluster", cluster_id),
        )
        # MCP_LLM_MAPPER_ENABLED=true routes mapping to the LLM mapper component;
        # false leaves a source as "needs mapping" for the calling AI agent to
        # map through the client mapping workflow. Absent module behaves as false.
        register_runtime_criteria_mapper = None
        if llm_mapper_enabled():
            try:
                from clusters.eligibility_mapper import (
                    register_runtime_criteria_mapper,
                )
            except ImportError:
                register_runtime_criteria_mapper = None
        if register_runtime_criteria_mapper is not None:
            register_runtime_criteria_mapper(
                app,
                criteria_ruleset_repository,
                policy_reader=policy_reader,
            )
    if supports_eligibility_feature(pattern_version, REQUEST_SCOPED_CANDIDATE_VERSION):
        register_request_scoped_candidate_tools(
            app,
            register_package_writer=False,
        )
