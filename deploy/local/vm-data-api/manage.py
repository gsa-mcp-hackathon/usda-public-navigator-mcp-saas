"""Build and independently toggle only the MCP-SaaS VM API and tunnel."""

import subprocess
import sys
from pathlib import Path

from config_loader import load_config

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
C = load_config(HERE / "config.json")


def run(args):
    """Run a command and exit with its status and message if it fails."""
    p = subprocess.run(args, text=True, capture_output=True)
    if p.returncode:
        print(p.stderr.strip() or "operation failed", file=sys.stderr)
        raise SystemExit(p.returncode)
    if p.stdout.strip():
        print(p.stdout.strip())


def exists(name):
    """Return whether a container with this name exists."""
    return subprocess.run(["podman", "container", "exists", name]).returncode == 0


def running(name):
    """Return whether the named container is running."""
    p = subprocess.run(
        ["podman", "inspect", "--format", "{{.State.Running}}", name],
        text=True,
        capture_output=True,
    )
    return p.returncode == 0 and p.stdout.strip() == "true"


def ensure_api_network():
    """Create the API container network if it does not exist."""
    result = subprocess.run(
        ["podman", "network", "exists", C["api_network"]],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        run(["podman", "network", "create", C["api_network"]])


def build():
    """Build the API container image."""
    run(
        [
            "podman",
            "build",
            "--file",
            str(HERE / "Dockerfile"),
            "--tag",
            C["api_image"],
            str(REPOSITORY_ROOT),
        ]
    )


def catalog_scope_env_args():
    """Return optional catalog scope settings, rejecting partial configuration."""
    keys = (
        "catalog_scope_cluster_id",
        "catalog_scope_pattern_version",
        "catalog_scope_tenant_id",
    )
    values = [C.get(key) for key in keys]
    if all(value is None for value in values):
        return []
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(
            "Catalog scope must set cluster, pattern version, and tenant together."
        )
    return [
        "--env",
        f"API_CATALOG_CLUSTER_ID={values[0]}",
        "--env",
        f"API_CATALOG_PATTERN_VERSION={values[1]}",
        "--env",
        f"API_CATALOG_TENANT_ID={values[2]}",
    ]


def profile_fixture_reader_secret_args():
    """Bind the isolated fixture-reader capability only when configured."""
    secret_name = C.get("profile_fixture_read_token_secret")
    secret_target = C.get("profile_fixture_read_token_target")
    if secret_name is None and secret_target is None:
        return []
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (secret_name, secret_target)
    ):
        raise ValueError(
            "Fixture-reader secret configuration must set both secret name and target."
        )
    return [
        "--secret",
        f"{secret_name},type=mount,target={secret_target},uid=10001,gid=10001,mode=0400",
        "--env",
        f"API_PROFILE_FIXTURE_READ_TOKEN_FILE=/run/secrets/{secret_target}",
    ]


def configured_api_secret_names():
    """Return configured API secret names without reading their values."""
    secret_keys = (
        "api_dsn_secret",
        "api_token_secret",
        "registry_read_token_secret",
        "registry_write_token_secret",
        "template_registry_write_token_secret",
        "tenant_registry_write_token_secret",
        "route_telemetry_write_token_secret",
        "profile_read_token_secret",
        "profile_write_token_secret",
        "catalog_read_token_secret",
        "public_catalog_ingest_dsn_secret",
        "public_catalog_write_token_secret",
        "requirements_package_write_token_secret",
        "criteria_ruleset_write_token_secret",
    )
    names = [C[key] for key in secret_keys]
    optional_secret = C.get("profile_fixture_read_token_secret")
    optional_target = C.get("profile_fixture_read_token_target")
    if optional_secret is not None or optional_target is not None:
        profile_fixture_reader_secret_args()
        names.append(optional_secret)
    return names


def preflight_api_reconfigure():
    """Verify replacement image, database network, and secret names before removal."""
    image = C.get("api_image")
    database_network = C.get("database_network")
    if not isinstance(image, str) or not image.strip():
        raise ValueError("API image must be configured before reconfigure.")
    if not isinstance(database_network, str) or not database_network.strip():
        raise ValueError("Database network must be configured before reconfigure.")
    checks = [
        ("image", ["podman", "image", "exists", image]),
        ("database network", ["podman", "network", "exists", database_network]),
        *(
            (f"secret {name}", ["podman", "secret", "exists", name])
            for name in configured_api_secret_names()
        ),
    ]
    for label, command in checks:
        if subprocess.run(command, capture_output=True, text=True).returncode:
            raise RuntimeError(
                f"API reconfigure preflight failed: configured {label} is unavailable; "
                "the running API container was left in place."
            )


def reconfigure_api():
    """Replace the API only after verifying every configured runtime dependency."""
    if exists(C["api_container"]):
        preflight_api_reconfigure()
        run(["podman", "rm", "--force", C["api_container"]])
    create_api()


def create_api():
    """Create the data API container."""
    ensure_api_network()
    run(
        [
            "podman",
            "run",
            "--detach",
            "--name",
            C["api_container"],
            "--restart",
            "unless-stopped",
            "--network",
            C["database_network"],
            "--publish",
            f"127.0.0.1:{C['api_port']}:8080",
            "--secret",
            f"{C['api_dsn_secret']},type=mount,target={C['api_dsn_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['api_token_secret']},type=mount,target={C['api_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['registry_read_token_secret']},type=mount,target={C['registry_read_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['registry_write_token_secret']},type=mount,target={C['registry_write_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['template_registry_write_token_secret']},type=mount,target={C['template_registry_write_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['tenant_registry_write_token_secret']},type=mount,target={C['tenant_registry_write_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['route_telemetry_write_token_secret']},type=mount,target={C['route_telemetry_write_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['profile_read_token_secret']},type=mount,target={C['profile_read_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['profile_write_token_secret']},type=mount,target={C['profile_write_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['catalog_read_token_secret']},type=mount,target={C['catalog_read_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['public_catalog_ingest_dsn_secret']},type=mount,target={C['public_catalog_ingest_dsn_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['public_catalog_write_token_secret']},type=mount,target={C['public_catalog_write_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['requirements_package_write_token_secret']},type=mount,target={C['requirements_package_write_token_target']},uid=10001,gid=10001,mode=0400",
            "--secret",
            f"{C['criteria_ruleset_write_token_secret']},type=mount,target={C['criteria_ruleset_write_token_target']},uid=10001,gid=10001,mode=0400",
            "--env",
            f"DB_DSN_FILE=/run/secrets/{C['api_dsn_target']}",
            "--env",
            f"API_TOKEN_FILE=/run/secrets/{C['api_token_target']}",
            "--env",
            f"API_REGISTRY_READ_TOKEN_FILE=/run/secrets/{C['registry_read_token_target']}",
            "--env",
            f"API_REGISTRY_WRITE_TOKEN_FILE=/run/secrets/{C['registry_write_token_target']}",
            "--env",
            f"API_TEMPLATE_REGISTRY_WRITE_TOKEN_FILE=/run/secrets/{C['template_registry_write_token_target']}",
            "--env",
            f"API_TENANT_REGISTRY_WRITE_TOKEN_FILE=/run/secrets/{C['tenant_registry_write_token_target']}",
            "--env",
            f"API_ROUTE_TELEMETRY_WRITE_TOKEN_FILE=/run/secrets/{C['route_telemetry_write_token_target']}",
            "--env",
            f"API_PROFILE_READ_TOKEN_FILE=/run/secrets/{C['profile_read_token_target']}",
            *profile_fixture_reader_secret_args(),
            "--env",
            f"API_PROFILE_WRITE_TOKEN_FILE=/run/secrets/{C['profile_write_token_target']}",
            "--env",
            f"API_CATALOG_READ_TOKEN_FILE=/run/secrets/{C['catalog_read_token_target']}",
            "--env",
            f"PUBLIC_CATALOG_INGEST_DSN_FILE=/run/secrets/{C['public_catalog_ingest_dsn_target']}",
            "--env",
            f"API_PUBLIC_CATALOG_WRITE_TOKEN_FILE=/run/secrets/{C['public_catalog_write_token_target']}",
            "--env",
            f"API_REQUIREMENTS_PACKAGE_WRITE_TOKEN_FILE=/run/secrets/{C['requirements_package_write_token_target']}",
            "--env",
            f"API_CRITERIA_RULESET_WRITE_TOKEN_FILE=/run/secrets/{C['criteria_ruleset_write_token_target']}",
            *catalog_scope_env_args(),
            C["api_image"],
        ]
    )
    run(
        [
            "podman",
            "network",
            "connect",
            "--alias",
            C["api_network_alias"],
            C["api_network"],
            C["api_container"],
        ]
    )


def create_tunnel():
    """Create the outbound-only tunnel container."""
    run(
        [
            "podman",
            "run",
            "--detach",
            "--name",
            C["tunnel_container"],
            "--restart",
            "unless-stopped",
            "--network",
            "host",
            "--secret",
            f"{C['tunnel_token_secret']},type=mount,target={C['tunnel_token_target']}",
            C["tunnel_image"],
            "tunnel",
            "--no-autoupdate",
            "run",
            "--token-file",
            f"/run/secrets/{C['tunnel_token_target']}",
        ]
    )


def toggle(name, action):
    """Start, stop or report the status of a container."""
    if action == "start":
        if not exists(name):
            raise SystemExit(f"{name} not installed")
        if not running(name):
            run(["podman", "start", name])
    elif action == "stop" and exists(name) and running(name):
        run(["podman", "stop", name])
    elif action == "status":
        print("running" if running(name) else "stopped")


def main():
    """Parse the command and run it."""
    cmds = {
        "build",
        "install",
        "reconfigure",
        "start",
        "stop",
        "status",
        "tunnel-install",
        "tunnel-start",
        "tunnel-stop",
        "tunnel-status",
    }
    cmd = sys.argv[1] if len(sys.argv) == 2 else ""
    if cmd not in cmds:
        raise SystemExit("usage: manage.py {" + ", ".join(sorted(cmds)) + "}")
    if cmd == "build":
        build()
    elif cmd == "install":
        if exists(C["api_container"]):
            raise SystemExit(
                "API container exists; use reconfigure only for this test API"
            )
        create_api()
    elif cmd == "reconfigure":
        reconfigure_api()
    elif cmd in {"start", "stop", "status"}:
        toggle(C["api_container"], cmd)
    elif cmd == "tunnel-install":
        if exists(C["tunnel_container"]):
            raise SystemExit("MCP-SaaS tunnel connector already exists")
        create_tunnel()
    elif cmd == "tunnel-start":
        if not exists(C["tunnel_container"]):
            create_tunnel()
        else:
            toggle(C["tunnel_container"], "start")
    elif cmd == "tunnel-stop":
        toggle(C["tunnel_container"], "stop")
    else:
        toggle(C["tunnel_container"], "status")


if __name__ == "__main__":
    main()
