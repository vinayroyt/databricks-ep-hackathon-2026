import json
import os
import sys
from datetime import datetime

import pandas as pd
import pydeck as pdk
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(ROOT, "agents")
SCRIPTS = os.path.join(ROOT, "scripts")
if AGENTS not in sys.path:
    sys.path.insert(0, AGENTS)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import annotation_agent
import lakebase_ui
import mock_data
import reclassification_agent
import sync_lakebase_ui
from lakebase import get_connection


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

FLAG_OPTIONS = {
    "No flag": None,
    "Looks good": "looks_good",
    "Wrong": "data_wrong",
    "Capability incorrect": "incorrect_capability",
    "Capability missing": "missing_capability",
}

TRUST_TONE = {
    "Verified": "good",
    "Plausible": "good",
    "Unverified": "warn",
    "Contradicted": "bad",
}


APP_NAME = "VeriCare Map"

st.set_page_config(page_title=APP_NAME, page_icon=None, layout="wide")

st.markdown(
    """
    <style>
    :root {
      --ink: #18212f;
      --muted: #5b6677;
      --line: #d7dde6;
      --band: #f6f8fb;
      --good: #1f7a4d;
      --warn: #9a6500;
      --bad: #b42336;
      --blue: #2457a7;
    }
    .block-container { padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1480px; }
    h1, h2, h3 { letter-spacing: 0; color: var(--ink); }
    div[data-testid="stMetric"] {
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      box-shadow: 0 1px 2px rgba(24, 33, 47, 0.05);
    }
    div[data-testid="stMetric"] label { color: var(--muted); }
    .hero {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px 18px;
      background: linear-gradient(90deg, #ffffff 0%, #f7fafc 100%);
      margin-bottom: 12px;
    }
    .hero-title { font-size: 30px; font-weight: 760; color: var(--ink); line-height: 1.1; }
    .hero-subtitle { color: var(--muted); margin-top: 4px; font-size: 14px; }
    .source-badge {
      display: inline-block;
      padding: 4px 9px;
      border-radius: 999px;
      color: #ffffff;
      font-size: 12px;
      font-weight: 650;
      margin-left: 8px;
      vertical-align: middle;
    }
    .source-live { background: var(--good); }
    .source-demo { background: var(--warn); }
    .section-label {
      margin: 8px 0 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 720;
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .facility-row {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      background: #ffffff;
      margin-bottom: 10px;
    }
    .queue-row {
      border-bottom: 1px solid var(--line);
      padding: 8px 2px;
      font-size: 13px;
    }
    .queue-name { font-weight: 700; color: var(--ink); }
    .queue-meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
    .flag {
      display: inline-block;
      border-radius: 999px;
      padding: 3px 8px;
      margin: 2px 4px 2px 0;
      font-size: 12px;
      color: #ffffff;
      font-weight: 650;
    }
    .flag-good { background: var(--good); }
    .flag-warn { background: var(--warn); }
    .flag-bad { background: var(--bad); }
    .flag-neutral { background: #526071; }
    .evidence {
      border-left: 3px solid #8aa4d6;
      padding: 5px 0 5px 9px;
      color: #394457;
      background: #f8faff;
      margin: 5px 0;
      font-size: 13px;
    }
    .why {
      border: 1px solid #d7dde6;
      border-radius: 8px;
      padding: 10px 12px;
      background: #fbfcfe;
      color: #253247;
    }
    .priority-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: #ffffff;
      margin: 6px 0;
    }
    .priority-title { font-weight: 760; color: var(--ink); }
    .priority-reason { color: var(--muted); font-size: 13px; margin-top: 3px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _dict_rows(cur):
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _query(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return _dict_rows(cur)
    finally:
        conn.close()


def _mock_scores(capability):
    rows = []
    for region in mock_data.get_regions():
        rows.append({
            "district": region["region_name"],
            "state": "Karnataka",
            "capability": capability,
            "total_facilities": 3 if region["region_id"] == "R01" else 1,
            "claimed_facilities": region.get("claimed_icu_facilities", 0),
            "verified_facilities": region.get("verified_icu_facilities", 0),
            "low_trust_facilities": 2 if region["region_id"] == "R01" else 0,
            "avg_confidence": 42 if region["region_id"] == "R01" else 91,
            "gap_score": region["gap_score"],
            "demand_score": 0.72 if region["region_id"] == "R01" else 0.35,
            "why": region["summary"],
            "latitude": 17.9 if region["region_id"] == "R01" else 18.25,
            "longitude": 77.5 if region["region_id"] == "R01" else 77.42,
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=60)
def load_overview():
    try:
        rows = _query(
            f"""
            SELECT
                count(*) AS facilities,
                count(DISTINCT district) AS districts,
                count(DISTINCT state) AS states,
                count(*) FILTER (
                    WHERE coalesce(trust_bucket, '') IN ('Contradicted', 'Unverified')
                       OR coalesce(trust_score, 0) < 0.5
                       OR jsonb_array_length(trust_flags) > 0
                ) AS low_trust_facilities,
                max(updated_at) AS updated_at
            FROM {lakebase_ui.UI_FACILITIES}
            """
        )[0]
        rows["source"] = "Lakebase"
        rows["is_live"] = True
        return rows
    except Exception:
        return {
            "facilities": len(mock_data.FACILITIES),
            "districts": len(mock_data.REGIONS),
            "states": 1,
            "low_trust_facilities": 2,
            "updated_at": None,
            "source": "Demo fallback; Lakebase UI tables not synced",
            "is_live": False,
        }


@st.cache_data(ttl=60)
def load_states():
    try:
        rows = _query(
            f"""
            SELECT state, count(*) AS facilities
            FROM {lakebase_ui.UI_FACILITIES}
            WHERE state IS NOT NULL AND state <> '' AND upper(state) <> 'UNKNOWN'
            GROUP BY state
            ORDER BY facilities DESC, state
            """
        )
        states = [r["state"] for r in rows]
        return states or ["All states"]
    except Exception:
        return ["All states", "Karnataka"]


@st.cache_data(ttl=60)
def load_scores(capability, state):
    try:
        state_filter = "" if state == "All states" else "AND coalesce(d.state, 'Unknown') = %(state)s"
        params = {"capability": capability}
        if state != "All states":
            params["state"] = state
        return pd.DataFrame(_query(
            f"""
            SELECT s.*, d.state, d.latitude, d.longitude, d.population
            FROM {lakebase_ui.UI_SCORES} s
            LEFT JOIN {lakebase_ui.UI_DISTRICTS} d USING (district)
            WHERE s.capability = %(capability)s
              {state_filter}
            ORDER BY s.gap_score DESC, s.low_trust_facilities DESC, s.total_facilities DESC
            """,
            params,
        ))
    except Exception:
        scores = _mock_scores(capability)
        return scores if state == "All states" else scores[scores["state"] == state]


@st.cache_data(ttl=120)
def load_facilities(district, capability=None, low_trust_only=False, limit=40):
    try:
        conditions = ["district = %(district)s"]
        params = {"district": district, "limit": limit}
        if capability:
            conditions.append("(capabilities ? %(capability)s OR claimed_capabilities ? %(capability)s)")
            params["capability"] = capability
        if low_trust_only:
            conditions.append(
                """
                (
                  coalesce(trust_bucket, '') IN ('Contradicted', 'Unverified')
                  OR coalesce(trust_score, 0) < 0.5
                  OR jsonb_array_length(trust_flags) > 0
                )
                """
            )
        rows = _query(
            f"""
            SELECT *
            FROM {lakebase_ui.UI_FACILITIES}
            WHERE {" AND ".join(conditions)}
            ORDER BY
                CASE WHEN coalesce(trust_bucket, '') IN ('Contradicted', 'Unverified') THEN 0 ELSE 1 END,
                trust_score NULLS FIRST,
                name
            LIMIT %(limit)s
            """,
            params,
        )
        return [_normalize_facility(row) for row in rows]
    except Exception:
        region = mock_data.get_region(district)
        if not region:
            region = next((r for r in mock_data.get_regions() if r["region_name"] == district), None)
        rows = []
        for f in mock_data.get_facilities(region["region_id"] if region else district):
            rows.append({
                "facility_id": f["facility_id"],
                "name": f["name"],
                "district": district,
                "summary": f["raw_text"],
                "capabilities": f["extracted"].get("specialties", []),
                "specialties": f["extracted"].get("specialties", []),
                "claimed_capabilities": f["extracted"].get("specialties", []),
                "equipment": f["extracted"].get("equipment", []),
                "services": [],
                "key_procedures": [],
                "extracted_fields": f["extracted"],
                "confidence": f["extracted"].get("confidence", {}),
                "evidence": f["extracted"].get("evidence", {}),
                "trust_score": f["trust_score"],
                "trust_bucket": "Verified" if f["trust_score"] >= 0.75 else "Contradicted",
                "trust_flags": f["trust_flags"],
                "raw_text": f["raw_text"],
            })
        if capability:
            rows = [r for r in rows if capability.lower() in [c.lower() for c in r["claimed_capabilities"]]]
        if low_trust_only:
            rows = [r for r in rows if r["trust_bucket"] != "Verified" or r["trust_flags"]]
        return rows


@st.cache_data(ttl=120)
def load_review_queue(capability, state, limit=6):
    try:
        state_filter = "" if state == "All states" else "AND coalesce(state, 'Unknown') = %(state)s"
        params = {"capability": capability, "limit": limit}
        if state != "All states":
            params["state"] = state
        rows = _query(
            f"""
            SELECT facility_id, name, district, state, trust_bucket, trust_score, trust_flags,
                   claimed_capabilities, capabilities
            FROM {lakebase_ui.UI_FACILITIES}
            WHERE (capabilities ? %(capability)s OR claimed_capabilities ? %(capability)s)
              AND (
                coalesce(trust_bucket, '') IN ('Contradicted', 'Unverified')
                OR coalesce(trust_score, 0) < 0.5
                OR jsonb_array_length(trust_flags) > 0
              )
              {state_filter}
            ORDER BY trust_score NULLS FIRST, name
            LIMIT %(limit)s
            """,
            params,
        )
        for row in rows:
            row["trust_flags"] = _json(row.get("trust_flags"), [])
            row["claimed_capabilities"] = _json(row.get("claimed_capabilities"), [])
            row["capabilities"] = _json(row.get("capabilities"), [])
        return rows
    except Exception:
        return [
            {
                "facility_id": f["facility_id"],
                "name": f["name"],
                "district": "Bidar",
                "state": "Karnataka",
                "trust_bucket": "Contradicted",
                "trust_score": f["trust_score"],
                "trust_flags": f["trust_flags"],
                "claimed_capabilities": f["extracted"].get("specialties", []),
                "capabilities": f["extracted"].get("specialties", []),
            }
            for f in mock_data.FACILITIES
            if f["trust_flags"]
        ]


@st.cache_data(ttl=120)
def load_demo_insights(capability, state):
    try:
        state_filter_scores = "" if state == "All states" else "AND coalesce(d.state, 'Unknown') = %(state)s"
        state_filter_facilities = "" if state == "All states" else "AND coalesce(state, 'Unknown') = %(state)s"
        state_filter_districts = "" if state == "All states" else "AND coalesce(state, 'Unknown') = %(state)s"
        params = {"capability": capability, "state": state}

        top_gap = _query(
            f"""
            SELECT s.district, d.state, s.gap_score, s.demand_score,
                   s.verified_facilities, s.total_facilities
            FROM {lakebase_ui.UI_SCORES} s
            LEFT JOIN {lakebase_ui.UI_DISTRICTS} d USING (district)
            WHERE s.capability = %(capability)s
              {state_filter_scores}
              AND s.district IS NOT NULL
              AND upper(s.district) <> 'UNKNOWN'
            ORDER BY s.gap_score DESC
            LIMIT 1
            """,
            params,
        )
        counts = _query(
            f"""
            SELECT
                count(*) AS facilities,
                count(*) FILTER (
                    WHERE coalesce(trust_bucket, '') IN ('Contradicted', 'Unverified')
                       OR coalesce(trust_score, 0) < 0.5
                       OR jsonb_array_length(trust_flags) > 0
                ) AS needs_review,
                count(*) FILTER (WHERE district IS NULL OR upper(district) = 'UNKNOWN') AS unknown_districts
            FROM {lakebase_ui.UI_FACILITIES}
            WHERE 1=1 {state_filter_facilities}
            """,
            params,
        )[0]
        demand = _query(
            f"""
            SELECT count(*) AS districts_with_demand
            FROM {lakebase_ui.UI_DISTRICTS}
            WHERE demand_score IS NOT NULL
              AND district IS NOT NULL
              AND upper(district) <> 'UNKNOWN'
              {state_filter_districts}
            """,
            params,
        )[0]

        insights = []
        if top_gap:
            row = top_gap[0]
            insights.append(
                f"{row['district']} is the first place to discuss for {capability.replace('_', ' ')}: "
                f"gap {float(row['gap_score']):.2f}, need {float(row.get('demand_score') or 0.5):.2f}, "
                f"{int(row['verified_facilities'])} verified of {int(row['total_facilities'])} facilities."
            )
        insights.append(
            f"{int(counts['needs_review'])} of {int(counts['facilities'])} synced facilities need review because claims are weak, contradictory, or low confidence."
        )
        insights.append(
            f"Demand is matched for {int(demand['districts_with_demand'])} districts, so the map prioritizes need plus verified supply."
        )
        if int(counts["unknown_districts"]) > 0:
            insights.append(
                f"{int(counts['unknown_districts'])} facilities still need district cleanup; pincode and geocoding backfills are the next pass."
            )
        return insights
    except Exception:
        return [
            "The map combines demand with verified supply, not raw facility counts.",
            "Low-trust facilities become a follow-up queue instead of silently inflating access.",
            "Planner flags feed the next reclassification pass, so field feedback improves the dataset.",
        ]


def _normalize_facility(row):
    for key, default in (
        ("capabilities", []),
        ("specialties", []),
        ("claimed_capabilities", []),
        ("equipment", []),
        ("services", []),
        ("key_procedures", []),
        ("extracted_fields", {}),
        ("confidence", {}),
        ("evidence", {}),
        ("trust_flags", []),
    ):
        row[key] = _json(row.get(key), default)
    return row


def load_annotations(region_id=None, facility_id=None):
    try:
        return annotation_agent.get_annotations(region_id=region_id, facility_id=facility_id)
    except Exception as exc:
        st.warning(f"Could not load annotations: {exc}")
        return []


def save_note(region_id, facility_id, note, author, human_flag):
    result = annotation_agent.save_annotation(
        region_id=region_id,
        facility_id=facility_id,
        note=note,
        author=author,
        human_flag=human_flag,
    )
    st.cache_data.clear()
    return result


def _gap_snapshot(district, capability):
    rows = _query(
        f"""
        SELECT gap_score, verified_facilities, claimed_facilities, low_trust_facilities, why
        FROM {lakebase_ui.UI_SCORES}
        WHERE district = %(district)s AND capability = %(capability)s
        """,
        {"district": district, "capability": capability},
    )
    return rows[0] if rows else {}


def recheck_facility(facility_id, district, capability, correction_note=None):
    before_gap = _gap_snapshot(district, capability)
    result = reclassification_agent.reclassify_facility(
        facility_id=facility_id,
        correction_note=correction_note.strip() if correction_note else None,
    )
    if "error" in result:
        return result

    refreshed = sync_lakebase_ui.sync_facility(facility_id)
    st.cache_data.clear()
    after_gap = _gap_snapshot(refreshed.get("district") or district, capability)
    return {
        "facility_id": facility_id,
        "facility_name": refreshed.get("name") or result.get("name"),
        "reclassification": result,
        "refreshed_facility": refreshed,
        "before_gap": before_gap,
        "after_gap": after_gap,
    }


def score_color(score):
    score = max(0.0, min(float(score or 0), 1.0))
    red = int(48 + score * 190)
    green = int(150 - score * 100)
    blue = int(74 - score * 20)
    return [red, green, blue, 205]


def tone_class(tone):
    return {
        "good": "flag-good",
        "warn": "flag-warn",
        "bad": "flag-bad",
    }.get(tone, "flag-neutral")


def badge(text, tone="neutral"):
    st.markdown(
        f"<span class='flag {tone_class(tone)}'>{text}</span>",
        unsafe_allow_html=True,
    )


def inline_badges(items, tone="neutral"):
    if not items:
        st.caption("None")
        return
    html = "".join(f"<span class='flag {tone_class(tone)}'>{str(item)}</span>" for item in items)
    st.markdown(html, unsafe_allow_html=True)


def render_map(scores, selected_district):
    map_df = scores.dropna(subset=["latitude", "longitude"]).copy()
    if map_df.empty:
        st.info("No district coordinates available yet.")
        return

    map_df["color"] = map_df["gap_score"].apply(score_color)
    map_df["radius"] = 18000 + map_df["total_facilities"].fillna(1).astype(float).clip(1, 100) * 900
    map_df["line_color"] = map_df["district"].apply(lambda d: [12, 28, 54, 255] if d == selected_district else [255, 255, 255, 120])
    view = pdk.ViewState(
        latitude=float(map_df["latitude"].mean()),
        longitude=float(map_df["longitude"].mean()),
        zoom=4.7 if len(map_df) > 20 else 6,
        pitch=0,
    )
    st.pydeck_chart(
        pdk.Deck(
            map_style=None,
            initial_view_state=view,
            layers=[
                pdk.Layer(
                    "ScatterplotLayer",
                    data=map_df,
                    get_position="[longitude, latitude]",
                    get_fill_color="color",
                    get_line_color="line_color",
                    get_line_width=1800,
                    stroked=True,
                    filled=True,
                    get_radius="radius",
                    pickable=True,
                )
            ],
            tooltip={
                "html": "<b>{district}</b><br/>Gap {gap_score}<br/>Need {demand_score}<br/>Verified {verified_facilities} / {total_facilities}<br/>{why}",
                "style": {"backgroundColor": "#18212f", "color": "white"},
            },
        ),
        width="stretch",
    )


def render_score_table(scores):
    if scores.empty:
        return
    scores = scores.copy()
    if "demand_score" not in scores.columns:
        scores["demand_score"] = 0.5
    table = scores[[
        "district", "state", "gap_score", "total_facilities",
        "claimed_facilities", "verified_facilities", "low_trust_facilities", "demand_score",
    ]].copy()
    table.columns = [
        "District", "State", "Gap", "Facilities", "Claimed",
        "Verified", "Needs review", "Need",
    ]
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "Gap": st.column_config.ProgressColumn("Gap", min_value=0, max_value=1, format="%.2f"),
            "Need": st.column_config.ProgressColumn("Need", min_value=0, max_value=1, format="%.2f"),
        },
    )


def short_reason(item):
    flags = item.get("trust_flags") or []
    if flags:
        return str(flags[0])
    bucket = item.get("trust_bucket") or "Low trust"
    if bucket == "Contradicted":
        return "Claim does not match the evidence"
    if bucket == "Unverified":
        return "Not enough evidence to trust this claim"
    return "Needs review"


def focus_queue_item(item):
    st.session_state.focus_district = item.get("district")
    st.session_state.pending_district_select = item.get("district")
    st.session_state.focus_facility_id = item.get("facility_id")
    st.session_state.show_facilities = True


def focus_district(district):
    st.session_state.focus_district = district
    st.session_state.pending_district_select = district
    st.session_state.focus_facility_id = None
    st.session_state.show_facilities = False


def render_priority_districts(scores):
    top = scores.head(3).to_dict("records")
    for idx, item in enumerate(top, start=1):
        st.markdown(
            f"<div class='priority-card'>"
            f"<div class='priority-title'>{idx}. {item.get('district')}</div>"
            f"<div class='priority-reason'>Gap {float(item.get('gap_score') or 0):.2f} | "
            f"Need {float(item.get('demand_score') or 0.5):.2f} | "
            f"{int(item.get('verified_facilities') or 0)} verified of {int(item.get('total_facilities') or 0)}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button("Review district", key=f"district_{item.get('district')}", width="stretch"):
            focus_district(item.get("district"))
            st.rerun()


def render_annotation_form(region_id, facility_id=None, key_suffix="region"):
    with st.form(f"annotation_{key_suffix}", clear_on_submit=True):
        note = st.text_area("Note", height=76, key=f"note_{key_suffix}")
        cols = st.columns([1, 1])
        author = cols[0].text_input("Author", value="TrustedPlatformUser", key=f"author_{key_suffix}")
        flag_label = cols[1].selectbox("Flag", list(FLAG_OPTIONS), key=f"flag_{key_suffix}")
        submitted = st.form_submit_button("Save")
        if submitted and note.strip():
            result = save_note(region_id, facility_id, note.strip(), author.strip(), FLAG_OPTIONS[flag_label])
            if "error" in result:
                st.error(result["error"])
            else:
                st.success("Saved.")
                st.rerun()


def render_notes(region_id, facility_id=None, limit=6):
    notes = load_annotations(region_id=region_id, facility_id=facility_id)
    if facility_id:
        notes = [n for n in notes if n.get("facility_id") == facility_id]
    else:
        notes = [n for n in notes if not n.get("facility_id")]
    for note in notes[:limit]:
        flag = f" [{note['human_flag']}]" if note.get("human_flag") else ""
        author = note.get("author") or "Unknown"
        st.caption(f"{note['created_at']} | {author}{flag}: {note['note']}")
    if not notes:
        st.caption("No notes yet.")


def render_notes_lazy(region_id, facility_id=None, limit=6, key_suffix="notes"):
    key = f"show_notes_{key_suffix}"
    if key not in st.session_state:
        st.session_state[key] = False
    if not st.session_state[key]:
        if st.button("Show notes", key=f"btn_{key}"):
            st.session_state[key] = True
            st.rerun()
        return
    render_notes(region_id, facility_id=facility_id, limit=limit)


def render_recheck_result(result):
    reclassed = result.get("reclassification", {})
    before = reclassed.get("before", {})
    after = reclassed.get("after", {})
    before_gap = result.get("before_gap") or {}
    after_gap = result.get("after_gap") or {}

    st.success("Evidence rechecked and scores refreshed.")
    cols = st.columns(3)
    cols[0].metric(
        "Trust",
        after.get("trust_bucket") or "Unknown",
        delta=f"was {before.get('trust_bucket') or 'Unknown'}",
    )
    before_conf = before.get("confidence")
    after_conf = after.get("confidence")
    cols[1].metric(
        "Confidence",
        f"{float(after_conf):.1f}" if after_conf is not None else "Unknown",
        delta=(f"{float(after_conf) - float(before_conf):+.1f}" if before_conf is not None and after_conf is not None else None),
    )
    before_score = before_gap.get("gap_score")
    after_score = after_gap.get("gap_score")
    cols[2].metric(
        "District gap",
        f"{float(after_score):.2f}" if after_score is not None else "Unknown",
        delta=(f"{float(after_score) - float(before_score):+.2f}" if before_score is not None and after_score is not None else None),
        delta_color="inverse",
    )

    summary = reclassed.get("extraction_summary")
    if summary:
        st.caption(summary)


def render_facility(facility, district, force_open=False):
    trust_bucket = facility.get("trust_bucket") or "Unknown"
    tone = TRUST_TONE.get(trust_bucket, "neutral")
    score = facility.get("trust_score")
    flags = facility.get("trust_flags") or []
    title = facility.get("name") or facility["facility_id"]
    claimed = facility.get("claimed_capabilities") or []
    verified = facility.get("capabilities") or []
    evidence = facility.get("evidence") or {}
    confidence = facility.get("confidence") or {}
    headline_reason = flags[0] if flags else ("Verified data" if trust_bucket in ("Verified", "Plausible") else "Needs review")
    evidence_text = next((v for v in evidence.values() if v), facility.get("summary") or facility.get("raw_text") or "")

    with st.expander(f"{title}  |  {trust_bucket}", expanded=force_open or bool(flags)):
        left, right = st.columns([1.7, 1])
        with left:
            st.markdown(f"<div class='section-label'>Why It Matters</div>", unsafe_allow_html=True)
            st.write(headline_reason)
            st.caption(f"{facility['facility_id']} | {facility.get('district') or district} | {facility.get('pincode') or 'no pincode'}")
        with right:
            badge(trust_bucket, tone)
            if score is not None:
                st.metric("Trust", f"{float(score):.2f}")

        if flags:
            inline_badges(flags, "bad")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("<div class='section-label'>Claimed</div>", unsafe_allow_html=True)
            inline_badges(claimed[:8], "neutral")
        with c2:
            st.markdown("<div class='section-label'>Verified</div>", unsafe_allow_html=True)
            inline_badges(verified[:8], "good" if trust_bucket in ("Verified", "Plausible") else "warn")

        if evidence_text:
            st.markdown("<div class='section-label'>Evidence</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='evidence'>{evidence_text}</div>", unsafe_allow_html=True)

        with st.expander("More details", expanded=False):
            if facility.get("beds") is not None or facility.get("doctors") is not None:
                st.caption(f"Beds: {facility.get('beds') or 'unknown'} | Doctors: {facility.get('doctors') or 'unknown'}")
            fields = facility.get("extracted_fields") or {}
            for key, value in fields.items():
                if value in (None, "", [], {}):
                    continue
                conf = confidence.get(key, confidence.get("overall"))
                st.write(f"{key}: {value}" + (f" | confidence {conf}" if conf is not None else ""))
                ev = evidence.get(key)
                if ev:
                    st.markdown(f"<div class='evidence'>{ev}</div>", unsafe_allow_html=True)

        recheck_key = f"recheck_result_{facility['facility_id']}"
        with st.expander("Recheck evidence", expanded=bool(st.session_state.get(recheck_key))):
            st.caption("Use after adding field evidence or a correction note. Scores only improve if the evidence supports it.")
            with st.form(f"recheck_{facility['facility_id']}", clear_on_submit=True):
                correction = st.text_area("Optional correction", height=72, key=f"recheck_note_{facility['facility_id']}")
                submitted = st.form_submit_button("Recheck facility")
                if submitted:
                    with st.spinner("Rechecking evidence and refreshing scores..."):
                        result = recheck_facility(
                            facility_id=facility["facility_id"],
                            district=facility.get("district") or district,
                            capability=st.session_state.get("selected_capability", CAPABILITIES[0]),
                            correction_note=correction,
                        )
                    if "error" in result:
                        st.error(result["error"])
                    else:
                        st.session_state[recheck_key] = result
                        st.rerun()

            result = st.session_state.get(recheck_key)
            if result:
                render_recheck_result(result)

        with st.expander("Planner notes", expanded=force_open):
            render_notes_lazy(district, facility_id=facility["facility_id"], key_suffix=facility["facility_id"])
            render_annotation_form(district, facility_id=facility["facility_id"], key_suffix=facility["facility_id"])


overview = load_overview()
source_class = "source-live" if overview["is_live"] else "source-demo"
source_label = "Lakebase live" if overview["is_live"] else "Demo fallback"

if "focus_district" not in st.session_state:
    st.session_state.focus_district = None
if "focus_facility_id" not in st.session_state:
    st.session_state.focus_facility_id = None
if "district_select" not in st.session_state:
    st.session_state.district_select = None
if "pending_district_select" not in st.session_state:
    st.session_state.pending_district_select = None
if "show_facilities" not in st.session_state:
    st.session_state.show_facilities = False

st.markdown(
    f"""
    <div class='hero'>
      <div class='hero-title'>{APP_NAME}
        <span class='source-badge {source_class}'>{source_label}</span>
      </div>
      <div class='hero-subtitle'>Verified capability gaps, evidence, trust flags, and planner notes in one district workflow.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    selected_capability = st.selectbox("Capability", CAPABILITIES, format_func=lambda s: s.replace("_", " ").title())
    st.session_state.selected_capability = selected_capability
    selected_state = st.selectbox("State", load_states())
    with st.expander("More filters", expanded=False):
        include_unknown = st.toggle("Show unknown districts", value=False)
    st.divider()
    st.caption(f"Source: {overview['source']}")

scores = load_scores(selected_capability, selected_state)
if not include_unknown and not scores.empty:
    scores = scores[
        scores["district"].notna()
        & (scores["district"].astype(str).str.upper() != "UNKNOWN")
    ]

if scores.empty:
    st.info("No districts match the current filters.")
    st.stop()

districts = scores["district"].dropna().tolist()
if st.session_state.pending_district_select in districts:
    st.session_state.district_select = st.session_state.pending_district_select
    st.session_state.focus_district = st.session_state.pending_district_select
    st.session_state.pending_district_select = None
elif st.session_state.focus_district in districts:
    st.session_state.district_select = st.session_state.focus_district
elif st.session_state.district_select not in districts:
    st.session_state.district_select = districts[0]

previous_district = st.session_state.district_select
selected_district = st.sidebar.selectbox("District", districts, key="district_select")
if selected_district != previous_district and selected_district != st.session_state.focus_district:
    st.session_state.focus_facility_id = None
    st.session_state.show_facilities = False
st.session_state.focus_district = selected_district
selected = scores[scores["district"] == selected_district].iloc[0]

tab_map, tab_fixes = st.tabs(["Map", "Data Fixes"])

with tab_map:
    metric_cols = st.columns(3)
    metric_cols[0].metric("Places checked", f"{int(overview['facilities']):,}")
    metric_cols[1].metric("Districts", f"{int(overview['districts']):,}")
    metric_cols[2].metric("Selected gap", f"{float(selected['gap_score']):.2f}")

    map_col, table_col = st.columns([1.3, 1])
    with map_col:
        st.subheader("Where To Look")
        render_map(scores, selected_district)
    with table_col:
        st.subheader("Start Here")
        render_priority_districts(scores)
        with st.expander("See full ranking", expanded=False):
            render_score_table(scores.head(25))

    st.markdown(f"<div class='why'>{selected.get('why') or ''}</div>", unsafe_allow_html=True)

    detail_cols = st.columns([1.15, 0.85])
    with detail_cols[0]:
        st.subheader(selected_district)
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Claimed", int(selected.get("claimed_facilities") or 0))
        d2.metric("Verified", int(selected.get("verified_facilities") or 0))
        d3.metric("Needs review", int(selected.get("low_trust_facilities") or 0))
        d4.metric("Need", f"{float(selected.get('demand_score') or 0.5):.2f}")
        with st.expander("Planner notes", expanded=False):
            render_notes_lazy(selected_district, key_suffix=f"district_{selected_district}")
            render_annotation_form(selected_district, key_suffix=f"district_{selected_district}")

    with detail_cols[1]:
        st.subheader("Needs Attention")
        queue = load_review_queue(selected_capability, selected_state)
        if not queue:
            st.caption("No facilities need attention for this view.")
        else:
            st.caption("Pick one to see the evidence below.")
        for idx, item in enumerate(queue, start=1):
            row_cols = st.columns([0.78, 0.22], vertical_alignment="center")
            with row_cols[0]:
                selected_marker = "Selected | " if item.get("facility_id") == st.session_state.get("focus_facility_id") else ""
                st.markdown(
                    f"<div class='queue-name'>{selected_marker}{idx}. {item.get('name') or item.get('facility_id')}</div>"
                    f"<div class='queue-meta'>{item.get('district')}</div>"
                    f"<div class='queue-meta'>{short_reason(item)}</div>",
                    unsafe_allow_html=True,
                )
            with row_cols[1]:
                if st.button("View", key=f"queue_{item.get('facility_id')}", width="stretch"):
                    focus_queue_item(item)
                    st.rerun()
            if idx < len(queue):
                st.divider()

    fe_cols = st.columns([0.75, 0.25], vertical_alignment="center")
    fe_cols[0].subheader("Facility Evidence")
    low_trust_only = fe_cols[1].toggle("Only needs review", value=False)
    if not st.session_state.show_facilities:
        st.caption("Open facility evidence when you need the details.")
        if st.button("Show facility evidence", width="stretch"):
            st.session_state.show_facilities = True
            st.rerun()
    else:
        if st.button("Hide facility evidence"):
            st.session_state.show_facilities = False
            st.session_state.focus_facility_id = None
            st.rerun()
        facilities = load_facilities(selected_district, selected_capability, low_trust_only=low_trust_only)
        if not facilities:
            st.caption("No facilities match this capability and filter.")
        focused_id = st.session_state.get("focus_facility_id")
        focused = next((f for f in facilities if f.get("facility_id") == focused_id), None)
        if focused:
            st.success(f"Showing review item: {focused.get('name') or focused.get('facility_id')} in {selected_district}")
            st.markdown("<div class='section-label'>Selected From Needs Attention</div>", unsafe_allow_html=True)
            render_facility(focused, selected_district, force_open=True)
            st.markdown("<div class='section-label'>Other Facilities In District</div>", unsafe_allow_html=True)
        elif focused_id:
            st.info("The selected facility is outside the current filter.")

        for facility in facilities:
            if focused and facility.get("facility_id") == focused.get("facility_id"):
                continue
            render_facility(facility, selected_district)

with tab_fixes:
    st.subheader("Demo Talking Points")
    for insight in load_demo_insights(selected_capability, selected_state):
        st.write(f"- {insight}")

    st.subheader("What We Cleaned")
    st.write("These fixes turn messy source records into planner-ready district and facility evidence.")

    fixes = [
        {
            "Fix": "State names repaired from pincode",
            "Before": "THANE or SIRMAUR appears as a state",
            "After": "THANE -> MAHARASHTRA; SIRMAUR -> HIMACHAL PRADESH",
            "Why it matters": "The State -> District picker becomes usable and districts stop appearing under the wrong state.",
        },
        {
            "Fix": "District backfilled from pincode",
            "Before": "District = Unknown",
            "After": "Use india_post_pincode_directory to fill the district when pincode is present",
            "Why it matters": "Facilities can be mapped and counted in the right district instead of being hidden in Unknown.",
        },
        {
            "Fix": "Bad coordinates tolerated",
            "Before": "latitude = NA causes the pipeline to fail",
            "After": "Malformed coordinates become blank, then pincode/geocode backfill can fill them",
            "Why it matters": "Bad rows do not break the full run.",
        },
        {
            "Fix": "Demand matched to supply",
            "Before": "Map only shows where facilities exist",
            "After": "NFHS need indicators are matched by district and included in the gap score",
            "Why it matters": "A red district means verified care is low and local need is high.",
        },
        {
            "Fix": "Claims checked against evidence",
            "Before": "A facility says ICU or dialysis, but the text may not support it",
            "After": "Trust flags call out mismatches like claims ICU but no ICU bed evidence",
            "Why it matters": "Planners see which facilities need follow-up instead of trusting raw claims.",
        },
        {
            "Fix": "Human flags feed re-review",
            "Before": "Field notes are stored but do not affect priority",
            "After": "Wrong or missing capability flags push a facility into the next reclassification pass",
            "Why it matters": "Planner feedback improves the data over time.",
        },
    ]
    st.dataframe(pd.DataFrame(fixes), hide_index=True, width="stretch")

    st.subheader("Pipeline Layers")
    st.write("Raw facilities + pincode reference + NFHS demand + LLM extraction + trust scoring + planner annotations.")
    st.caption("The app reads the cleaned Lakebase cache: cg_facilities, cg_districts, cg_demand_reference, and cg_district_capability_scores.")

updated_at = overview.get("updated_at")
updated = updated_at.isoformat() if hasattr(updated_at, "isoformat") else (updated_at or "not synced")
st.caption(f"Last data update: {updated} | Page rendered: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
