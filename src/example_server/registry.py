"""Validated Git definitions and the PostgreSQL active registry projection."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    model_validator,
)

from example_server.storage import StorageUnavailable

LifecycleState = Literal["active", "inactive", "deprecated"]


def _same_schema_meaning(
    registered: Mapping[str, object], desired: Mapping[str, object]
) -> bool:
    """Treat an omitted legacy empty tag map as the current default empty map."""
    registered_definition = dict(registered)
    desired_definition = dict(desired)
    if registered_definition.get("tags") == {}:
        registered_definition.pop("tags")
    if desired_definition.get("tags") == {}:
        desired_definition.pop("tags")
    return registered_definition == desired_definition


def _schema_value_matches(value: object, value_type: object) -> bool:
    """Check JSON-compatible values against a registered tag value type."""
    matches = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: (
            isinstance(item, (int, float)) and not isinstance(item, bool)
        ),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
    }.get(value_type)
    return matches is not None and matches(value)


class OperationDefinition(BaseModel):
    """Cluster-owned tool contract selected by a stable operation ID."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1)
    mcp_tool_name: str = Field(min_length=1)
    request_schema_id: str = Field(min_length=1)
    response_schema_id: str = Field(min_length=1)
    access_mode: Literal["read_only", "idempotent_write"]
    route_scope: Literal["tenant", "cluster"] = "tenant"


class PatternDefinition(BaseModel):
    """Versioned, reusable cluster metadata with no tenant-specific values."""

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1)
    pattern_version: str = Field(min_length=1)
    lifecycle_state: LifecycleState
    title: str = Field(min_length=1)
    catalog_names: list[str] = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    operations: list[OperationDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def ensure_unique_contract_ids(self) -> PatternDefinition:
        """Reject a pattern whose operations repeat a contract id."""
        operation_ids = [item.operation_id for item in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("Pattern operation IDs must be unique.")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("Pattern capability IDs must be unique.")
        if len(self.catalog_names) != len(set(self.catalog_names)):
            raise ValueError("Pattern catalog names must be unique.")
        return self


class SchemaDefinition(BaseModel):
    """Complete, versioned semantic schema persisted by the active registry."""

    model_config = ConfigDict(extra="allow")

    registry_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    namespaces: dict[str, str] = Field(min_length=1)
    dimensions: dict[str, str] = Field(min_length=1)
    attributes: dict[str, dict[str, object]] = Field(default_factory=dict)
    relationships: dict[str, dict[str, object]] = Field(default_factory=dict)
    tags: dict[str, dict[str, object]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_attribute_dimensions(self) -> SchemaDefinition:
        """Reject schema attributes that use an undeclared dimension namespace."""
        unknown_namespaces = {
            item.split(".", maxsplit=1)[0]
            for item in [
                *self.dimensions,
                *self.attributes,
                *self.relationships,
                *self.tags,
            ]
            if item.split(".", maxsplit=1)[0] not in self.namespaces
        }
        if unknown_namespaces:
            raise ValueError("Schema identifiers reference unregistered namespaces.")
        unknown = {
            str(value.get("dimension_id"))
            for value in self.attributes.values()
            if value.get("dimension_id") not in self.dimensions
        }
        if unknown:
            raise ValueError("Schema attributes reference unregistered dimensions.")
        for tag_id, definition in self.tags.items():
            if (
                not isinstance(definition, dict)
                or not isinstance(definition.get("meaning"), str)
                or not definition["meaning"].strip()
                or definition.get("value_type")
                not in {"string", "integer", "number", "boolean", "object", "array"}
                or definition.get("lifecycle_state") not in {"active", "deprecated"}
                or not isinstance(definition.get("applies_to"), list)
                or not definition["applies_to"]
                or any(
                    item
                    not in {
                        "profile",
                        "public_dataset",
                        "pattern_template",
                        "tenant",
                        "source",
                        "route",
                    }
                    for item in definition["applies_to"]
                )
            ):
                raise ValueError(f"Invalid global tag definition: {tag_id}.")
            allowed_values = definition.get("allowed_values")
            if allowed_values is not None and (
                not isinstance(allowed_values, list)
                or not allowed_values
                or any(
                    not _schema_value_matches(item, definition["value_type"])
                    for item in allowed_values
                )
                or len({repr(item) for item in allowed_values}) != len(allowed_values)
            ):
                raise ValueError(f"Invalid allowed values for global tag: {tag_id}.")
            if definition.get("lifecycle_state") == "deprecated":
                replacement = definition.get("replaced_by")
                if not isinstance(replacement, str) or replacement not in self.tags:
                    raise ValueError(
                        f"Deprecated tag {tag_id} requires a replacement "
                        "in this version."
                    )
        return self


class SchemaDocumentReference(BaseModel):
    """Repository-relative source for a schema definition to project."""

    model_config = ConfigDict(extra="forbid")

    registry_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    path: str = Field(min_length=1)
    lifecycle_state: LifecycleState = "active"


class UseCaseDefinition(BaseModel):
    """Stable capability mapping to one versioned pattern operation."""

    model_config = ConfigDict(extra="forbid")

    use_case_id: str = Field(min_length=1)
    use_case_version: str = Field(min_length=1)
    pattern_id: str = Field(min_length=1)
    pattern_version: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    lifecycle_state: LifecycleState
    tenant_ids: list[str] = Field(default_factory=list)


class TenantDefinition(BaseModel):
    """Tenant-to-pattern binding with an environment-supplied MCP endpoint."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    pattern_id: str = Field(min_length=1)
    pattern_version: str = Field(min_length=1)
    endpoint_environment_variable: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    lifecycle_state: LifecycleState
    cluster_config: dict[str, object] = Field(default_factory=dict)

    @property
    def registration_id(self) -> str:
        """Return the stable id for this tenant, pattern and version registration."""
        return tenant_registration_id(
            self.tenant_id, self.pattern_id, self.pattern_version
        )


class CatalogBindingDefinition(BaseModel):
    """Tenant-owned catalog context over one reusable public dataset identity."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1)
    pattern_id: str = Field(min_length=1)
    pattern_version: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    binding_context: dict[str, object]


def tenant_registration_id(
    tenant_id: str, pattern_id: str, pattern_version: str
) -> str:
    """Encode a tenant's cluster-version binding for endpoint configuration."""
    return f"{tenant_id}|{pattern_id}@{pattern_version}"


class RegistryManifest(BaseModel):
    """Reviewed desired definitions for one bounded registry projection."""

    model_config = ConfigDict(extra="forbid")

    manifest_id: str = Field(min_length=1)
    manifest_version: str = Field(min_length=1)
    schemas: list[SchemaDocumentReference] = Field(min_length=1)
    patterns: list[PatternDefinition] = Field(min_length=1)
    use_cases: list[UseCaseDefinition] = Field(min_length=1)
    tenants: list[TenantDefinition] = Field(default_factory=list)
    catalog_bindings: list[CatalogBindingDefinition] | None = None

    @model_validator(mode="after")
    def validate_references(self) -> RegistryManifest:
        """Reject a manifest whose references do not resolve to declared items."""
        schema_keys = [(item.registry_id, item.schema_version) for item in self.schemas]
        if len(schema_keys) != len(set(schema_keys)):
            raise ValueError("Schema registry ID and version pairs must be unique.")
        patterns = {(p.pattern_id, p.pattern_version): p for p in self.patterns}
        if len(patterns) != len(self.patterns):
            raise ValueError("Pattern ID and version pairs must be unique.")

        tenants = {
            (tenant.tenant_id, tenant.pattern_id, tenant.pattern_version): tenant
            for tenant in self.tenants
        }
        if len(tenants) != len(self.tenants):
            raise ValueError(
                "Tenant registration keys must be unique within a manifest."
            )
        for tenant in self.tenants:
            if (tenant.pattern_id, tenant.pattern_version) not in patterns:
                raise ValueError("Tenant references an unregistered pattern version.")

        use_case_keys: set[tuple[str, str]] = set()
        for use_case in self.use_cases:
            key = (use_case.use_case_id, use_case.use_case_version)
            if key in use_case_keys:
                raise ValueError("Use-case ID and version pairs must be unique.")
            use_case_keys.add(key)
            if len(use_case.tenant_ids) != len(set(use_case.tenant_ids)):
                raise ValueError("Use-case tenant IDs must be unique.")
            pattern = patterns.get((use_case.pattern_id, use_case.pattern_version))
            if pattern is None:
                raise ValueError("Use case references an unregistered pattern version.")
            if use_case.operation_id not in {
                operation.operation_id for operation in pattern.operations
            }:
                raise ValueError(
                    "Use case references an unregistered pattern operation."
                )
            for tenant_id in use_case.tenant_ids:
                tenant = tenants.get(
                    (tenant_id, use_case.pattern_id, use_case.pattern_version)
                )
                if tenant is None:
                    raise ValueError("Use case references an unregistered tenant.")
                if (tenant.pattern_id, tenant.pattern_version) != (
                    use_case.pattern_id,
                    use_case.pattern_version,
                ):
                    raise ValueError(
                        "Use-case tenant has an incompatible pattern version."
                    )
                if (
                    use_case.lifecycle_state == "active"
                    and tenant.lifecycle_state != "active"
                ):
                    raise ValueError(
                        "Active use cases require active tenant registrations."
                    )
        if self.catalog_bindings is not None:
            binding_keys = [
                (
                    item.dataset_id,
                    item.pattern_id,
                    item.pattern_version,
                    item.tenant_id,
                )
                for item in self.catalog_bindings
            ]
            if len(binding_keys) != len(set(binding_keys)):
                raise ValueError("Tenant catalog binding identities must be unique.")
            for binding in self.catalog_bindings:
                tenant = tenants.get(
                    (binding.tenant_id, binding.pattern_id, binding.pattern_version)
                )
                if tenant is None:
                    raise ValueError("Catalog binding references an unknown tenant.")
                if not any(
                    use_case.pattern_id == binding.pattern_id
                    and use_case.pattern_version == binding.pattern_version
                    and binding.tenant_id in use_case.tenant_ids
                    for use_case in self.use_cases
                ):
                    raise ValueError(
                        "Catalog binding tenant is not enabled for a matching use case."
                    )
        return self

    def load_schemas(self, repository_root: Path) -> list[SchemaDefinition]:
        """Load validated schema JSON while preventing paths outside the repo."""
        root = repository_root.resolve()
        schemas: list[SchemaDefinition] = []
        for reference in self.schemas:
            path = (root / reference.path).resolve()
            if not path.is_relative_to(root):
                raise ValueError(
                    "Schema document path must stay within the repository."
                )
            schema = SchemaDefinition.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if (schema.registry_id, schema.schema_version) != (
                reference.registry_id,
                reference.schema_version,
            ):
                raise ValueError(
                    "Schema identity does not match its manifest reference."
                )
            schemas.append(schema)
        return schemas

    def resolve_endpoints(self, environment: Mapping[str, str]) -> dict[str, HttpUrl]:
        """Resolve only active tenant endpoints from the current environment."""
        endpoints: dict[str, HttpUrl] = {}
        for tenant in self.tenants:
            if tenant.lifecycle_state != "active":
                continue
            value = environment.get(tenant.endpoint_environment_variable, "").strip()
            if not value:
                raise ValueError(
                    "An active tenant endpoint environment variable is missing."
                )
            endpoints[tenant.registration_id] = TypeAdapter(HttpUrl).validate_python(
                value
            )
        return endpoints


class SchemaProjectionManifest(BaseModel):
    """Reviewed schema-only projection with no pattern or tenant registrations."""

    model_config = ConfigDict(extra="forbid")

    manifest_id: str = Field(min_length=1)
    manifest_version: str = Field(min_length=1)
    schemas: list[SchemaDocumentReference] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_schema_identities(self) -> SchemaProjectionManifest:
        """Reject a schema projection that repeats a schema identity."""
        identities = [(item.registry_id, item.schema_version) for item in self.schemas]
        if len(identities) != len(set(identities)):
            raise ValueError("Schema registry ID and version pairs must be unique.")
        return self

    def load_schemas(self, repository_root: Path) -> list[SchemaDefinition]:
        """Load validated schema JSON while preventing paths outside the repo."""
        root = repository_root.resolve()
        schemas: list[SchemaDefinition] = []
        for reference in self.schemas:
            path = (root / reference.path).resolve()
            if not path.is_relative_to(root):
                raise ValueError(
                    "Schema document path must stay within the repository."
                )
            schema = SchemaDefinition.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if (schema.registry_id, schema.schema_version) != (
                reference.registry_id,
                reference.schema_version,
            ):
                raise ValueError(
                    "Schema identity does not match its manifest reference."
                )
            schemas.append(schema)
        return schemas


class RegisteredTenant(BaseModel):
    """Resolved active tenant registration returned by the registry reader."""

    tenant_id: str
    pattern_id: str
    pattern_version: str
    endpoint: HttpUrl

    @property
    def registration_id(self) -> str:
        """Return the stable id for this tenant, pattern and version registration."""
        return tenant_registration_id(
            self.tenant_id, self.pattern_id, self.pattern_version
        )


class ResolvedUseCase(BaseModel):
    """Use case, its pattern contract, and eligible active tenant routes."""

    use_case: UseCaseDefinition
    pattern: PatternDefinition
    tenants: list[RegisteredTenant]


class RegistryRepository(Protocol):
    """Runtime route lookup boundary used by the shared core."""

    def resolve_use_case(
        self, use_case_id: str, use_case_version: str
    ) -> ResolvedUseCase | None:
        """Return active route metadata or ``None`` for an unknown use case."""


class RegistryLookupUnavailable(RuntimeError):
    """The active registry could not be read; no route decision is safe."""


class PostgresRegistryRepository:
    """Read and reconcile registry projections using the provider-neutral contract."""

    def __init__(self, database_url: SecretStr, *, connect_timeout: int = 3) -> None:
        self._database_url = database_url
        self._connect_timeout = connect_timeout

    def reconcile(
        self,
        manifest: RegistryManifest,
        endpoints: Mapping[str, HttpUrl],
        git_revision: str,
        schemas: list[SchemaDefinition] | None = None,
    ) -> None:
        """Atomically project one validated manifest and retire its removed entries."""
        if re.fullmatch(r"[0-9a-f]{7,40}", git_revision) is None:
            raise ValueError("A reviewed Git revision is required for reconciliation.")
        active_tenants = {
            tenant.registration_id
            for tenant in manifest.tenants
            if tenant.lifecycle_state == "active"
        }
        if set(endpoints) != active_tenants:
            raise ValueError("Active tenant endpoint set does not match the manifest.")
        schemas = schemas or []
        if {(item.registry_id, item.schema_version) for item in schemas} != {
            (item.registry_id, item.schema_version) for item in manifest.schemas
        }:
            raise ValueError("Loaded schema definitions do not match the manifest.")

        try:
            with psycopg.connect(
                self._database_url.get_secret_value(),
                connect_timeout=self._connect_timeout,
            ) as connection:
                for schema in schemas:
                    definition = schema.model_dump(mode="json")
                    immutable = connection.execute(
                        "SELECT definition FROM mcp_saas.schema_registry "
                        "WHERE registry_id = %s AND schema_version = %s",
                        (schema.registry_id, schema.schema_version),
                    ).fetchone()
                    if immutable is not None and not _same_schema_meaning(
                        immutable[0], definition
                    ):
                        raise ValueError(
                            "A registered schema version cannot change meaning."
                        )
                    persisted = connection.execute(
                        """INSERT INTO mcp_saas.schema_registry
                           (manifest_id, registry_id, schema_version, lifecycle_state,
                            definition, git_revision)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (registry_id, schema_version) DO UPDATE SET
                             manifest_id = EXCLUDED.manifest_id,
                             lifecycle_state = EXCLUDED.lifecycle_state,
                             git_revision = EXCLUDED.git_revision,
                             updated_at = now()
                           RETURNING definition""",
                        (
                            manifest.manifest_id,
                            schema.registry_id,
                            schema.schema_version,
                            next(
                                item.lifecycle_state
                                for item in manifest.schemas
                                if (item.registry_id, item.schema_version)
                                == (schema.registry_id, schema.schema_version)
                            ),
                            Jsonb(definition),
                            git_revision,
                        ),
                    ).fetchone()
                    if not _same_schema_meaning(persisted[0], definition):
                        raise ValueError(
                            "A registered schema version cannot change meaning."
                        )
                connection.execute(
                    """UPDATE mcp_saas.schema_registry
                       SET lifecycle_state = 'inactive', git_revision = %s,
                           updated_at = now()
                       WHERE manifest_id = %s AND NOT EXISTS (
                           SELECT 1 FROM unnest(%s::text[], %s::text[])
                               AS desired(registry_id, schema_version)
                           WHERE desired.registry_id = schema_registry.registry_id
                             AND desired.schema_version = schema_registry.schema_version
                       )""",
                    (
                        git_revision,
                        manifest.manifest_id,
                        [item.registry_id for item in manifest.schemas],
                        [item.schema_version for item in manifest.schemas],
                    ),
                )
                connection.execute(
                    """UPDATE mcp_saas.pattern_templates
                       SET lifecycle_state = 'inactive', git_revision = %s,
                           updated_at = now()
                       WHERE manifest_id = %s AND NOT EXISTS (
                           SELECT 1 FROM unnest(%s::text[], %s::text[])
                               AS desired(pattern_id, pattern_version)
                           WHERE desired.pattern_id = pattern_templates.pattern_id
                             AND desired.pattern_version =
                                 pattern_templates.pattern_version
                       )""",
                    (
                        git_revision,
                        manifest.manifest_id,
                        [item.pattern_id for item in manifest.patterns],
                        [item.pattern_version for item in manifest.patterns],
                    ),
                )
                connection.execute(
                    """UPDATE mcp_saas.use_case_registry
                       SET lifecycle_state = 'inactive', git_revision = %s,
                           updated_at = now()
                       WHERE manifest_id = %s AND NOT EXISTS (
                           SELECT 1 FROM unnest(%s::text[], %s::text[])
                               AS desired(use_case_id, use_case_version)
                           WHERE desired.use_case_id = use_case_registry.use_case_id
                             AND desired.use_case_version =
                                 use_case_registry.use_case_version
                       )""",
                    (
                        git_revision,
                        manifest.manifest_id,
                        [item.use_case_id for item in manifest.use_cases],
                        [item.use_case_version for item in manifest.use_cases],
                    ),
                )
                connection.execute(
                    "UPDATE mcp_saas.tenant_registry SET lifecycle_state = 'inactive', "
                    "git_revision = %s, updated_at = now() WHERE manifest_id = %s "
                    "AND NOT EXISTS (SELECT 1 FROM unnest(%s::text[], %s::text[], "
                    "%s::text[]) AS desired(tenant_id, pattern_id, pattern_version) "
                    "WHERE desired.tenant_id = tenant_registry.tenant_id "
                    "AND desired.pattern_id = tenant_registry.pattern_id "
                    "AND desired.pattern_version = tenant_registry.pattern_version)",
                    (
                        git_revision,
                        manifest.manifest_id,
                        [item.tenant_id for item in manifest.tenants],
                        [item.pattern_id for item in manifest.tenants],
                        [item.pattern_version for item in manifest.tenants],
                    ),
                )

                for pattern in manifest.patterns:
                    connection.execute(
                        """INSERT INTO mcp_saas.pattern_templates
                           (manifest_id, pattern_id, pattern_version, lifecycle_state,
                            definition, git_revision)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (pattern_id, pattern_version) DO UPDATE SET
                             manifest_id = EXCLUDED.manifest_id,
                             lifecycle_state = EXCLUDED.lifecycle_state,
                             definition = EXCLUDED.definition,
                             git_revision = EXCLUDED.git_revision,
                             updated_at = now()""",
                        (
                            manifest.manifest_id,
                            pattern.pattern_id,
                            pattern.pattern_version,
                            pattern.lifecycle_state,
                            Jsonb(pattern.model_dump(mode="json")),
                            git_revision,
                        ),
                    )

                for tenant in manifest.tenants:
                    if tenant.lifecycle_state != "active":
                        connection.execute(
                            """UPDATE mcp_saas.tenant_registry
                               SET lifecycle_state = %s, git_revision = %s,
                                   updated_at = now()
                               WHERE manifest_id = %s AND tenant_id = %s
                                 AND pattern_id = %s AND pattern_version = %s""",
                            (
                                tenant.lifecycle_state,
                                git_revision,
                                manifest.manifest_id,
                                tenant.tenant_id,
                                tenant.pattern_id,
                                tenant.pattern_version,
                            ),
                        )
                        continue
                    connection.execute(
                        """INSERT INTO mcp_saas.tenant_registry
                           (manifest_id, tenant_id, pattern_id, pattern_version,
                            mcp_endpoint, lifecycle_state, git_revision)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (tenant_id, pattern_id, pattern_version)
                           DO UPDATE SET
                             manifest_id = EXCLUDED.manifest_id,
                             mcp_endpoint = EXCLUDED.mcp_endpoint,
                             lifecycle_state = EXCLUDED.lifecycle_state,
                             git_revision = EXCLUDED.git_revision,
                             updated_at = now()""",
                        (
                            manifest.manifest_id,
                            tenant.tenant_id,
                            tenant.pattern_id,
                            tenant.pattern_version,
                            str(endpoints[tenant.registration_id]),
                            tenant.lifecycle_state,
                            git_revision,
                        ),
                    )

                # Reconciliation may move an existing use-case identity to a
                # new manifest and pattern version. Replace its eligible tenant
                # links by the exact identities in this manifest, regardless
                # of which prior manifest last owned the use-case row.
                for use_case in manifest.use_cases:
                    connection.execute(
                        """DELETE FROM mcp_saas.use_case_tenants
                           WHERE use_case_id = %s AND use_case_version = %s""",
                        (use_case.use_case_id, use_case.use_case_version),
                    )
                for use_case in manifest.use_cases:
                    connection.execute(
                        """INSERT INTO mcp_saas.use_case_registry
                           (manifest_id, use_case_id, use_case_version, pattern_id,
                            pattern_version, operation_id, lifecycle_state,
                            git_revision)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (use_case_id, use_case_version) DO UPDATE SET
                             manifest_id = EXCLUDED.manifest_id,
                             pattern_id = EXCLUDED.pattern_id,
                             pattern_version = EXCLUDED.pattern_version,
                             operation_id = EXCLUDED.operation_id,
                             lifecycle_state = EXCLUDED.lifecycle_state,
                             git_revision = EXCLUDED.git_revision,
                             updated_at = now()""",
                        (
                            manifest.manifest_id,
                            use_case.use_case_id,
                            use_case.use_case_version,
                            use_case.pattern_id,
                            use_case.pattern_version,
                            use_case.operation_id,
                            use_case.lifecycle_state,
                            git_revision,
                        ),
                    )
                    if use_case.lifecycle_state == "active":
                        with connection.cursor() as cursor:
                            cursor.executemany(
                                """INSERT INTO mcp_saas.use_case_tenants
                                   (use_case_id, use_case_version, tenant_id,
                                    pattern_id, pattern_version)
                                   VALUES (%s, %s, %s, %s, %s)""",
                                [
                                    (
                                        use_case.use_case_id,
                                        use_case.use_case_version,
                                        tenant_id,
                                        use_case.pattern_id,
                                        use_case.pattern_version,
                                    )
                                    for tenant_id in use_case.tenant_ids
                                ],
                            )
        except psycopg.Error as exc:
            raise StorageUnavailable("Registry reconciliation failed.") from exc

    def reconcile_schemas_only(
        self,
        manifest: SchemaProjectionManifest,
        schemas: list[SchemaDefinition],
        git_revision: str,
    ) -> None:
        """Activate reviewed schemas without changing patterns or tenants."""
        if re.fullmatch(r"[0-9a-f]{7,40}", git_revision) is None:
            raise ValueError("A reviewed Git revision is required for reconciliation.")
        expected = {
            (item.registry_id, item.schema_version): item.lifecycle_state
            for item in manifest.schemas
        }
        actual = {(item.registry_id, item.schema_version) for item in schemas}
        if actual != set(expected):
            raise ValueError("Loaded schema definitions do not match the manifest.")

        try:
            with psycopg.connect(
                self._database_url.get_secret_value(),
                connect_timeout=self._connect_timeout,
            ) as connection:
                for schema in schemas:
                    identity = (schema.registry_id, schema.schema_version)
                    definition = schema.model_dump(mode="json")
                    immutable = connection.execute(
                        "SELECT definition FROM mcp_saas.schema_registry "
                        "WHERE registry_id = %s AND schema_version = %s",
                        identity,
                    ).fetchone()
                    if immutable is not None and not _same_schema_meaning(
                        immutable[0], definition
                    ):
                        raise ValueError(
                            "A registered schema version cannot change meaning."
                        )
                    persisted = connection.execute(
                        """INSERT INTO mcp_saas.schema_registry
                           (manifest_id, registry_id, schema_version, lifecycle_state,
                            definition, git_revision)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (registry_id, schema_version) DO UPDATE SET
                             manifest_id = EXCLUDED.manifest_id,
                             lifecycle_state = EXCLUDED.lifecycle_state,
                             git_revision = EXCLUDED.git_revision,
                             updated_at = now()
                           RETURNING definition""",
                        (
                            manifest.manifest_id,
                            schema.registry_id,
                            schema.schema_version,
                            expected[identity],
                            Jsonb(definition),
                            git_revision,
                        ),
                    ).fetchone()
                    if not _same_schema_meaning(persisted[0], definition):
                        raise ValueError(
                            "A registered schema version cannot change meaning."
                        )
                connection.execute(
                    """UPDATE mcp_saas.schema_registry
                       SET lifecycle_state = 'inactive', git_revision = %s,
                           updated_at = now()
                       WHERE manifest_id = %s AND NOT EXISTS (
                           SELECT 1 FROM unnest(%s::text[], %s::text[])
                               AS desired(registry_id, schema_version)
                           WHERE desired.registry_id = schema_registry.registry_id
                             AND desired.schema_version = schema_registry.schema_version
                       )""",
                    (
                        git_revision,
                        manifest.manifest_id,
                        [item.registry_id for item in manifest.schemas],
                        [item.schema_version for item in manifest.schemas],
                    ),
                )
        except psycopg.Error as exc:
            raise StorageUnavailable("Schema registry reconciliation failed.") from exc

    def resolve_use_case(
        self, use_case_id: str, use_case_version: str
    ) -> ResolvedUseCase | None:
        """Resolve an active use case and its active compatible tenant endpoints."""
        query = """
            SELECT use_case.use_case_id, use_case.use_case_version,
                   use_case.pattern_id, use_case.pattern_version,
                   use_case.operation_id, use_case.lifecycle_state AS use_case_state,
                   pattern.definition AS pattern_definition,
                   tenant.tenant_id, tenant.pattern_id AS tenant_pattern_id,
                   tenant.pattern_version AS tenant_pattern_version,
                   tenant.mcp_endpoint
            FROM mcp_saas.use_case_registry AS use_case
            JOIN mcp_saas.pattern_templates AS pattern
              ON pattern.pattern_id = use_case.pattern_id
             AND pattern.pattern_version = use_case.pattern_version
            LEFT JOIN mcp_saas.use_case_tenants AS eligible
              ON eligible.use_case_id = use_case.use_case_id
             AND eligible.use_case_version = use_case.use_case_version
            LEFT JOIN mcp_saas.tenant_registry AS tenant
              ON tenant.tenant_id = eligible.tenant_id
             AND tenant.pattern_id = eligible.pattern_id
             AND tenant.pattern_version = eligible.pattern_version
             AND tenant.lifecycle_state = 'active'
            WHERE use_case.use_case_id = %s
              AND use_case.use_case_version = %s
              AND use_case.lifecycle_state = 'active'
              AND pattern.lifecycle_state = 'active'
            ORDER BY tenant.tenant_id
        """
        try:
            with psycopg.connect(
                self._database_url.get_secret_value(),
                connect_timeout=self._connect_timeout,
                row_factory=dict_row,
            ) as connection:
                rows = connection.execute(
                    query, (use_case_id, use_case_version)
                ).fetchall()
        except psycopg.Error as exc:
            raise RegistryLookupUnavailable("Registry lookup failed.") from exc
        if not rows:
            return None

        first = rows[0]
        use_case = UseCaseDefinition(
            use_case_id=first["use_case_id"],
            use_case_version=first["use_case_version"],
            pattern_id=first["pattern_id"],
            pattern_version=first["pattern_version"],
            operation_id=first["operation_id"],
            lifecycle_state=first["use_case_state"],
            tenant_ids=[row["tenant_id"] for row in rows if row["tenant_id"]],
        )
        tenants = [
            RegisteredTenant(
                tenant_id=row["tenant_id"],
                pattern_id=row["tenant_pattern_id"],
                pattern_version=row["tenant_pattern_version"],
                endpoint=row["mcp_endpoint"],
            )
            for row in rows
            if row["tenant_id"]
        ]
        return ResolvedUseCase(
            use_case=use_case,
            pattern=PatternDefinition.model_validate(first["pattern_definition"]),
            tenants=tenants,
        )

    def resolve_schema(
        self, registry_id: str, schema_version: str
    ) -> SchemaDefinition | None:
        """Read one active schema definition from the database authority."""
        try:
            with psycopg.connect(
                self._database_url.get_secret_value(),
                connect_timeout=self._connect_timeout,
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    """SELECT definition FROM mcp_saas.schema_registry
                       WHERE registry_id = %s AND schema_version = %s
                         AND lifecycle_state = 'active'""",
                    (registry_id, schema_version),
                ).fetchone()
        except psycopg.Error as exc:
            raise RegistryLookupUnavailable("Schema registry lookup failed.") from exc
        return SchemaDefinition.model_validate(row["definition"]) if row else None
