# syntax=docker/dockerfile:1
# Build one portable MCP application image for local Podman/Docker and Code Engine.

FROM ghcr.io/astral-sh/uv:0.12.24@sha256:dc1f306f26ba5ec796d32b4a30053a2f61192514d01c42278c8e5e0e90b11b22 AS uv
FROM python:3.12.16-alpine3.24@sha256:a61cbd76a882645de7f91423329fe43a67eed60d7da24f9e5331414bb3618b20

ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="MCP SaaS Platform" \
    org.opencontainers.image.revision=${VCS_REF}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOME=/tmp \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Upgrade the pinned Python base's Alpine packages from the maintained v3.24 repository.
RUN apk upgrade --no-cache

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY configs/registry/demo-v1.json ./configs/registry/demo-v1.json
COPY configs/registry/eligibility-cluster*.json ./configs/registry/
COPY configs/eligibility/public-sources.v1.json ./configs/eligibility/public-sources.v1.json
COPY configs/eligibility/public-search-policy.v1.json ./configs/eligibility/public-search-policy.v1.json
COPY configs/eligibility/catalog-seeds/hrsa-health-center-locations.json ./configs/eligibility/catalog-seeds/hrsa-health-center-locations.json
COPY configs/templates/eligibility*.json ./configs/templates/
COPY registries/schemas ./registries/schemas
COPY scripts/reconcile_registry.py scripts/select_eligibility_release.py ./scripts/
RUN chmod 644 \
    /app/configs/registry/demo-v1.json \
    /app/configs/registry/eligibility-cluster*.json \
    /app/configs/eligibility/public-sources.v1.json \
    /app/configs/eligibility/public-search-policy.v1.json \
    /app/configs/eligibility/catalog-seeds/hrsa-health-center-locations.json \
    /app/configs/templates/eligibility*.json \
    /app/scripts/reconcile_registry.py \
    /app/scripts/select_eligibility_release.py

# Install only locked production dependencies; do not include build/test tools.
# Mount the pinned package manager only for this build step so it is not retained
# in the runtime image or published to the registry.
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv,ro \
    uv pip install --python /usr/local/bin/python --system \
        setuptools==84.0.0 wheel==0.48.0 && \
    uv sync --locked --no-dev --no-cache --no-editable

# The platform persists to PostgreSQL and has no SQLite runtime path.
RUN apk add --no-cache xz-libs libbz2 gdbm libffi libnsl libpanelw libtirpc libuuid \
        readline libcrypto3 libssl3 libncursesw && \
    apk del .python-rundeps && \
    python -c "import bz2, ctypes, dbm.gnu, lzma, readline, ssl, uuid"

# Code Engine only needs read access to application files and listens on 8080.
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "example_server.app"]
