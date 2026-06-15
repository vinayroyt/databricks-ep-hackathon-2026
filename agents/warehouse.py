"""Shared SQL warehouse helper for querying/updating the facility_app /
facility_refined / facility_confidence Unity Catalog tables produced by the
00_facility_pipeline notebook.
"""
import time
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from lakebase import get_workspace_client

WAREHOUSE_ID = "f0eca2b7634ebc80"  # Serverless Starter Warehouse

_TYPE_MAP = {str: "STRING", bool: "BOOLEAN", int: "INT", float: "DOUBLE"}


def _param(name, value):
    for py_type, sql_type in _TYPE_MAP.items():
        if isinstance(value, py_type):
            return StatementParameterListItem(name=name, value=None if value is None else str(value), type=sql_type)
    return StatementParameterListItem(name=name, value=value, type="STRING")


def run_sql(statement: str, params: dict = None):
    """Execute a SQL statement and return (columns, rows). `params` are bound
    via :name placeholders, type-inferred from the Python value."""
    w = get_workspace_client()
    parameters = [_param(k, v) for k, v in (params or {}).items()]

    res = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=statement,
        parameters=parameters or None,
        wait_timeout="30s",
    )
    while res.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1)
        res = w.statement_execution.get_statement(res.statement_id)

    if res.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL failed ({res.status.state}): {res.status.error}")

    columns = [c.name for c in res.manifest.schema.columns] if res.manifest and res.manifest.schema else []
    data = res.result.data_array if res.result and res.result.data_array else []
    rows = [dict(zip(columns, row)) for row in data]
    return columns, rows
