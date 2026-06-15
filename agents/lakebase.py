"""Shared Lakebase Postgres connection helper for the Care Gap Atlas.

Reused by the agents here and intended to be reused by the Databricks App
backend - same project/branch/endpoint, same credential pattern.
"""
import os
import re
import subprocess
import psycopg
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


def get_connection():
    """Open a fresh Postgres connection. Tokens expire after ~1hr, so generate per-connection.

    The Postgres role name must match the connecting principal's Databricks
    identity: a user's email locally, or a service principal's application ID
    when running inside Model Serving.
    """
    w = get_workspace_client()
    token = w.postgres.generate_database_credential(ENDPOINT).token
    pg_user = w.current_user.me().user_name
    kwargs = dict(host=HOST, dbname=DBNAME, user=pg_user, password=token, sslmode="require")
    hostaddr = _resolve_hostaddr(HOST)
    if hostaddr:
        kwargs["hostaddr"] = hostaddr
    try:
        return psycopg.connect(**kwargs)
    except psycopg.OperationalError as e:
        raise psycopg.OperationalError(f"(connecting as Postgres role '{pg_user}') {e}") from e
