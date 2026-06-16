"""Sync flattened Databricks tables into Lakebase for the Streamlit UI.

Source layers:
- Raw facility records:
  databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
- Geographic reference:
  databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory
- Demand reference:
  databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators
- Extracted + scored facility:
  workspace.default.facility_app

The sync keeps Lakebase simple: the app reads cg_facilities, cg_districts, and
cg_district_capability_scores, while all planner writes go through
region_annotations via annotation_agent.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(ROOT, "agents")
if AGENTS not in sys.path:
    sys.path.insert(0, AGENTS)

import annotation_agent
import lakebase_ui
import scoring
import warehouse


FACILITY_APP = os.getenv("FACILITY_APP_TABLE", "workspace.default.facility_app")
RAW_FACILITIES = os.getenv(
    "RAW_FACILITIES_TABLE",
    "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities",
)
PINCODE_DIRECTORY = os.getenv(
    "PINCODE_DIRECTORY_TABLE",
    "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory",
)
DEMAND_TABLE = os.getenv(
    "DEMAND_TABLE",
    "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators",
)

CAPABILITIES = [
    "icu",
    "dialysis",
    "maternity",
    "emergency_care",
    "blood_bank",
    "operation_theatre",
    "radiology",
    "laboratory",
    "ambulance",
]

NFHS_COLUMNS = {
    "district": "district_name",
    "state": "state_ut",
    "institutional_birth": "institutional_birth_5y_pct",
    "stunting": "child_u5_who_are_stunted_height_for_age_18_pct",
    "anaemia_women": "all_w15_49_who_are_anaemic_pct",
    "oop_delivery": "average_out_of_pocket_expenditure_per_delivery_in_a_public_fac",
}


def _split_table_name(full_name):
    parts = full_name.split(".")
    if len(parts) != 3:
        raise ValueError(f"Expected catalog.schema.table, got {full_name}")
    return parts


def _table_columns(full_name):
    catalog, schema, table = _split_table_name(full_name)
    _, rows = warehouse.run_sql(
        f"""
        SELECT column_name
        FROM {catalog}.information_schema.columns
        WHERE table_schema = :schema AND table_name = :table
        """,
        {"schema": schema, "table": table},
    )
    return {r["column_name"].lower() for r in rows}


def _select_expr(columns, name, alias=None, default="NULL"):
    alias = alias or name
    if name.lower() in columns:
        return f"f.{name} AS {alias}"
    return f"{default} AS {alias}"


def _load_jsonish(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return [v.strip() for v in value.split(",") if v.strip()] if isinstance(default, list) else default


def _as_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded_0_1(value):
    if value is None:
        return None
    return max(0.0, min(float(value), 1.0))


def _avg_present(values, default=0.5):
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else default


NEGATIVE_ICU_BED_PATTERNS = (
    r"\b(no|zero|0)\s+(?:icu\s+)?beds?\b",
    r"\bicu\b.{0,30}\b(no|zero|missing|lack|unknown|not available|not operational|non-operational)\b",
    r"\b(no|missing|lack|unknown)\b.{0,30}\bicu\b.{0,30}\bbeds?\b",
)
POSITIVE_ICU_BED_PATTERNS = (
    r"\b(\d{1,3})\s*[- ]?(?:icu|intensive care|critical care)\s*beds?\b",
    r"\b(?:icu|intensive care|critical care)\b.{0,50}\b(\d{1,3})\s*[- ]?beds?\b",
    r"\b(\d{1,3})\s*[- ]?beds?\b.{0,50}\b(?:icu|intensive care|critical care)\b",
    r"\b(?:icu|intensive care|critical care)\s*beds?\b.{0,80}\b(\d{1,3})\s*(?:total\s*)?(?:available|operational|running|functional)\b",
)


def _has_positive_icu_bed_evidence(text):
    return any(re.search(pattern, text) for pattern in POSITIVE_ICU_BED_PATTERNS)


def _has_negative_icu_bed_evidence(text):
    return any(re.search(pattern, text) for pattern in NEGATIVE_ICU_BED_PATTERNS)


def _trust_flags(row):
    flags = _load_jsonish(row.get("trust_flags"), [])
    trust_bucket = row.get("trust_bucket")
    n_contradictions = _as_int(row.get("n_contradictions")) or 0
    beds = _as_int(row.get("beds"))
    caps = set(_load_jsonish(row.get("capabilities"), []))
    curated_evidence_text = " . ".join(
        str(value or "")
        for value in (
            row.get("summary"),
            json.dumps(_load_jsonish(row.get("evidence"), {})),
        )
    ).lower()
    raw_evidence_text = " . ".join(
        str(value or "")
        for value in (
            row.get("raw_text"),
        )
    ).lower()
    positive_icu_bed_evidence = _has_positive_icu_bed_evidence(curated_evidence_text)
    negative_icu_bed_evidence = (
        not positive_icu_bed_evidence
        and (_has_negative_icu_bed_evidence(curated_evidence_text) or _has_negative_icu_bed_evidence(raw_evidence_text))
    )

    if flags and positive_icu_bed_evidence:
        flags = [
            flag for flag in flags
            if not re.search(r"\bicu\b.*\b(no|missing|lack|zero|0)\b.*\bbeds?\b|\bno\b.*\bicu\b.*\bbeds?\b|\bbed count too low\b", str(flag).lower())
        ]
    if flags:
        return flags

    inferred = []
    if trust_bucket == "Contradicted" or n_contradictions > 0:
        inferred.append("claim conflicts with available structured evidence")
    if "icu" in caps and (beds is None or beds == 0) and not positive_icu_bed_evidence:
        inferred.append("claims ICU but no ICU bed evidence is available")
    if "icu" in caps and beds is not None and beds <= 10 and not positive_icu_bed_evidence:
        inferred.append("bed count too low to plausibly include an ICU")
    return inferred


def _normalize_facility(row):
    capabilities = scoring.normalize_list(_load_jsonish(row.get("capabilities"), []), scoring.CAP_VOCAB)
    specialties = scoring.normalize_list(_load_jsonish(row.get("specialties"), []), scoring.SPEC_VOCAB)
    confidence_score = _as_float(row.get("confidence"))
    trust_score = (confidence_score / 100.0) if confidence_score is not None else None

    confidence = _load_jsonish(row.get("confidence_by_field"), {})
    if not confidence and confidence_score is not None:
        confidence = {"overall": confidence_score}

    evidence = (
        _load_jsonish(row.get("evidence"), {})
        or _load_jsonish(row.get("evidence_snippets"), {})
        or _load_jsonish(row.get("field_evidence"), {})
    )
    claimed = scoring.normalize_list(
        _load_jsonish(row.get("claimed_caps"), capabilities + specialties),
        scoring.CAP_VOCAB + scoring.SPEC_VOCAB,
    )
    extracted_fields = {
        "facility_type": row.get("facility_type"),
        "ownership": row.get("ownership"),
        "summary": row.get("summary"),
        "capabilities": capabilities,
        "specialties": specialties,
        "equipment": _load_jsonish(row.get("equipment"), []),
        "services": _load_jsonish(row.get("services"), []),
        "key_procedures": _load_jsonish(row.get("key_procedures"), []),
    }

    return {
        "facility_id": row["facility_id"],
        "name": row.get("name"),
        "district": row.get("district") or "Unknown",
        "state": row.get("state"),
        "pincode": str(row.get("pincode")) if row.get("pincode") is not None else None,
        "latitude": _as_float(row.get("latitude")),
        "longitude": _as_float(row.get("longitude")),
        "facility_type": row.get("facility_type"),
        "ownership": row.get("ownership"),
        "summary": row.get("summary"),
        "raw_text": row.get("raw_text"),
        "capabilities": capabilities,
        "specialties": specialties,
        "claimed_capabilities": sorted(set(claimed or capabilities + specialties)),
        "equipment": _load_jsonish(row.get("equipment"), []),
        "services": _load_jsonish(row.get("services"), []),
        "key_procedures": _load_jsonish(row.get("key_procedures"), []),
        "extracted_fields": extracted_fields,
        "confidence": confidence,
        "evidence": evidence,
        "trust_score": trust_score,
        "trust_bucket": row.get("trust_bucket"),
        "trust_flags": _trust_flags(row),
        "doctors": _as_int(row.get("doctors")),
        "beds": _as_int(row.get("beds")),
        "year_established": _as_int(row.get("year_established")),
    }


def fetch_facility_rows(limit=None, facility_id=None):
    columns = _table_columns(FACILITY_APP)
    required = ["facility_id", "name", "district"]
    missing = [c for c in required if c not in columns]
    if missing:
        raise RuntimeError(f"{FACILITY_APP} is missing required columns: {missing}")

    optional = [
        "state", "pincode", "latitude", "longitude", "facility_type", "ownership",
        "summary", "capabilities", "specialties", "equipment", "services",
        "key_procedures", "confidence", "confidence_by_field", "evidence",
        "evidence_snippets", "field_evidence", "claimed_caps", "trust_bucket",
        "trust_flags", "n_contradictions", "doctors", "beds", "year_established",
    ]
    select_list = [
        "f.facility_id AS facility_id",
        "f.name AS name",
        "f.district AS district",
        *[_select_expr(columns, c) for c in optional],
    ]

    if "raw_text" in columns:
        select_list.append("raw_text")
        raw_join = ""
    else:
        raw_join = f"""
        LEFT JOIN (
            SELECT unique_id AS raw_facility_id,
                   concat_ws(' . ', description, capability, procedure, equipment, specialties) AS raw_text
            FROM {RAW_FACILITIES}
        ) raw ON f.facility_id = raw.raw_facility_id
        """
        select_list.append("raw.raw_text AS raw_text")

    conditions = ["f.facility_id IS NOT NULL"]
    params = {}
    if facility_id:
        conditions.append("f.facility_id = :facility_id")
        params["facility_id"] = facility_id

    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    _, rows = warehouse.run_sql(
        f"""
        SELECT {", ".join(select_list)}
        FROM {FACILITY_APP} f
        {raw_join}
        WHERE {" AND ".join(conditions)}
        {limit_clause}
        """,
        params,
    )
    return [_normalize_facility(row) for row in rows]


def fetch_demand_rows():
    columns = _table_columns(DEMAND_TABLE)
    required = [NFHS_COLUMNS["district"]]
    missing = [c for c in required if c.lower() not in columns]
    if missing:
        raise RuntimeError(f"{DEMAND_TABLE} is missing required columns: {missing}")

    def col(name, alias=None):
        alias = alias or name
        source = NFHS_COLUMNS[name]
        if source.lower() in columns:
            return f"{source} AS {alias}"
        return f"NULL AS {alias}"

    _, rows = warehouse.run_sql(
        f"""
        SELECT
            {col("district", "district")},
            {col("state", "state")},
            {col("institutional_birth", "institutional_birth_pct")},
            {col("stunting", "stunting_pct")},
            {col("anaemia_women", "anaemia_women_pct")},
            {col("oop_delivery", "oop_delivery")}
        FROM {DEMAND_TABLE}
        WHERE {NFHS_COLUMNS["district"]} IS NOT NULL
        """
    )

    out = []
    by_district = {}
    for row in rows:
        district = row.get("district")
        if not district:
            continue
        inst_birth = _as_float(row.get("institutional_birth_pct"))
        stunting = _as_float(row.get("stunting_pct"))
        anaemia = _as_float(row.get("anaemia_women_pct"))
        oop = _as_float(row.get("oop_delivery"))

        indicators = {
            "institutional_birth_pct": inst_birth,
            "stunting_pct": stunting,
            "anaemia_women_pct": anaemia,
            "oop_delivery": oop,
        }
        demand_parts = [
            _bounded_0_1((100.0 - inst_birth) / 100.0) if inst_birth is not None else None,
            _bounded_0_1(stunting / 100.0) if stunting is not None else None,
            _bounded_0_1(anaemia / 100.0) if anaemia is not None else None,
        ]
        demand_score = round(_avg_present(demand_parts), 3)
        current = by_district.get(district)
        if current is None or demand_score > current["demand_score"]:
            by_district[district] = {
                "district": district,
                "state": row.get("state"),
                "population": None,
                "demand_score": demand_score,
                "demand_indicators": indicators,
            }

    out.extend(by_district.values())
    return out


def fetch_pincode_state_map():
    _, rows = warehouse.run_sql(
        f"""
        SELECT
            regexp_extract(cast(pincode AS string), '(\\\\d{{6}})', 1) AS pincode,
            max(statename) AS state,
            max(district) AS district
        FROM {PINCODE_DIRECTORY}
        WHERE pincode IS NOT NULL
        GROUP BY regexp_extract(cast(pincode AS string), '(\\\\d{{6}})', 1)
        """
    )
    return {
        str(row["pincode"]): {
            "state": row.get("state"),
            "district": row.get("district"),
        }
        for row in rows
        if row.get("pincode")
    }


def apply_pincode_backfill(rows, pincode_map):
    for row in rows:
        pin = row.get("pincode")
        ref = pincode_map.get(str(pin)) if pin else None
        if ref:
            if ref.get("state"):
                row["state"] = ref["state"]
            if (not row.get("district") or row.get("district") == "Unknown") and ref.get("district"):
                row["district"] = ref["district"]
        if row.get("state"):
            row["state"] = str(row["state"]).upper()
        if row.get("district"):
            row["district"] = str(row["district"]).upper()
    return rows


def sync(limit=None):
    print("Ensuring annotation table...", flush=True)
    annotation_agent.ensure_annotation_table()
    print("Ensuring Lakebase UI tables...", flush=True)
    lakebase_ui.ensure_ui_tables()
    print("Reading facility_app from SQL Warehouse...", flush=True)
    rows = fetch_facility_rows(limit=limit)
    print(f"Fetched {len(rows)} facilities.", flush=True)
    print("Backfilling district/state from pincode reference...", flush=True)
    rows = apply_pincode_backfill(rows, fetch_pincode_state_map())
    print("Reading NFHS demand indicators from SQL Warehouse...", flush=True)
    demand_rows = fetch_demand_rows()
    print(f"Fetched {len(demand_rows)} demand rows.", flush=True)
    print("Upserting demand indicators into Lakebase...", flush=True)
    lakebase_ui.upsert_demand(demand_rows)
    print("Upserting facilities into Lakebase...", flush=True)
    lakebase_ui.upsert_facilities(rows)
    print("Refreshing district/capability scores...", flush=True)
    lakebase_ui.refresh_districts_and_scores(CAPABILITIES)
    print("Lakebase UI sync complete.", flush=True)
    return len(rows)


def sync_facility(facility_id):
    """Refresh one facility from facility_app into Lakebase and recompute scores.

    This is the fast path used after a planner-triggered reclassification. The
    analytical source of truth stays in Databricks; Lakebase is only refreshed
    for the affected serving row plus district/capability aggregates.
    """
    annotation_agent.ensure_annotation_table()
    lakebase_ui.ensure_ui_tables()
    rows = fetch_facility_rows(facility_id=facility_id)
    if not rows:
        raise RuntimeError(f"No facility found in {FACILITY_APP}: {facility_id}")
    rows = apply_pincode_backfill(rows, fetch_pincode_state_map())
    lakebase_ui.upsert_facilities(rows)
    lakebase_ui.refresh_districts_and_scores(CAPABILITIES)
    return rows[0]


if __name__ == "__main__":
    limit = os.getenv("SYNC_LIMIT")
    count = sync(limit=int(limit) if limit else None)
    print(f"Synced {count} facilities into Lakebase UI tables.")
