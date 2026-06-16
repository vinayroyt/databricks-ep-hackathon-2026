"""Lakebase UI-serving tables for the Care Gap Atlas Databricks App.

Databricks warehouse tables remain the analytical source of truth. These
Lakebase tables are a small read-optimized cache for the app: districts,
flattened facilities, per-capability district scores, and planner annotations.
"""
import json
from lakebase import get_connection


UI_FACILITIES = "cg_facilities"
UI_DISTRICTS = "cg_districts"
UI_SCORES = "cg_district_capability_scores"
UI_DEMAND = "cg_demand_reference"
UI_GEOCODE_CACHE = "cg_geocode_cache"


def _clean_value(value):
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, dict):
        return {
            _clean_value(key): _clean_value(val)
            for key, val in value.items()
        }
    return value


def _clean_row(row):
    return {key: _clean_value(value) for key, value in row.items()}


def _json(value):
    if value is None:
        return None
    value = _clean_value(value)
    return json.dumps(value)


DISTRICT_KEY_SQL = "upper(regexp_replace(district, '[^A-Za-z0-9]+', '', 'g'))"


def ensure_ui_tables():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {UI_FACILITIES} (
                    facility_id TEXT PRIMARY KEY,
                    name TEXT,
                    district TEXT NOT NULL,
                    state TEXT,
                    pincode TEXT,
                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,
                    facility_type TEXT,
                    ownership TEXT,
                    summary TEXT,
                    raw_text TEXT,
                    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                    specialties JSONB NOT NULL DEFAULT '[]'::jsonb,
                    claimed_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                    equipment JSONB NOT NULL DEFAULT '[]'::jsonb,
                    services JSONB NOT NULL DEFAULT '[]'::jsonb,
                    key_procedures JSONB NOT NULL DEFAULT '[]'::jsonb,
                    extracted_fields JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    confidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    trust_score DOUBLE PRECISION,
                    trust_bucket TEXT,
                    trust_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
                    doctors INTEGER,
                    beds INTEGER,
                    year_established INTEGER,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {UI_DISTRICTS} (
                    district TEXT PRIMARY KEY,
                    state TEXT,
                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,
                    facility_count INTEGER NOT NULL DEFAULT 0,
                    population DOUBLE PRECISION,
                    demand_score DOUBLE PRECISION,
                    demand_indicators JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(f"ALTER TABLE {UI_DISTRICTS} ADD COLUMN IF NOT EXISTS demand_score DOUBLE PRECISION")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {UI_SCORES} (
                    district TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    total_facilities INTEGER NOT NULL,
                    claimed_facilities INTEGER NOT NULL,
                    verified_facilities INTEGER NOT NULL,
                    low_trust_facilities INTEGER NOT NULL,
                    avg_confidence DOUBLE PRECISION,
                    demand_score DOUBLE PRECISION,
                    gap_score DOUBLE PRECISION NOT NULL,
                    why TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (district, capability)
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {UI_DEMAND} (
                    district TEXT PRIMARY KEY,
                    district_key TEXT,
                    state TEXT,
                    population DOUBLE PRECISION,
                    demand_score DOUBLE PRECISION,
                    demand_indicators JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(f"ALTER TABLE {UI_DEMAND} ADD COLUMN IF NOT EXISTS district_key TEXT")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {UI_GEOCODE_CACHE} (
                    query_key TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,
                    confidence TEXT,
                    raw_response JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{UI_FACILITIES}_district ON {UI_FACILITIES}(district)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{UI_SCORES}_capability ON {UI_SCORES}(capability)")
        conn.commit()
    finally:
        conn.close()


def upsert_demand(rows):
    ensure_ui_tables()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for row in rows:
                row = _clean_row(row)
                cur.execute(
                    f"""
                    INSERT INTO {UI_DEMAND} (
                        district, district_key, state, population, demand_score, demand_indicators, updated_at
                    )
                    VALUES (
                        %(district)s, upper(regexp_replace(%(district)s, '[^A-Za-z0-9]+', '', 'g')),
                        %(state)s, %(population)s, %(demand_score)s,
                        %(demand_indicators)s::jsonb, now()
                    )
                    ON CONFLICT (district) DO UPDATE SET
                        district_key = EXCLUDED.district_key,
                        state = EXCLUDED.state,
                        population = EXCLUDED.population,
                        demand_score = EXCLUDED.demand_score,
                        demand_indicators = EXCLUDED.demand_indicators,
                        updated_at = now()
                    """,
                    {**row, "demand_indicators": _json(row.get("demand_indicators"))},
                )
        conn.commit()
    finally:
        conn.close()


def upsert_facilities(rows):
    ensure_ui_tables()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for row in rows:
                row = _clean_row(row)
                cur.execute(
                    f"""
                    INSERT INTO {UI_FACILITIES} (
                        facility_id, name, district, state, pincode, latitude, longitude,
                        facility_type, ownership, summary, raw_text, capabilities, specialties,
                        claimed_capabilities, equipment, services, key_procedures,
                        extracted_fields, confidence, evidence, trust_score, trust_bucket,
                        trust_flags, doctors, beds, year_established, updated_at
                    )
                    VALUES (
                        %(facility_id)s, %(name)s, %(district)s, %(state)s, %(pincode)s,
                        %(latitude)s, %(longitude)s, %(facility_type)s, %(ownership)s,
                        %(summary)s, %(raw_text)s, %(capabilities)s::jsonb,
                        %(specialties)s::jsonb, %(claimed_capabilities)s::jsonb,
                        %(equipment)s::jsonb, %(services)s::jsonb, %(key_procedures)s::jsonb,
                        %(extracted_fields)s::jsonb, %(confidence)s::jsonb, %(evidence)s::jsonb,
                        %(trust_score)s, %(trust_bucket)s, %(trust_flags)s::jsonb,
                        %(doctors)s, %(beds)s, %(year_established)s, now()
                    )
                    ON CONFLICT (facility_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        district = EXCLUDED.district,
                        state = EXCLUDED.state,
                        pincode = EXCLUDED.pincode,
                        latitude = EXCLUDED.latitude,
                        longitude = EXCLUDED.longitude,
                        facility_type = EXCLUDED.facility_type,
                        ownership = EXCLUDED.ownership,
                        summary = EXCLUDED.summary,
                        raw_text = EXCLUDED.raw_text,
                        capabilities = EXCLUDED.capabilities,
                        specialties = EXCLUDED.specialties,
                        claimed_capabilities = EXCLUDED.claimed_capabilities,
                        equipment = EXCLUDED.equipment,
                        services = EXCLUDED.services,
                        key_procedures = EXCLUDED.key_procedures,
                        extracted_fields = EXCLUDED.extracted_fields,
                        confidence = EXCLUDED.confidence,
                        evidence = EXCLUDED.evidence,
                        trust_score = EXCLUDED.trust_score,
                        trust_bucket = EXCLUDED.trust_bucket,
                        trust_flags = EXCLUDED.trust_flags,
                        doctors = EXCLUDED.doctors,
                        beds = EXCLUDED.beds,
                        year_established = EXCLUDED.year_established,
                        updated_at = now()
                    """,
                    {**row, **{k: _json(row.get(k)) for k in (
                        "capabilities", "specialties", "claimed_capabilities", "equipment",
                        "services", "key_procedures", "extracted_fields", "confidence",
                        "evidence", "trust_flags",
                    )}},
                )
        conn.commit()
    finally:
        conn.close()


def refresh_districts_and_scores(capabilities):
    """Recompute district rows and selected-capability gap scores inside Lakebase."""
    ensure_ui_tables()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {UI_DISTRICTS} (
                    district, state, latitude, longitude, facility_count,
                    population, demand_score, demand_indicators, updated_at
                )
                SELECT
                    f.district,
                    coalesce(max(f.state), max(d.state)),
                    avg(f.latitude),
                    avg(f.longitude),
                    count(*),
                    max(d.population),
                    coalesce(max(d.demand_score), 0.5),
                    coalesce((jsonb_agg(d.demand_indicators) FILTER (WHERE d.demand_indicators IS NOT NULL))->0, '{{}}'::jsonb),
                    now()
                FROM {UI_FACILITIES} f
                LEFT JOIN {UI_DEMAND} d
                  ON upper(regexp_replace(f.district, '[^A-Za-z0-9]+', '', 'g')) = d.district_key
                GROUP BY f.district
                ON CONFLICT (district) DO UPDATE SET
                    state = EXCLUDED.state,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    facility_count = EXCLUDED.facility_count,
                    population = EXCLUDED.population,
                    demand_score = EXCLUDED.demand_score,
                    demand_indicators = EXCLUDED.demand_indicators,
                    updated_at = now()
                """
            )
            for capability in capabilities:
                cur.execute(
                    f"""
                    WITH facility_scores AS (
                        SELECT
                            district,
                            count(*) AS total_facilities,
                            count(*) FILTER (
                                WHERE capabilities ? %(capability)s
                                   OR claimed_capabilities ? %(capability)s
                            ) AS claimed_facilities,
                            count(*) FILTER (
                                WHERE (capabilities ? %(capability)s)
                                  AND coalesce(trust_bucket, '') IN ('Verified', 'Plausible')
                                  AND coalesce(trust_score, 0) >= 0.5
                            ) AS verified_facilities,
                            count(*) FILTER (
                                WHERE (capabilities ? %(capability)s OR claimed_capabilities ? %(capability)s)
                                  AND (
                                      coalesce(trust_bucket, '') IN ('Contradicted', 'Unverified')
                                      OR coalesce(trust_score, 0) < 0.5
                                      OR jsonb_array_length(trust_flags) > 0
                                  )
                            ) AS low_trust_facilities,
                            avg(trust_score) FILTER (
                                WHERE capabilities ? %(capability)s OR claimed_capabilities ? %(capability)s
                            ) AS avg_confidence
                        FROM {UI_FACILITIES}
                        GROUP BY district
                    ),
                    scored AS (
                        SELECT
                            f.*,
                            coalesce(d.demand_score, 0.5) AS demand_score,
                            (1.0 - least(1.0, f.verified_facilities::numeric / greatest(1, f.total_facilities))) AS supply_gap
                        FROM facility_scores f
                        LEFT JOIN {UI_DEMAND} d
                          ON upper(regexp_replace(f.district, '[^A-Za-z0-9]+', '', 'g')) = d.district_key
                    )
                    INSERT INTO {UI_SCORES} (
                        district, capability, total_facilities, claimed_facilities,
                        verified_facilities, low_trust_facilities, avg_confidence,
                        demand_score, gap_score, why, updated_at
                    )
                    SELECT
                        district,
                        %(capability)s,
                        total_facilities,
                        claimed_facilities,
                        verified_facilities,
                        low_trust_facilities,
                        avg_confidence,
                        demand_score,
                        round(((0.65 * supply_gap) + (0.35 * demand_score))::numeric, 3),
                        claimed_facilities || ' of ' || total_facilities || ' facilities claim ' ||
                            %(capability_label)s || ' capability, ' || verified_facilities ||
                            ' have verified evidence. Demand need is ' ||
                            round(demand_score::numeric, 2) || '.',
                        now()
                    FROM scored
                    ON CONFLICT (district, capability) DO UPDATE SET
                        total_facilities = EXCLUDED.total_facilities,
                        claimed_facilities = EXCLUDED.claimed_facilities,
                        verified_facilities = EXCLUDED.verified_facilities,
                        low_trust_facilities = EXCLUDED.low_trust_facilities,
                        avg_confidence = EXCLUDED.avg_confidence,
                        demand_score = EXCLUDED.demand_score,
                        gap_score = EXCLUDED.gap_score,
                        why = EXCLUDED.why,
                        updated_at = now()
                    """,
                    {"capability": capability, "capability_label": capability.replace("_", " ")},
                )
        conn.commit()
    finally:
        conn.close()
