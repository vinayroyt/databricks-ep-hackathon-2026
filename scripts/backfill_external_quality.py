"""Optional trusted-API enrichment for UI data gaps.

Run after `scripts/sync_lakebase_ui.py`. This fills Lakebase UI-serving gaps,
not the raw source tables. Results are cached in cg_geocode_cache with provider
provenance, then copied into cg_facilities/cg_districts only when coordinates
are missing.

Default provider: OpenStreetMap Nominatim. Respect its public API limits:
one request per second, cache results, and use a descriptive user agent.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(ROOT, "agents")
if AGENTS not in sys.path:
    sys.path.insert(0, AGENTS)

import lakebase_ui
from lakebase import get_connection


USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "care-gap-atlas/0.1 contact=vinayroyt@gmail.com")
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _query_key(text):
    return "nominatim:" + " ".join(text.lower().split())


def _fetch_nominatim(query):
    params = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "in"})
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload:
        return None
    best = payload[0]
    return {
        "latitude": float(best["lat"]),
        "longitude": float(best["lon"]),
        "confidence": best.get("importance"),
        "raw_response": best,
    }


def _cached_lookup(conn, query):
    key = _query_key(query)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT latitude, longitude FROM {lakebase_ui.UI_GEOCODE_CACHE} WHERE query_key = %s",
            (key,),
        )
        row = cur.fetchone()
        if row:
            return row[0], row[1], True

    result = _fetch_nominatim(query)
    time.sleep(1.1)
    if result is None:
        result = {"latitude": None, "longitude": None, "confidence": None, "raw_response": {}}

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {lakebase_ui.UI_GEOCODE_CACHE} (
                query_key, query_text, provider, latitude, longitude, confidence, raw_response, updated_at
            )
            VALUES (%s, %s, 'nominatim', %s, %s, %s, %s::jsonb, now())
            ON CONFLICT (query_key) DO UPDATE SET
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                confidence = EXCLUDED.confidence,
                raw_response = EXCLUDED.raw_response,
                updated_at = now()
            """,
            (
                key,
                query,
                result["latitude"],
                result["longitude"],
                None if result["confidence"] is None else str(result["confidence"]),
                json.dumps(result["raw_response"]),
            ),
        )
    conn.commit()
    return result["latitude"], result["longitude"], False


def backfill_district_coordinates(limit=50):
    lakebase_ui.ensure_ui_tables()
    conn = get_connection()
    updated = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT district, state
                FROM {lakebase_ui.UI_DISTRICTS}
                WHERE latitude IS NULL OR longitude IS NULL
                ORDER BY facility_count DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

        for district, state in rows:
            query = ", ".join([p for p in (district, state, "India") if p])
            lat, lon, cached = _cached_lookup(conn, query)
            if lat is None or lon is None:
                print(f"no geocode: {query}")
                continue
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {lakebase_ui.UI_DISTRICTS}
                    SET latitude = COALESCE(latitude, %s),
                        longitude = COALESCE(longitude, %s),
                        updated_at = now()
                    WHERE district = %s
                    """,
                    (lat, lon, district),
                )
            conn.commit()
            updated += 1
            print(f"{'cached' if cached else 'geocoded'} district: {query} -> {lat}, {lon}")
    finally:
        conn.close()
    return updated


def backfill_facility_coordinates(limit=50):
    lakebase_ui.ensure_ui_tables()
    conn = get_connection()
    updated = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT facility_id, name, district, state, pincode
                FROM {lakebase_ui.UI_FACILITIES}
                WHERE latitude IS NULL OR longitude IS NULL
                ORDER BY trust_score DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

        for facility_id, name, district, state, pincode in rows:
            query = ", ".join([p for p in (name, district, state, pincode, "India") if p])
            lat, lon, cached = _cached_lookup(conn, query)
            if lat is None or lon is None:
                print(f"no geocode: {query}")
                continue
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {lakebase_ui.UI_FACILITIES}
                    SET latitude = COALESCE(latitude, %s),
                        longitude = COALESCE(longitude, %s),
                        updated_at = now()
                    WHERE facility_id = %s
                    """,
                    (lat, lon, facility_id),
                )
            conn.commit()
            updated += 1
            print(f"{'cached' if cached else 'geocoded'} facility: {facility_id} -> {lat}, {lon}")
    finally:
        conn.close()
    return updated


if __name__ == "__main__":
    limit = int(os.getenv("GEOCODE_LIMIT", "50"))
    d = backfill_district_coordinates(limit=limit)
    f = backfill_facility_coordinates(limit=limit)
    print(f"Backfilled {d} districts and {f} facilities.")
