"""Reclassification / backfill agent.

The 00_facility_pipeline notebook scores every facility's extracted capability
claims into a confidence (0-100) and trust_bucket (Verified / Plausible /
Unverified / Contradicted) in workspace.default.facility_app. This agent finds
facilities that landed in Contradicted or Unverified (or have low confidence),
folds in any planner-reported corrections from region_annotations, re-runs the
same LLM extraction prompt with that extra context, rescores the result with
the same deterministic formula (see scoring.py), and writes the refreshed
fields back to facility_refined / facility_confidence.
"""
import json
import re
import scoring
import warehouse
from lakebase import get_connection
from common import get_client, run_agent, ENDPOINT_NAME

FACILITY_APP = "workspace.default.facility_app"
FACILITY_REFINED = "workspace.default.facility_refined"
FACILITY_CONFIDENCE = "workspace.default.facility_confidence"
BRONZE_TABLE = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities"

_cap_v = ", ".join(scoring.CAP_VOCAB)
_spec_v = ", ".join(scoring.SPEC_VOCAB)

# Same extraction prompt as 00_facility_pipeline, so re-extraction stays
# consistent with the rest of the gold table.
EXTRACTION_PROMPT = (
    "You extract structured data about an Indian healthcare facility from noisy text "
    "(may be JSON-like lists of claims). Return ONLY one minified JSON object, no markdown, keys: "
    "facility_type (hospital, specialty_hospital, clinic, phc, chc, sub_centre, nursing_home, diagnostic_centre, "
    "medical_college, dispensary, eye_hospital, dental_clinic, maternity_home, ayush, other, unknown), "
    "ownership (public, private, trust_ngo, unknown), "
    f"capabilities (subset of [{_cap_v}]), specialties (subset of [{_spec_v}]), "
    "key_procedures (array), equipment (array), services (array), accreditations (array), "
    "emergency_24x7, maternity_services, ambulance_available, blood_bank, pharmacy_onsite, teleconsultation (bool or null), "
    "summary (one factual sentence). Use null/empty when unknown; never invent. Text: "
)


def _sql_literal(value):
    """Render a Python value as a SQL literal for an UPDATE ... SET clause."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "array(" + ", ".join(_sql_literal(v) for v in value) + ")"
    return "'" + str(value).replace("'", "''") + "'"


def _get_facility_annotations(facility_id: str):
    """Planner notes for a real facility_id (bypasses mock_data, which only
    knows about the demo F001-F004 facilities)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, author, note, created_at
                FROM region_annotations
                WHERE facility_id = %s AND is_test = false
                ORDER BY created_at DESC
                """,
                (facility_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "author": r[1], "note": r[2], "created_at": r[3].isoformat()} for r in rows]


def _extract_and_score(name, all_text, num_doctors=None, capacity=None, year_established=None,
                        field_completeness_pct=None, correction_note=None):
    """Run the extraction prompt (+ optional correction context) through the LLM,
    normalize the result, and rescore it. No warehouse access - pure function,
    safe to test standalone."""
    prompt_text = EXTRACTION_PROMPT + (all_text or "")
    if correction_note:
        prompt_text += (
            "\n\nA field planner has reported the following, which should override "
            "the text above where they conflict: " + correction_note
        )

    client = get_client()
    resp = client.chat.completions.create(
        model=ENDPOINT_NAME,
        messages=[{"role": "user", "content": prompt_text}],
        temperature=0,
    )
    raw = resp.choices[0].message.content or ""
    match = re.search(r"\{.*\}", raw, re.S)
    e = json.loads(match.group(0)) if match else {}

    capabilities = scoring.normalize_list(e.get("capabilities"), vocab=scoring.CAP_VOCAB)
    specialties = scoring.normalize_list(e.get("specialties"), vocab=scoring.SPEC_VOCAB)
    key_procedures = scoring.normalize_list(e.get("key_procedures"))
    equipment = scoring.normalize_list(e.get("equipment"))
    services = scoring.normalize_list(e.get("services"))
    accreditations = scoring.normalize_list(e.get("accreditations"))
    all_caps = sorted(set(capabilities) | set(specialties))

    score = scoring.score_facility(
        name=name, all_text=all_text, claimed_caps=all_caps,
        num_doctors=num_doctors, capacity=capacity,
        year_established=year_established, field_completeness_pct=field_completeness_pct,
    )
    flags = scoring.compute_flags(all_caps, all_text)

    refined = {
        "facility_type": (e.get("facility_type") or "unknown").lower(),
        "ownership": (e.get("ownership") or "unknown").lower(),
        "summary": e.get("summary"),
        "capabilities": capabilities,
        "specialties": specialties,
        "key_procedures": key_procedures,
        "equipment": equipment,
        "services": services,
        "accreditations": accreditations,
        "capability_count": len(capabilities),
        "display_capabilities": ", ".join(capabilities),
        "display_specialties": ", ".join(specialties),
        "emergency_24x7": e.get("emergency_24x7"),
        "maternity_services": e.get("maternity_services"),
        "ambulance_available": e.get("ambulance_available"),
        "blood_bank": e.get("blood_bank"),
        "pharmacy_onsite": e.get("pharmacy_onsite"),
        "teleconsultation": e.get("teleconsultation"),
        **flags,
    }
    return {"extracted": e, "refined": refined, "score": score}


def list_reclassification_candidates(trust_bucket: str = None, max_confidence: float = None,
                                      district: str = None, limit: int = 10):
    limit = max(1, min(int(limit or 10), 50))
    conditions, params = [], {}

    if trust_bucket:
        conditions.append("trust_bucket = :trust_bucket")
        params["trust_bucket"] = trust_bucket
    if max_confidence is not None:
        conditions.append("confidence <= :max_confidence")
        params["max_confidence"] = float(max_confidence)
    if district:
        conditions.append("upper(district) = upper(:district)")
        params["district"] = district
    if not conditions:
        conditions.append("trust_bucket IN ('Contradicted', 'Unverified')")

    where_clause = " AND ".join(conditions)
    _, rows = warehouse.run_sql(
        f"""
        SELECT facility_id, name, district, confidence, trust_bucket, n_contradictions,
               display_capabilities, display_specialties
        FROM {FACILITY_APP}
        WHERE {where_clause}
        ORDER BY confidence ASC NULLS FIRST, n_contradictions DESC
        LIMIT {limit}
        """,
        params,
    )
    return rows


_INT_FIELDS = ("doctors", "beds", "year_established", "n_contradictions")
_FLOAT_FIELDS = ("field_completeness_pct", "confidence", "confidence_band")
_ARRAY_FIELDS = ("capabilities", "specialties")


def _coerce_record(record):
    """The SQL Statement Execution API returns every column as a string;
    cast the ones get_facility_detail/reclassify_facility need as numbers/arrays."""
    for f in _INT_FIELDS:
        if record.get(f) is not None:
            record[f] = int(float(record[f]))
    for f in _FLOAT_FIELDS:
        if record.get(f) is not None:
            record[f] = float(record[f])
    for f in _ARRAY_FIELDS:
        if record.get(f):
            record[f] = json.loads(record[f])
    return record


def get_facility_detail(facility_id: str):
    _, rows = warehouse.run_sql(
        f"""
        SELECT facility_id, name, district, state, pincode, doctors, beds, year_established,
               field_completeness_pct, capabilities, specialties, display_capabilities, display_specialties,
               facility_type, ownership, summary, confidence, confidence_band, evidence_level,
               trust_bucket, n_contradictions
        FROM {FACILITY_APP}
        WHERE facility_id = :facility_id
        """,
        {"facility_id": facility_id},
    )
    if not rows:
        return {"error": f"unknown facility_id {facility_id}"}
    record = _coerce_record(rows[0])

    _, raw_rows = warehouse.run_sql(
        f"""
        SELECT description, capability, procedure, equipment, specialties
        FROM {BRONZE_TABLE}
        WHERE unique_id = :facility_id
        """,
        {"facility_id": facility_id},
    )
    raw = raw_rows[0] if raw_rows else {}
    record["all_text"] = " . ".join(
        (raw.get(c) or "") for c in ("description", "capability", "procedure", "equipment", "specialties")
    ).lower()
    try:
        record["planner_notes"] = _get_facility_annotations(facility_id)
    except Exception as exc:
        # Lakebase is a separate system from the warehouse and can be down
        # independently - don't let that block reclassification.
        record["planner_notes"] = []
        record["planner_notes_error"] = str(exc)
    return record


def reclassify_facility(facility_id: str, correction_note: str = None):
    """Re-run extraction + rescoring for one facility, folding in planner notes
    plus an optional extra correction, and write the result back to
    facility_refined / facility_confidence."""
    detail = get_facility_detail(facility_id)
    if "error" in detail:
        return detail

    notes = [n["note"] for n in detail["planner_notes"]]
    if correction_note:
        notes.append(correction_note)
    combined_note = " ".join(notes) if notes else None

    result = _extract_and_score(
        name=detail["name"], all_text=detail["all_text"],
        num_doctors=detail.get("doctors"), capacity=detail.get("beds"),
        year_established=detail.get("year_established"),
        field_completeness_pct=detail.get("field_completeness_pct"),
        correction_note=combined_note,
    )
    refined, score = result["refined"], result["score"]

    refined_set = ", ".join(f"{col} = {_sql_literal(val)}" for col, val in refined.items())
    warehouse.run_sql(
        f"UPDATE {FACILITY_REFINED} SET {refined_set}, "
        f"extracted_at = current_timestamp(), pipe_version = 'reclass-v1' "
        f"WHERE facility_id = :facility_id",
        {"facility_id": facility_id},
    )

    conf_set = ", ".join(f"{col} = {_sql_literal(val)}" for col, val in score.items())
    warehouse.run_sql(
        f"UPDATE {FACILITY_CONFIDENCE} SET {conf_set}, "
        f"scored_at = current_timestamp(), score_version = 'reclass-v1' "
        f"WHERE facility_id = :facility_id",
        {"facility_id": facility_id},
    )

    return {
        "facility_id": facility_id,
        "name": detail["name"],
        "correction_applied": combined_note,
        "before": {
            "confidence": detail["confidence"],
            "trust_bucket": detail["trust_bucket"],
            "n_contradictions": detail["n_contradictions"],
            "capabilities": detail["capabilities"],
            "specialties": detail["specialties"],
        },
        "after": {
            "confidence": score["confidence"],
            "trust_bucket": score["trust_bucket"],
            "n_contradictions": score["n_contradictions"],
            "capabilities": refined["capabilities"],
            "specialties": refined["specialties"],
        },
        "extraction_summary": result["extracted"].get("summary"),
    }


TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "list_reclassification_candidates",
            "description": (
                "List facilities from facility_app whose capability extraction needs review. "
                "Defaults to trust_bucket Contradicted or Unverified if no filters are given."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trust_bucket": {"type": "string", "enum": ["Contradicted", "Unverified", "Plausible", "Verified"]},
                    "max_confidence": {"type": "number", "description": "Only facilities with confidence <= this value"},
                    "district": {"type": "string"},
                    "limit": {"type": "integer", "description": "Max rows to return (default 10, max 50)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_facility_detail",
            "description": (
                "Fetch a facility's current extracted fields, confidence/trust_bucket, "
                "the raw source text it was extracted from, and any planner notes saved for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"facility_id": {"type": "string"}},
                "required": ["facility_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reclassify_facility",
            "description": (
                "Re-run capability extraction for one facility, incorporating any saved planner "
                "notes plus an optional extra correction, rescore it with the deterministic "
                "confidence/trust_bucket formula, and write the refreshed fields back to "
                "facility_refined and facility_confidence. Returns a before/after diff."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "facility_id": {"type": "string"},
                    "correction_note": {
                        "type": "string",
                        "description": "Extra ground-truth context (e.g. a planner's field report) to weigh against the source text",
                    },
                },
                "required": ["facility_id"],
            },
        },
    },
]

TOOL_IMPLS = {
    "list_reclassification_candidates": list_reclassification_candidates,
    "get_facility_detail": get_facility_detail,
    "reclassify_facility": reclassify_facility,
}

SYSTEM_PROMPT = (
    "You are the Care Gap Atlas reclassification agent. Facilities in "
    "workspace.default.facility_app carry a confidence score (0-100) and trust_bucket "
    "(Verified / Plausible / Unverified / Contradicted) computed by a deterministic pipeline "
    "from their extracted capability claims. Your job is to find facilities whose trust_bucket "
    "is Contradicted or Unverified (or whose confidence is low), inspect why, and trigger a "
    "backfill: reclassify_facility re-runs extraction with any planner corrections folded in, "
    "rescores the result, and writes it back. Always report the before/after confidence and "
    "trust_bucket so the planner can see what changed and why."
)


if __name__ == "__main__":
    print("--- Standalone re-extraction + rescoring (no warehouse needed) ---")
    before = _extract_and_score(
        name="Bidar Rural Health Clinic",
        all_text=(
            "small primary health clinic offering general checkups, vaccination, and basic "
            "first aid. claims icu and dialysis services are available on request."
        ),
        num_doctors=2, capacity=6, year_established=1998, field_completeness_pct=60,
    )
    print("Before correction:")
    print(json.dumps({"refined_caps": before["refined"]["capabilities"], "score": before["score"]}, indent=2))

    after = _extract_and_score(
        name="Bidar Rural Health Clinic",
        all_text=(
            "small primary health clinic offering general checkups, vaccination, and basic "
            "first aid. claims icu and dialysis services are available on request."
        ),
        num_doctors=2, capacity=6, year_established=1998, field_completeness_pct=60,
        correction_note=(
            "Planner visited the site: this clinic does NOT have ICU or dialysis. "
            "It only offers basic outpatient consultations and vaccination."
        ),
    )
    print("\nAfter planner correction:")
    print(json.dumps({"refined_caps": after["refined"]["capabilities"], "score": after["score"]}, indent=2))

    print("\n--- Live agent loop (needs warehouse access) ---")
    client = get_client()
    print(run_agent(
        client, SYSTEM_PROMPT, TOOLS_SPEC, TOOL_IMPLS,
        "Find up to 3 facilities that need reclassification review and summarize why each "
        "landed in their current trust_bucket.",
    ))
