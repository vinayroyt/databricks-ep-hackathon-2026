"""Planner annotation agent: lets a planner save/recall notes at the region
or facility level.

This is the write-back capability Genie can't do on its own. Annotations are
stored in the `region_annotations` table in the dbrx-hackathon-2026 Lakebase
project - the same store the Databricks App backend reads/writes for the
persistence feature.
"""
import json
import mock_data
from lakebase import get_connection
from common import get_client, run_agent


def list_regions():
    return [{"region_id": r["region_id"], "region_name": r["region_name"]} for r in mock_data.get_regions()]


def list_facilities(region_id: str):
    return [{"facility_id": f["facility_id"], "name": f["name"]} for f in mock_data.get_facilities(region_id)]


def save_annotation(note: str, region_id: str, facility_id: str = None, author: str = None, is_test: bool = False):
    region = mock_data.get_region(region_id)
    if region is None:
        return {"error": f"unknown region_id {region_id}"}
    region_id = region["region_id"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO region_annotations (region_id, facility_id, author, note, is_test)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (region_id, facility_id, author, note, is_test),
            )
            new_id, created_at = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "saved",
        "id": new_id,
        "region_id": region_id,
        "facility_id": facility_id,
        "note": note,
        "created_at": created_at.isoformat(),
    }


def get_annotations(region_id: str = None, facility_id: str = None, include_test: bool = False):
    conditions = []
    params = []

    if region_id:
        region = mock_data.get_region(region_id)
        if region is None:
            return {"error": f"unknown region_id {region_id}"}
        conditions.append("region_id = %s")
        params.append(region["region_id"])

    if facility_id:
        conditions.append("facility_id = %s")
        params.append(facility_id)

    if not include_test:
        conditions.append("is_test = false")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, region_id, facility_id, author, note, created_at
                FROM region_annotations
                {where_clause}
                ORDER BY created_at DESC
                """,
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r[0],
            "region_id": r[1],
            "facility_id": r[2],
            "author": r[3],
            "note": r[4],
            "created_at": r[5].isoformat(),
        }
        for r in rows
    ]


TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "list_regions",
            "description": "List all region IDs and names available in the system.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_facilities",
            "description": "List facility IDs and names within a region.",
            "parameters": {
                "type": "object",
                "properties": {"region_id": {"type": "string"}},
                "required": ["region_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_annotation",
            "description": (
                "Save a planner note for later sessions. Always requires a region_id. "
                "If facility_id is also given, the note is scoped to that facility "
                "within the region; otherwise it's a region-level note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region_id": {"type": "string", "description": "Region ID or name, e.g. R01 or Bidar"},
                    "facility_id": {"type": "string", "description": "Optional facility ID or name for a facility-level note"},
                    "note": {"type": "string", "description": "The note text to save"},
                    "author": {"type": "string", "description": "Optional name of the planner saving the note"},
                    "is_test": {"type": "boolean", "description": "Mark true for test/dev annotations so they're excluded from demo views. Defaults to false."},
                },
                "required": ["region_id", "note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_annotations",
            "description": (
                "Retrieve previously saved planner notes. Filter by region_id and/or "
                "facility_id; omit both to fetch all notes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region_id": {"type": "string"},
                    "facility_id": {"type": "string"},
                    "include_test": {"type": "boolean", "description": "Include test/dev annotations. Defaults to false."},
                },
            },
        },
    },
]

TOOL_IMPLS = {
    "list_regions": list_regions,
    "list_facilities": list_facilities,
    "save_annotation": save_annotation,
    "get_annotations": get_annotations,
}

SYSTEM_PROMPT = (
    "You are a planner's assistant for the Care Gap Atlas. You help planners save "
    "and recall notes about regions and individual facilities for later sessions. "
    "Use the available tools to look up region/facility IDs, save notes (region-level "
    "or facility-level), and retrieve past notes. Be concise."
)


if __name__ == "__main__":
    client = get_client()

    test_suffix = " (mark this annotation as a test row by passing is_test=true)"

    print("--- Saving a region-level note ---")
    print(run_agent(
        client, SYSTEM_PROMPT, TOOLS_SPEC, TOOL_IMPLS,
        "Save a note on the Bidar region: 2 facilities claim ICU capability but "
        "report zero ICU beds between them - needs follow-up verification next session."
        + test_suffix,
    ))

    print("\n--- Saving a facility-level note ---")
    print(run_agent(
        client, SYSTEM_PROMPT, TOOLS_SPEC, TOOL_IMPLS,
        "For the District General Hospital in Bidar, save a note: 'Called the "
        "facility - they confirmed the ICU is currently non-operational pending "
        "staffing.' Sign it as planner Asha." + test_suffix,
    ))

    print("\n--- Recalling notes (including test rows) ---")
    print(run_agent(
        client, SYSTEM_PROMPT, TOOLS_SPEC, TOOL_IMPLS,
        "What notes do we have saved for Bidar, at both the region and facility "
        "level? Include test rows.",
    ))
