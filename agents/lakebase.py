"""Shared Lakebase Postgres connection helper for the Care Gap Atlas.

Reused by the agents here and intended to be reused by the Databricks App
backend - same project/branch/endpoint, same credential pattern.
"""
import os
import re
import subprocess
import uuid
import psycopg
import requests
from databricks.sdk import WorkspaceClient

PROFILE = "dbrx-hackathon-2026"
PROJECT_ID = "dbrx-hackathon-2026"
ENDPOINT = f"projects/{PROJECT_ID}/branches/production/endpoints/primary"
HOST = "ep-long-heart-d8anwpz5.database.us-east-2.cloud.databricks.com"
DBNAME = "databricks_postgres"


def get_workspace_client() -> WorkspaceClient:
    """Local dev uses the CLI profile; inside Model Serving, fall back to default env auth."""
    if os.path.exists(os.path.expanduser("~/.databrickscfg")):
        return WorkspaceClient(profile=PROFILE)
    return WorkspaceClient()


def _resolve_hostaddr(host: str):
    """macOS's resolver can fail on these long Lakebase hostnames; resolve via dig."""
    try:
        out = subprocess.check_output(["dig", "+short", host], text=True, timeout=5)
        ips = [line for line in out.splitlines() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", line)]
        return ips[-1] if ips else None
    except Exception:
        return None


def _extract_token(res):
    if hasattr(res, "token"):
        return res.token
    if isinstance(res, dict):
        token = res.get("token") or res.get("access_token")
        if token:
            return token
    raise RuntimeError(f"Lakebase credential response did not contain a token: {res!r}")


def _workspace_api_post(w: WorkspaceClient, path: str, body: dict):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    api_client = getattr(w, "_api_client", None)
    if api_client is not None and hasattr(api_client, "do"):
        return api_client.do("POST", path, body=body, headers=headers)

    cfg = getattr(w, "config", None) or getattr(w, "_config", None)
    if cfg is None or not hasattr(cfg, "authenticate") or not getattr(cfg, "host", None):
        raise RuntimeError("Databricks SDK client cannot make authenticated REST calls")
    auth_headers = cfg.authenticate()
    auth_headers.update(headers)
    response = requests.post(f"{cfg.host}{path}", json=body, headers=auth_headers, timeout=30)
    response.raise_for_status()
    return response.json()


def _generate_database_token(w: WorkspaceClient, endpoint: str) -> str:
    postgres_api = getattr(w, "postgres", None)
    if postgres_api is not None:
        return _extract_token(postgres_api.generate_database_credential(endpoint=endpoint))

    # Databricks Apps may ship an older SDK without `w.postgres`; the REST API
    # is still available through the configured app identity.
    try:
        return _extract_token(
            _workspace_api_post(
                w,
                "/api/2.0/postgres/credentials",
                {"endpoint": endpoint},
            )
        )
    except Exception:
        return _extract_token(
            _workspace_api_post(
                w,
                "/api/2.0/database/credentials",
                {"instance_names": [endpoint], "request_id": str(uuid.uuid4())},
            )
        )


def get_connection():
    """Open a fresh Postgres connection. Tokens expire after ~1hr, so generate per-connection.

    The Postgres role name must match the connecting principal's Databricks
    identity: a user's email locally, or a service principal's application ID
    when running inside Model Serving.
    """
    w = get_workspace_client()
    endpoint = os.environ.get("LAKEBASE_ENDPOINT", ENDPOINT)
    token = _generate_database_token(w, endpoint)
    host = os.environ.get("PGHOST", HOST)
    pg_user = os.environ.get("PGUSER") or w.current_user.me().user_name
    kwargs = dict(
        host=host,
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=os.environ.get("PGDATABASE", DBNAME),
        user=pg_user,
        password=token,
        sslmode=os.environ.get("PGSSLMODE", "require"),
    )
    if not os.environ.get("PGHOST"):
        hostaddr = _resolve_hostaddr(host)
        if hostaddr:
            kwargs["hostaddr"] = hostaddr
    try:
        return psycopg.connect(**kwargs)
    except psycopg.OperationalError as e:
        raise psycopg.OperationalError(f"(connecting as Postgres role '{pg_user}') {e}") from e
