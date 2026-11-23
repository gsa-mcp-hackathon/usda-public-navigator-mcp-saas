FROM python:3.12.16-alpine3.24@sha256:a61cbd76a882645de7f91423329fe43a67eed60d7da24f9e5331414bb3618b20

ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="MCP SaaS Persistence Access API" \
    org.opencontainers.image.revision=${VCS_REF}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app
RUN apk upgrade --no-cache && apk add --no-cache xz-libs
COPY deploy/local/vm-data-api/requirements.txt ./
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
        setuptools==84.0.0 wheel==0.48.0 \
    && python -m pip install --no-cache-dir --disable-pip-version-check \
        -r requirements.txt

# The Data API persists to PostgreSQL through Psycopg and has no SQLite runtime path.
RUN apk add --no-cache xz-libs libbz2 gdbm libffi libnsl libpanelw libtirpc libuuid \
        readline libcrypto3 libssl3 libncursesw && \
    apk del .python-rundeps && \
    python -c "import bz2, ctypes, dbm.gnu, lzma, readline, ssl, uuid"

COPY --chown=10001:10001 deploy/local/vm-data-api/api.py \
    deploy/local/vm-data-api/criteria_rulesets.py \
    deploy/local/vm-data-api/template_packages.py \
    deploy/local/vm-data-api/tenant_packages.py \
    deploy/local/vm-data-api/evaluation_fixtures.py \
    deploy/local/vm-data-api/schema_tags.py \
    deploy/local/vm-data-api/source_policy_manifests.py \
    deploy/local/vm-data-api/program_candidate_records.py \
    deploy/local/vm-data-api/program_candidate_publications.py \
    deploy/local/vm-data-api/operator_audit.py \
    deploy/local/vm-data-api/public_catalog_registry.py \
    deploy/local/vm-data-api/profile_interests.py ./
COPY --chown=10001:10001 configs/evaluation-fixture-sets.v1.json ./fixture-sets.v1.json
COPY --chown=10001:10001 src/example_server/profile_refinement.py \
    src/example_server/models.py src/example_server/storage.py \
    src/example_server/registry.py ./example_server/
USER 10001:10001
EXPOSE 8080
CMD ["python", "api.py"]
