"""Resolve allowlisted shared-profile fixtures through a pattern-owned mapping."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from clusters.eligibility_contracts import (
    FarmerProducerProfile,
    GeneralProfile,
    LowIncomeAssistanceProfile,
    Profile,
    SeniorCaregiverProfile,
)
from clusters.eligibility_discovery import CANONICAL_TO_PROFILE_ATTRIBUTE

JsonScalar = str | int | float | bool | None
PROFILE_ADAPTER = TypeAdapter(Profile)
TEMPLATE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TEMPLATE_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MAPPING_ARTIFACT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,127}\.json$")


class ProfileFixtureReference(BaseModel):
    """Opaque reference to one member of a governed fixture set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_set_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    fixture_set_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    fixture_id: str = Field(pattern=r"^fixture-[a-z0-9]{4,32}$")


class FieldProjection(BaseModel):
    """One exact shared-schema attribute to Navigator field mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_attribute_id: str = Field(
        pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$"
    )
    target_field: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source_context: str | dict[str, JsonScalar] | None = None
    source_unit: str | None = Field(
        default=None, pattern=r"^[A-Za-z][A-Za-z0-9._/-]{0,63}$"
    )
    source_time_basis: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9._-]{0,63}$"
    )

    @classmethod
    def _valid_context(cls, value: object) -> bool:
        if isinstance(value, str):
            return re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", value) is not None
        return (
            isinstance(value, dict)
            and 1 <= len(value) <= 16
            and all(
                isinstance(key, str)
                and re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", key) is not None
                and cls._valid_context_value(item)
                for key, item in value.items()
            )
        )

    @staticmethod
    def _valid_context_value(value: object) -> bool:
        if isinstance(value, str):
            return len(value) <= 128
        if isinstance(value, bool) or value is None:
            return True
        if isinstance(value, int):
            return abs(value) <= 10**12
        return (
            isinstance(value, float) and math.isfinite(value) and abs(value) <= 10**12
        )

    @model_validator(mode="before")
    @classmethod
    def validate_source_context(cls, value: object) -> object:
        """Validate the source context before the model is built."""
        if not isinstance(value, Mapping):
            return value
        context = value.get("source_context")
        if context is not None and not cls._valid_context(context):
            raise ValueError("Profile projection context is invalid.")
        return value


class NavigatorProjection(BaseModel):
    """Versioned mapping owned by one Eligibility Navigator contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    navigator: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    target_schema_version: str = Field(min_length=1)
    fields: list[FieldProjection] = Field(max_length=64)

    @model_validator(mode="after")
    def general_projection_keeps_registered_attributes(self) -> NavigatorProjection:
        """Only the general navigator may keep attributes without mappings."""
        if (self.navigator == "general") != (not self.fields):
            raise ValueError(
                "Only general may preserve attributes without field mappings."
            )
        return self


class FixtureSetProjection(BaseModel):
    """One fixture collection explicitly authorized for a template version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_set_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    fixture_set_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


class ProfileProjectionMapping(BaseModel):
    """Template-owned immutable projection for one source schema version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = Field(pattern=r"^profile-projection-v[1-9][0-9]*$")
    mapping_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    template_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    template_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    source_schema_registry_id: str = Field(min_length=1)
    source_schema_version: str = Field(min_length=1)
    allowed_fixture_sets: list[FixtureSetProjection] = Field(
        min_length=1, max_length=16
    )
    navigators: list[NavigatorProjection] = Field(min_length=1, max_length=8)

    @classmethod
    def load(
        cls, path: Path, *, template_id: str, template_version: str
    ) -> ProfileProjectionMapping:
        """Load and bind one mapping artifact to the exact active template version."""
        mapping = cls.model_validate_json(path.read_text(encoding="utf-8"))
        if (mapping.template_id, mapping.template_version) != (
            template_id,
            template_version,
        ):
            raise ValueError("Profile mapping does not match the active template.")
        navigator_ids = [item.navigator for item in mapping.navigators]
        if len(navigator_ids) != len(set(navigator_ids)):
            raise ValueError("Profile mapping contains duplicate Navigators.")
        fixture_set_ids = [
            (item.fixture_set_id, item.fixture_set_version)
            for item in mapping.allowed_fixture_sets
        ]
        if len(fixture_set_ids) != len(set(fixture_set_ids)):
            raise ValueError("Profile mapping contains duplicate fixture sets.")
        for navigator in mapping.navigators:
            targets = [item.target_field for item in navigator.fields]
            sources = [item.source_attribute_id for item in navigator.fields]
            if len(targets) != len(set(targets)) or len(sources) != len(set(sources)):
                raise ValueError("Profile mapping fields must be one-to-one.")
        return mapping

    def for_navigator(self, navigator: str) -> NavigatorProjection | None:
        """Return the projection for a navigator, or None if there is none."""
        return next(
            (item for item in self.navigators if item.navigator == navigator), None
        )

    def allows_fixture_set(self, reference: ProfileFixtureReference) -> bool:
        """Check fixture-set scope before the tenant makes any API request."""
        return any(
            item.fixture_set_id == reference.fixture_set_id
            and item.fixture_set_version == reference.fixture_set_version
            for item in self.allowed_fixture_sets
        )


def resolve_templates_directory(
    *,
    working_directory: Path | None = None,
    module_path: Path | None = None,
) -> Path:
    """Find runtime templates from the configured app root or source checkout.

    Production installs this module into ``site-packages`` while Docker copies
    configuration into the app root. Prefer the app working directory, with a
    source-tree fallback for local execution.
    """
    configured = os.environ.get("USDA_MCP_TEMPLATES_DIRECTORY")
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = (working_directory or Path.cwd()) / candidate
        if candidate.is_dir():
            return candidate.resolve()
        raise ValueError("Configured Eligibility templates directory is missing.")

    app_root = working_directory or Path.cwd()
    source_module = module_path or Path(__file__)
    candidates = (
        app_root / "configs" / "templates",
        source_module.resolve().parents[2] / "configs" / "templates",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise ValueError("Eligibility templates directory is missing.")


def load_declared_profile_projection_mapping(
    template_id: str, template_version: str, templates_directory: Path
) -> ProfileProjectionMapping | None:
    """Load only the mapping artifact and version declared by a template file."""
    if not TEMPLATE_ID_PATTERN.fullmatch(
        template_id
    ) or not TEMPLATE_VERSION_PATTERN.fullmatch(template_version):
        raise ValueError("Template identity is invalid.")
    definitions: list[dict[str, object]] = []
    for template_path in templates_directory.glob(f"{template_id}.v*.json"):
        try:
            definition = json.loads(template_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Template definition is invalid.") from exc
        if not isinstance(definition, dict):
            raise ValueError("Template definition is invalid.")
        if (
            definition.get("template_id") == template_id
            and definition.get("template_version") == template_version
        ):
            definitions.append(definition)
    if not definitions:
        return None
    if len(definitions) != 1:
        raise ValueError("Template definition is ambiguous.")
    definition = definitions[0]
    declaration = definition.get("profile_projection_mapping")
    if declaration is None:
        return None
    if (
        not isinstance(declaration, dict)
        or set(declaration) != {"artifact_ref", "mapping_version"}
        or not isinstance(declaration.get("artifact_ref"), str)
        or not MAPPING_ARTIFACT_PATTERN.fullmatch(declaration["artifact_ref"])
        or not isinstance(declaration.get("mapping_version"), str)
    ):
        raise ValueError("Template profile projection declaration is invalid.")
    # The declaring template is the binding: one immutable mapping artifact may
    # serve several template versions that declare the same mapping_version.
    path = templates_directory / declaration["artifact_ref"]
    try:
        raw = ProfileProjectionMapping.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError("Template profile projection artifact is invalid.") from exc
    if raw.template_id != template_id:
        raise ValueError("Profile mapping does not match the active template.")
    mapping = ProfileProjectionMapping.load(
        path, template_id=raw.template_id, template_version=raw.template_version
    ).model_copy(update={"template_version": template_version})
    if mapping.mapping_version != declaration["mapping_version"]:
        raise ValueError("Template profile projection version does not match.")
    return mapping


class ProfileFixtureReader(Protocol):
    """Read only allowlisted fixture references, never arbitrary profile IDs."""

    def read_fixture(self, reference: ProfileFixtureReference) -> dict[str, object]:
        """Read the profile fixture a reference points to."""


class ProfileFixtureUnavailable(RuntimeError):
    """The authenticated fixture endpoint did not provide a valid fixture."""


class HttpProfileFixtureReader:
    """Read a fixture through the dedicated least-privilege API capability."""

    def __init__(
        self,
        api_url: str,
        token_file: Path,
        *,
        timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(api_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Profile API URL must be an API origin.")
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Fixture reader capability is unavailable.") from exc
        if not token:
            raise ValueError("Fixture reader capability is empty.")
        self._base_url = api_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def read_fixture(self, reference: ProfileFixtureReference) -> dict[str, object]:
        """Fetch one allowlisted fixture without exposing storage identifiers."""
        path = (
            "/v1/evaluation/fixture-sets/"
            f"{quote(reference.fixture_set_id, safe='')}/"
            f"{quote(reference.fixture_set_version, safe='')}/fixtures/"
            f"{quote(reference.fixture_id, safe='')}"
        )
        try:
            with httpx.Client(
                headers=self._headers,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.get(self._base_url + path)
            if response.status_code in {401, 403, 404}:
                payload = response.json()
                return (
                    payload if isinstance(payload, dict) else {"status": "unavailable"}
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Fixture response is invalid.")
            if (
                payload.get("status") != "available"
                or payload.get("fixture_set_id") != reference.fixture_set_id
                or payload.get("fixture_set_version") != reference.fixture_set_version
                or payload.get("fixture_id") != reference.fixture_id
                or not isinstance(payload.get("profile"), dict)
            ):
                raise ValueError("Fixture response identity is invalid.")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise ProfileFixtureUnavailable from exc


class ProjectionResult(BaseModel):
    """Typed profile projection plus safe reference and mapping lineage."""

    model_config = ConfigDict(extra="forbid")

    status: str
    profile_payload: dict[str, object] | None = None
    profile_reference: dict[str, str] | None = None
    projection_lineage: dict[str, str] | None = None
    unresolved_fields: list[str] = Field(default_factory=list)
    field_provenance: list[dict[str, object]] = Field(default_factory=list)


def project_profile_fixture(
    fixture: Mapping[str, object],
    reference: ProfileFixtureReference,
    mapping: ProfileProjectionMapping,
    *,
    tenant_id: str,
    template_id: str,
    template_version: str,
) -> ProjectionResult:
    """Project declared, exact fields and retain missing data as unresolved."""
    profile = fixture.get("profile")
    fixture_tenant_id = fixture.get("tenant_id")
    if (
        fixture.get("status") != "available"
        or fixture.get("fixture_set_id") != reference.fixture_set_id
        or fixture.get("fixture_set_version") != reference.fixture_set_version
        or fixture.get("fixture_id") != reference.fixture_id
        or not any(
            item.fixture_set_id == reference.fixture_set_id
            and item.fixture_set_version == reference.fixture_set_version
            for item in mapping.allowed_fixture_sets
        )
        or fixture_tenant_id not in (None, tenant_id)
        or (mapping.template_id, mapping.template_version)
        != (template_id, template_version)
        or not isinstance(profile, Mapping)
        or profile.get("schema_registry_id") != mapping.source_schema_registry_id
        or profile.get("schema_version") != mapping.source_schema_version
        or not isinstance(profile.get("attributes"), list)
    ):
        return ProjectionResult(status="profile_scope_rejected")

    use_case_id = fixture.get("use_case_id")
    navigator = mapping.for_navigator(str(use_case_id))
    if navigator is None:
        # The fixture's need/use-case metadata is not a tenant authorization
        # boundary. This projection simply has no evaluator contract for it.
        return ProjectionResult(status="profile_projection_unmapped")

    profile_id = _opaque_profile_id(reference)
    projected = _project_attribute_records(
        profile["attributes"], mapping, navigator, profile_id
    )
    if projected.status != "available":
        return projected
    normalized_payload = projected.profile_payload
    unresolved = projected.unresolved_fields
    provenance = projected.field_provenance

    # Return only the declared projection, never the original shared profile.
    return ProjectionResult(
        status="available",
        profile_payload=normalized_payload,
        profile_reference=reference.model_dump(),
        projection_lineage={
            "tenant_id": tenant_id,
            "template_id": template_id,
            "template_version": template_version,
            "mapping_version": mapping.mapping_version,
            "source_schema_registry_id": mapping.source_schema_registry_id,
            "source_schema_version": mapping.source_schema_version,
            "target_schema_version": navigator.target_schema_version,
        },
        unresolved_fields=unresolved,
        field_provenance=provenance,
    )


def project_shared_profile(
    shared_profile: Mapping[str, object],
    mapping: ProfileProjectionMapping,
    *,
    navigator: str | None,
    tenant_id: str,
    template_id: str,
    template_version: str,
) -> ProjectionResult:
    """Project a Core-resolved session profile for one Navigator.

    Core reads the caller's session-scoped profile and forwards its typed
    attributes; the tenant applies the template-declared mapping. Declared core
    fields map to Navigator fields. Attributes outside the ``core`` namespace
    (registered rule extensions such as ``nrcs.eqip.*``) pass through as
    Navigator ``attributes`` so source-backed rules can read their exact keys.
    """
    if (
        shared_profile.get("status", "available") != "available"
        or mapping.template_id != template_id
        or shared_profile.get("schema_registry_id") != mapping.source_schema_registry_id
        or not _compatible_core_schema_version(
            mapping.source_schema_version, shared_profile.get("schema_version")
        )
        or not isinstance(shared_profile.get("attributes"), list)
    ):
        return ProjectionResult(status="profile_scope_rejected")
    navigator_projection = mapping.for_navigator(navigator or "general")
    if navigator_projection is None:
        return ProjectionResult(status="profile_projection_unmapped")
    profile_ref_sha256 = shared_profile.get("profile_ref_sha256")
    profile_id = synthetic_profile_id_from_reference_hash(profile_ref_sha256)
    if profile_id is None:
        return ProjectionResult(status="profile_scope_rejected")
    revision = shared_profile.get("revision")
    if navigator_projection.navigator == "general":
        projected = _project_general_profile(
            shared_profile["attributes"],
            profile_id=profile_id,
            schema_version=str(shared_profile["schema_version"]),
            root_schema_registry_id=mapping.source_schema_registry_id,
            accepted_schema_versions=frozenset(
                {mapping.source_schema_version, str(shared_profile["schema_version"])}
            ),
        )
        if projected.status != "available" or projected.profile_payload is None:
            return projected
        return ProjectionResult(
            status="available",
            profile_payload=projected.profile_payload,
            profile_reference={
                "profile_ref_sha256": profile_ref_sha256,
                "revision": str(revision) if revision is not None else "",
            },
            projection_lineage={
                "tenant_id": tenant_id,
                "template_id": template_id,
                "template_version": template_version,
                "mapping_version": mapping.mapping_version,
                "source_schema_registry_id": mapping.source_schema_registry_id,
                "source_schema_version": mapping.source_schema_version,
                "target_schema_version": str(shared_profile["schema_version"]),
            },
            unresolved_fields=projected.unresolved_fields,
            field_provenance=projected.field_provenance,
        )
    declared = {item.source_attribute_id for item in navigator_projection.fields}
    extension_values: dict[str, object] = {}
    navigator_model = _NAVIGATOR_PROFILE_MODELS.get(navigator_projection.navigator)
    for item in shared_profile["attributes"]:
        if not isinstance(item, Mapping):
            return ProjectionResult(status="profile_projection_invalid")
        attribute_id = item.get("attribute_id")
        value = item.get("value")
        canonical_target = (
            CANONICAL_TO_PROFILE_ATTRIBUTE.get(attribute_id)
            if isinstance(attribute_id, str)
            else None
        )
        is_profile_value = isinstance(value, (str, int, float, bool)) or (
            isinstance(value, list)
            and all(
                member is None or isinstance(member, (str, int, float, bool))
                for member in value
            )
        )
        if (
            isinstance(attribute_id, str)
            and attribute_id not in declared
            and "value" in item
            and _profile_field_schema_is_accepted(
                item,
                mapping.source_schema_registry_id,
                frozenset(
                    {
                        mapping.source_schema_version,
                        str(shared_profile["schema_version"]),
                    }
                ),
            )
            and is_profile_value
            and not (
                canonical_target
                and navigator_model
                and canonical_target in navigator_model.model_fields
            )
        ):
            extension_values[attribute_id] = value
    canonical_values = _canonical_navigator_values(
        shared_profile["attributes"], navigator_projection
    )
    projected = _project_attribute_records(
        shared_profile["attributes"],
        mapping,
        navigator_projection,
        profile_id,
        extension_values,
        accepted_schema_versions=frozenset(
            {mapping.source_schema_version, str(shared_profile["schema_version"])}
        ),
        canonical_values=canonical_values,
    )
    if projected.status != "available":
        return projected
    return ProjectionResult(
        status="available",
        profile_payload=projected.profile_payload,
        profile_reference={
            "profile_ref_sha256": profile_ref_sha256,
            "revision": str(revision) if revision is not None else "",
        },
        projection_lineage={
            "tenant_id": tenant_id,
            "template_id": template_id,
            "template_version": template_version,
            "mapping_version": mapping.mapping_version,
            "source_schema_registry_id": mapping.source_schema_registry_id,
            "source_schema_version": mapping.source_schema_version,
            "target_schema_version": navigator_projection.target_schema_version,
        },
        unresolved_fields=projected.unresolved_fields,
        field_provenance=projected.field_provenance,
    )


def _project_general_profile(
    attribute_records: object,
    *,
    profile_id: str,
    schema_version: str,
    root_schema_registry_id: str,
    accepted_schema_versions: frozenset[str],
) -> ProjectionResult:
    """Preserve typed registered attributes without imposing a Navigator schema."""
    if not isinstance(attribute_records, list):
        return ProjectionResult(status="profile_projection_invalid")
    attributes: dict[str, object] = {}
    unresolved: list[str] = []
    provenance: list[dict[str, object]] = []
    for item in attribute_records:
        if not isinstance(item, Mapping) or not isinstance(
            item.get("attribute_id"), str
        ):
            return ProjectionResult(status="profile_projection_invalid")
        attribute_id = item["attribute_id"]
        if attribute_id in attributes or attribute_id in unresolved:
            return ProjectionResult(status="profile_projection_ambiguous")
        if (
            not _profile_field_schema_is_accepted(
                item, root_schema_registry_id, accepted_schema_versions
            )
            or "value" not in item
        ):
            return ProjectionResult(status="profile_projection_incompatible")
        freshness = item.get("freshness_status")
        if freshness not in (None, "current", "stale"):
            return ProjectionResult(status="profile_projection_incompatible")
        provenance.append(
            {
                "target_field": attribute_id,
                "source_attribute_id": attribute_id,
                "namespace": attribute_id.split(".", 1)[0],
                "source_schema_registry_id": _field_schema_registry_id(
                    item, root_schema_registry_id
                ),
                "source_schema_version": item.get("schema_version"),
                "freshness_status": freshness or "not_assessed",
            }
        )
        if freshness == "stale":
            unresolved.append(attribute_id)
            continue
        attributes[attribute_id] = item["value"]
    try:
        profile = GeneralProfile.model_validate(
            {
                "profile_id": profile_id,
                "navigator": "general",
                "schema_version": schema_version,
                "attributes": attributes,
            },
            strict=True,
        )
    except (ValidationError, TypeError, ValueError):
        return ProjectionResult(status="profile_projection_incompatible")
    return ProjectionResult(
        status="available",
        profile_payload=profile.model_dump(mode="json", exclude_none=True),
        unresolved_fields=unresolved,
        field_provenance=provenance,
    )


def synthetic_profile_id_from_reference_hash(value: object) -> str | None:
    """Return the stable tenant-local profile ID for a Core reference digest."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        return None
    return f"synthetic-profile-{value[:20]}"


_NAVIGATOR_PROFILE_MODELS: dict[str, type[BaseModel]] = {
    "farmer_producer": FarmerProducerProfile,
    "senior_caregiver": SeniorCaregiverProfile,
    "low_income_assistance": LowIncomeAssistanceProfile,
    "general": GeneralProfile,
}
_CORE_SCHEMA_VERSION = re.compile(r"^core-v([1-9][0-9]*)$")


def _compatible_core_schema_version(mapping_version: str, candidate: object) -> bool:
    """Accept the mapping's core version or a later additive core version.

    Core schema versions are additive (core-v8 keeps every core-v7 attribute
    ID and type), so a profile written against the active later version still
    satisfies a mapping declared for an earlier one.
    """
    if not isinstance(candidate, str):
        return False
    if candidate == mapping_version:
        return True
    base = _CORE_SCHEMA_VERSION.fullmatch(mapping_version)
    newer = _CORE_SCHEMA_VERSION.fullmatch(candidate)
    return bool(base and newer and int(newer.group(1)) >= int(base.group(1)))


def _field_schema_registry_id(
    item: Mapping[str, object], root_schema_registry_id: str
) -> str:
    """Return the persisted schema identity for a profile field."""
    provenance = item.get("provenance")
    registry_id = (
        provenance.get("schema_registry_id")
        if isinstance(provenance, Mapping)
        else None
    )
    return registry_id if isinstance(registry_id, str) else root_schema_registry_id


def _profile_field_schema_is_accepted(
    item: Mapping[str, object],
    root_schema_registry_id: str,
    accepted_core_versions: frozenset[str],
) -> bool:
    """Trust core fields by version and extensions by persisted registry identity."""
    registry_id = _field_schema_registry_id(item, root_schema_registry_id)
    schema_version = item.get("schema_version")
    if registry_id == root_schema_registry_id:
        return schema_version in accepted_core_versions
    provenance = item.get("provenance")
    return (
        isinstance(provenance, Mapping)
        and provenance.get("schema_registry_id") == registry_id
        and isinstance(registry_id, str)
        and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", registry_id) is not None
        and isinstance(schema_version, str)
        and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", schema_version) is not None
    )


def _canonical_navigator_values(
    attribute_records: list[object], navigator: NavigatorProjection
) -> dict[str, object]:
    """Fill undeclared Navigator fields from registered canonical core IDs.

    The template mapping stays authoritative for the fields it declares. Other
    core attributes with a registered canonical Navigator field (for example
    ``core.household.food_assistance_need``) are projected when the Navigator
    profile defines that field and the value validates for its type.
    """
    model = _NAVIGATOR_PROFILE_MODELS.get(navigator.navigator)
    if model is None:
        return {}
    declared_targets = {item.target_field for item in navigator.fields}
    values: dict[str, object] = {}
    for item in attribute_records:
        if not isinstance(item, Mapping) or "value" not in item:
            continue
        target = CANONICAL_TO_PROFILE_ATTRIBUTE.get(str(item.get("attribute_id")))
        field = model.model_fields.get(target or "")
        if target is None or field is None or target in declared_targets:
            continue
        try:
            values[target] = TypeAdapter(field.annotation).validate_python(
                item["value"]
            )
        except (ValidationError, TypeError, ValueError):
            continue
    return values


def _project_attribute_records(
    attribute_records: object,
    mapping: ProfileProjectionMapping,
    navigator: NavigatorProjection,
    profile_id: str,
    extension_values: Mapping[str, object] | None = None,
    *,
    accepted_schema_versions: frozenset[str] | None = None,
    canonical_values: Mapping[str, object] | None = None,
) -> ProjectionResult:
    """Project declared shared-schema attributes into one Navigator payload."""
    accepted = accepted_schema_versions or frozenset({mapping.source_schema_version})
    if not isinstance(attribute_records, list):
        return ProjectionResult(status="profile_projection_invalid")
    by_id: dict[str, Mapping[str, object]] = {}
    for item in attribute_records:
        if not isinstance(item, Mapping) or not isinstance(
            item.get("attribute_id"), str
        ):
            return ProjectionResult(status="profile_projection_invalid")
        if item["attribute_id"] in by_id:
            return ProjectionResult(status="profile_projection_ambiguous")
        by_id[item["attribute_id"]] = item

    payload: dict[str, object] = {
        "profile_id": profile_id,
        "navigator": navigator.navigator,
        "schema_version": navigator.target_schema_version,
    }
    unresolved: list[str] = []
    provenance: list[dict[str, object]] = []
    for field_mapping in navigator.fields:
        item = by_id.get(field_mapping.source_attribute_id)
        if item is None:
            unresolved.append(field_mapping.target_field)
            continue
        if (
            not _profile_field_schema_is_accepted(
                item, mapping.source_schema_registry_id, accepted
            )
            or "value" not in item
        ):
            return ProjectionResult(status="profile_projection_incompatible")
        source_metadata = item.get("provenance")
        source_metadata = (
            source_metadata if isinstance(source_metadata, Mapping) else {}
        )
        context = item.get("context", source_metadata.get("context"))
        if context is not None and not FieldProjection._valid_context(context):
            return ProjectionResult(status="profile_projection_incompatible")
        for key in ("context", "unit", "time_basis"):
            expected = getattr(field_mapping, f"source_{key}")
            actual = item.get(key, source_metadata.get(key))
            if expected is not None and actual != expected:
                return ProjectionResult(status="profile_projection_incompatible")
        freshness_status = item.get(
            "freshness_status", source_metadata.get("freshness_status")
        )
        if freshness_status not in (None, "current", "stale"):
            return ProjectionResult(status="profile_projection_incompatible")
        freshness_status = freshness_status or "not_assessed"
        safe_provenance = {
            key: value
            for key in (
                "origin",
                "confirmation_status",
                "method_id",
                "method_version",
                "method_type",
                "input_unit",
                "output_unit",
            )
            if isinstance((value := source_metadata.get(key)), str)
        }
        for key in ("assumptions", "input_attribute_ids"):
            value = source_metadata.get(key)
            if (
                isinstance(value, list)
                and len(value) <= 16
                and all(isinstance(item, str) and len(item) <= 256 for item in value)
            ):
                safe_provenance[key] = value
        factor = source_metadata.get("factor")
        if (
            isinstance(factor, int)
            and not isinstance(factor, bool)
            and abs(factor) <= 10**12
        ) or (
            isinstance(factor, float)
            and math.isfinite(factor)
            and abs(factor) <= 10**12
        ):
            safe_provenance["factor"] = factor
        uncertainty = source_metadata.get("uncertainty")
        if isinstance(uncertainty, str) and len(uncertainty) <= 512:
            safe_provenance["uncertainty"] = uncertainty
        provenance.append(
            {
                "target_field": field_mapping.target_field,
                "source_attribute_id": field_mapping.source_attribute_id,
                "namespace": field_mapping.source_attribute_id.split(".", 1)[0],
                "source_schema_registry_id": _field_schema_registry_id(
                    item, mapping.source_schema_registry_id
                ),
                "source_id": item.get("source_id"),
                "dimension_id": item.get("dimension_id"),
                "value_type": item.get("value_type"),
                "source_schema_version": item.get("schema_version"),
                "observed_at": item.get("observed_at"),
                "freshness_status": freshness_status,
                "field_provenance": safe_provenance,
                **{
                    key: value
                    for key in ("context", "unit", "time_basis")
                    if (
                        (
                            value := context
                            if key == "context"
                            else item.get(key, source_metadata.get(key))
                        )
                        is not None
                        and (key == "context" or isinstance(value, str))
                    )
                },
            }
        )
        if freshness_status == "stale":
            unresolved.append(field_mapping.target_field)
            continue
        payload[field_mapping.target_field] = item["value"]

    for target_field, value in (canonical_values or {}).items():
        payload.setdefault(target_field, value)
    for attribute_id, value in sorted((extension_values or {}).items()):
        item = by_id.get(attribute_id)
        if item is None:
            return ProjectionResult(status="profile_projection_incompatible")
        freshness = item.get("freshness_status")
        if freshness not in (None, "current", "stale"):
            return ProjectionResult(status="profile_projection_incompatible")
        provenance.append(
            {
                "target_field": attribute_id,
                "source_attribute_id": attribute_id,
                "namespace": attribute_id.split(".", 1)[0],
                "source_schema_registry_id": _field_schema_registry_id(
                    item, mapping.source_schema_registry_id
                ),
                "source_schema_version": item.get("schema_version"),
                "freshness_status": freshness or "not_assessed",
            }
        )
        if freshness == "stale":
            unresolved.append(attribute_id)
            continue
        extension_attributes = payload.setdefault("attributes", {})
        if isinstance(extension_attributes, dict):
            extension_attributes[attribute_id] = value
    try:
        normalized = PROFILE_ADAPTER.validate_python(payload, strict=True)
    except (ValidationError, TypeError, ValueError):
        return ProjectionResult(status="profile_projection_incompatible")
    return ProjectionResult(
        status="available",
        profile_payload=normalized.model_dump(mode="json", exclude_none=True),
        unresolved_fields=unresolved,
        field_provenance=provenance,
    )


def _opaque_profile_id(reference: ProfileFixtureReference) -> str:
    """Derive a stable synthetic ID without disclosing the stored profile key."""
    identity = ":".join(
        (reference.fixture_set_id, reference.fixture_set_version, reference.fixture_id)
    )
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"synthetic-fixture-{suffix}"
