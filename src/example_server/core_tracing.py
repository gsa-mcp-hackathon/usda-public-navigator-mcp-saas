"""Privacy-safe, optional tracing for inbound Core MCP tool calls."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastmcp.server.middleware import Middleware
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)
_PHOENIX_PROJECT = "usda-core-native"
_SERVICE_NAME = "mcp-core"
_MAX_EXPORT_DELAY_MS = 500
_EXPORT_TIMEOUT_SECONDS = 2
_CLIENT_IDENTITY_STATE_KEY = "telemetry.client_identity"
_ROUTE_SPAN_STATE_KEY = "telemetry.route_span"
_CLIENT_LABEL = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_CLIENT_LABEL_LENGTH = 64
_ROUTE_ID = re.compile(r"^[a-z][a-z0-9.-]{0,63}$")
_OPERATION_ID = re.compile(r"^[a-z][a-z0-9.-]{0,127}$")
_ROUTE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")
_ROUTE_OUTCOMES = frozenset(
    {
        "ok",
        "unknown_route",
        "incompatible",
        "invalid_request",
        "downstream_error",
        "unavailable",
    }
)
_ROUTE_ERROR_CLASSES = frozenset(
    {
        "unknown_route",
        "incompatible_route",
        "tenant_argument_validation",
        "tenant_tool_error",
        "registry_unavailable",
        "tenant_unavailable",
    }
)


class CoreMcpTracingMiddleware(Middleware):
    """Trace MCP tool calls without reading their arguments or results."""

    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer

    async def on_initialize(self, context: Any, call_next: Any) -> Any:
        """Retain bounded, client-declared MCP identity in session state."""
        result = await call_next(context)
        fastmcp_context = getattr(context, "fastmcp_context", None)
        client_info = getattr(
            getattr(getattr(context.message, "params", None), "client_info", None),
            "name",
            None,
        )
        client_name = _safe_client_label(client_info)
        client_version = _safe_client_label(
            getattr(
                getattr(getattr(context.message, "params", None), "client_info", None),
                "version",
                None,
            )
        )
        if fastmcp_context is not None and client_name is not None:
            try:
                await fastmcp_context.set_state(
                    _CLIENT_IDENTITY_STATE_KEY,
                    {
                        "name": client_name,
                        "version": client_version,
                        "provenance": "mcp.initialize.client_info; client-declared",
                    },
                )
            except Exception:
                logger.debug("MCP client identity unavailable for trace attribution.")
        return result

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        """Record safe identity, duration, and outcome metadata only."""
        tool_name = context.message.name
        source = context.source if context.source in {"client", "server"} else "unknown"
        request_id = str(uuid.uuid4())
        span = self._tracer.start_span("mcp.tool.call")
        span.set_attribute("mcp.tool.name", tool_name)
        span.set_attribute("mcp.request.id", request_id)
        span.set_attribute("mcp.request.source", source)
        span.set_attribute("mcp.provider.usage.status", "unavailable")
        client_identity = await _read_client_identity(context)
        span.set_attribute(
            "mcp.client.identity.status",
            "client_declared" if client_identity else "unavailable",
        )
        if client_identity:
            span.set_attribute(
                "mcp.client.identity.provenance", client_identity["provenance"]
            )
            span.set_attribute("mcp.client.name", client_identity["name"])
            if client_identity["version"] is not None:
                span.set_attribute("mcp.client.version", client_identity["version"])

        # FastMCP may invoke the tool handler through a task boundary where the
        # OpenTelemetry current-span context is not inherited. Keep this span in
        # request-scoped state so the routed handler can enrich the exact call.
        fastmcp_context = getattr(context, "fastmcp_context", None)
        if fastmcp_context is not None:
            try:
                session_id = getattr(fastmcp_context, "session_id", None)
                if isinstance(session_id, str) and 0 < len(session_id) <= 256:
                    digest = hashlib.sha256(
                        f"mcp-trace-session-v1\0{session_id}".encode("utf-8")
                    ).hexdigest()
                    span.set_attribute("mcp.session.sha256", digest[:16])
            except Exception:
                logger.debug("MCP session unavailable for trace attribution.")
            try:
                await fastmcp_context.set_state(
                    _ROUTE_SPAN_STATE_KEY, span, serializable=False
                )
            except Exception:
                logger.debug("Core route span unavailable for request attribution.")

        try:
            with trace.use_span(
                span,
                end_on_exit=False,
                record_exception=False,
                set_status_on_exception=False,
            ):
                result = await call_next(context)
            is_error = getattr(result, "isError", False) is True
            span.set_attribute("mcp.result.status", "error" if is_error else "ok")
            if is_error:
                span.set_status(trace.Status(trace.StatusCode.ERROR))
            return result
        except Exception as exc:
            span.set_attribute("mcp.result.status", "error")
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            raise
        finally:
            span.end()


def record_core_route_context(
    *,
    span: trace.Span | None = None,
    correlation_id: str,
    outcome: str,
    duration_ms: float,
    error_class: str | None = None,
    route_id: str | None = None,
    route_version: str | None = None,
    operation_id: str | None = None,
    template_id: str | None = None,
) -> None:
    """Attach allowlisted registry metadata to the active route-operation span."""
    span = span or trace.get_current_span()
    if not span.is_recording():
        return

    try:
        safe_correlation_id = str(uuid.UUID(correlation_id))
    except (AttributeError, TypeError, ValueError):
        safe_correlation_id = None
    if safe_correlation_id is not None:
        span.set_attribute("mcp.correlation.id", safe_correlation_id)

    if outcome in _ROUTE_OUTCOMES:
        span.set_attribute("mcp.route.outcome", outcome)
    if error_class in _ROUTE_ERROR_CLASSES:
        span.set_attribute("mcp.route.error_class", error_class)
    if isinstance(duration_ms, (int, float)) and 0 <= duration_ms <= 600_000:
        span.set_attribute("mcp.route.duration_ms", float(duration_ms))

    if isinstance(route_id, str) and _ROUTE_ID.fullmatch(route_id):
        span.set_attribute("mcp.route.id", route_id)
    if isinstance(route_version, str) and _ROUTE_VERSION.fullmatch(route_version):
        span.set_attribute("mcp.route.version", route_version)
    if isinstance(operation_id, str) and _OPERATION_ID.fullmatch(operation_id):
        span.set_attribute("mcp.operation.id", operation_id)
    if isinstance(template_id, str) and _ROUTE_ID.fullmatch(template_id):
        span.set_attribute("mcp.template.id", template_id)


def record_provider_usage(
    span: trace.Span | None,
    *,
    provider_id: object,
    status: str,
    units: int | None = None,
) -> None:
    """Record a known provider and bounded search-request usage."""
    span = span or trace.get_current_span()
    if not span.is_recording():
        return
    if provider_id != "brave_web_search_api":
        return
    span.set_attribute("mcp.provider.id", provider_id)
    if status == "measured" and type(units) is int and 0 <= units <= 1000:
        span.set_attribute("mcp.provider.usage.status", "measured")
        span.set_attribute("mcp.provider.usage.units", units)
        span.set_attribute("mcp.provider.usage.unit", "search_request")
    else:
        span.set_attribute("mcp.provider.usage.status", "unavailable")


async def get_request_route_span(context: Any) -> trace.Span | None:
    """Return the tool-call span explicitly carried in request-scoped state."""
    fastmcp_context = getattr(context, "fastmcp_context", context)
    get_state = getattr(fastmcp_context, "get_state", None)
    if get_state is None:
        return None
    try:
        span = await get_state(_ROUTE_SPAN_STATE_KEY)
    except Exception:
        return None
    return span if callable(getattr(span, "set_attribute", None)) else None


def _safe_client_label(value: object) -> str | None:
    """Bound MCP software labels and discard control and unsupported characters."""
    if not isinstance(value, str):
        return None
    normalized = _CLIENT_LABEL.sub("-", value.strip())[:_MAX_CLIENT_LABEL_LENGTH]
    normalized = normalized.strip("-._")
    return normalized or None


async def _read_client_identity(context: Any) -> dict[str, str | None] | None:
    """Read initialize metadata from session state without failing tool calls."""
    fastmcp_context = getattr(context, "fastmcp_context", None)
    if fastmcp_context is None:
        return None
    try:
        identity = await fastmcp_context.get_state(_CLIENT_IDENTITY_STATE_KEY)
    except Exception:
        return None
    if not isinstance(identity, dict):
        return None
    name = _safe_client_label(identity.get("name"))
    provenance = identity.get("provenance")
    if name is None or provenance != "mcp.initialize.client_info; client-declared":
        return None
    return {
        "name": name,
        "version": _safe_client_label(identity.get("version")),
        "provenance": provenance,
    }


def _valid_phoenix_endpoint(endpoint: str) -> bool:
    """Accept only an HTTPS Phoenix OTLP/HTTP traces endpoint."""
    try:
        parsed = urlsplit(endpoint)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.path.rstrip("/") == "/v1/traces"
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def create_phoenix_middleware(
    endpoint: str | None,
    *,
    exporter: OTLPSpanExporter | None = None,
) -> CoreMcpTracingMiddleware | None:
    """Configure bounded OTLP export, returning no middleware on setup failure."""
    if not endpoint:
        return None
    if not _valid_phoenix_endpoint(endpoint):
        logger.warning("Core Phoenix tracing disabled: endpoint configuration invalid.")
        return None

    try:
        span_exporter = exporter or OTLPSpanExporter(
            endpoint=endpoint,
            timeout=_EXPORT_TIMEOUT_SECONDS,
        )
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": _SERVICE_NAME,
                    # Phoenix routes spans to a project by this attribute;
                    # without it they land in the shared "default" project.
                    "openinference.project.name": _PHOENIX_PROJECT,
                }
            )
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                span_exporter,
                schedule_delay_millis=_MAX_EXPORT_DELAY_MS,
                max_queue_size=128,
                max_export_batch_size=32,
                export_timeout_millis=_EXPORT_TIMEOUT_SECONDS * 1000,
            )
        )
        return CoreMcpTracingMiddleware(provider.get_tracer(_SERVICE_NAME))
    except Exception as exc:
        # Do not include exception text: it can contain configured endpoint data.
        logger.warning(
            "Core Phoenix tracing disabled: setup failed (%s).", type(exc).__name__
        )
        return None
