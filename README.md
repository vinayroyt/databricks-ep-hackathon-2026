# Care Gap Atlas

**Track:** Medical Desert Planner — Databricks EP Hackathon 2026

A healthcare facility intelligence platform that goes beyond counting hospitals. It extracts, validates, aggregates, and visualizes *verified* care capabilities — surfacing not just where facilities are missing, but **why specific claims can't be trusted**, with cited evidence and confidence scores throughout.

---

## The Problem

Most gap analyses count facilities. A district with three hospitals and a district with three functional ICUs look identical in a raw count. This project computes gaps from **verified** capabilities — facilities that *claim* an ICU but list zero ICU beds are flagged and excluded from the score.

---

## Architecture

```
Raw facility text
       │
       ▼
[LLM Extraction]  ── confidence score + evidence snippet per field
       │
       ▼
[Trust Scoring]   ── claimed vs. structured cross-reference → trust_score + trust_flags
       │
       ▼
[Geographic Aggregation] ── gap score from verified capabilities only
       │
       ▼
[Databricks App] ── interactive map + drill-down + planner annotations (Lakebase)
       │
       ▼
[Planner AI Agent] ── deployed ResponsesAgent, tool-calling over gap data + annotations
```

---

## Components

### `agents/`

| File | Purpose |
|---|---|
| `lakebase.py` | Shared Lakebase Postgres connection helper (local + Model Serving) |
| `evidence_agent.py` | Explains care gaps with cited evidence snippets and confidence scores |
| `annotation_agent.py` | Saves/retrieves planner notes to/from `region_annotations` in Lakebase |
| `planner_agent.py` | Combined MLflow `ResponsesAgent` — evidence + annotations in one agent, deployed to Model Serving |
| `deploy_planner_agent.py` | Logs agent to MLflow, registers in Unity Catalog, deploys to `care_gap_planner_agent` endpoint |
| `reclassification_agent.py` | Finds Contradicted/Unverified facilities, re-runs extraction with planner corrections, rescores and writes back to Delta |
| `scoring.py` | Deterministic confidence/trust scoring formula (single-row Python port of the pipeline notebook) |
| `mock_data.py` | Placeholder data shaped like pipeline output — swap for real pipeline data |
| `common.py` | Shared OpenAI client, `run_agent` loop, endpoint name |
| `warehouse.py` | Databricks SQL warehouse helper |

**Lakebase project:** `dbrx-hackathon-2026`  
**Model Serving endpoint:** `care_gap_planner_agent` (READY)  
**Base LLM endpoint:** `dbrxhack2026`

### `care-gap-atlas/`

Databricks App (TypeScript/React + AppKit) — the planner-facing UI.

**Live URL:** https://care-gap-atlas-7474645043324520.aws.databricksapps.com

| Route | What it shows |
|---|---|
| `/` | Interactive map (Leaflet, CartoDB tiles) centered on Karnataka. Color-coded circles by gap score — click for popup with gap score, claimed vs. verified ICU counts, and "View details" |
| `/region/:id` | Facility table with trust scores, expandable evidence snippets + field confidence bars, and persistent planner notes panel |
| `/planner` | Chat with the deployed `care_gap_planner_agent` ResponsesAgent; suggested questions pre-loaded |

**Backend Express routes:**
- `GET /api/regions` — region gap scores and summaries
- `GET /api/regions/:id/facilities` — facility-level data with trust scores and evidence
- `GET|POST|DELETE /api/annotations` — planner notes persisted in Lakebase `region_annotations`

**Plugins:** `lakebase` (annotation CRUD) + `serving` (`care_gap_planner_agent` proxy)

---

## Key Design Choices

**Uncertainty is first-class.** Every extracted field carries a confidence score and a cited evidence snippet from the source text. The UI surfaces these inline — a planner can see exactly why a facility's ICU claim is flagged as unreliable.

**Gap scores use verified capabilities.** A facility that claims ICU but has zero ICU beds is excluded from the verified count. The regional gap score reflects what can actually be delivered, not what facilities claim.

**Persistence across sessions.** Planner annotations are stored in Lakebase Postgres and surface in both the web UI and the AI agent. A note saved via chat appears in the map drill-down and vice versa.

**Reclassification loop.** The `reclassification_agent` incorporates planner-reported corrections from `region_annotations`, re-runs LLM extraction with that extra context, and rescores — closing the loop between human review and the pipeline.

---

## Local Development

```bash
# Python agents
pip install databricks-sdk psycopg openai mlflow

# Run an agent locally (uses the dbx CLI profile)
cd agents
python annotation_agent.py

# Databricks App
cd care-gap-atlas
npm install
npm run dev        # http://localhost:8000

# Deploy
databricks apps deploy --profile dbx
```

**Prerequisites:**
- Databricks CLI ≥ v1.0 with profile `dbx` authenticated
- Lakebase endpoint `dbrx-hackathon-2026/production/primary` enabled
- Model Serving endpoints `dbrxhack2026` and `care_gap_planner_agent` READY

---

## Data

Source: Databricks Virtue Foundation Dataset (DAIS 2026) — `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities`

The pipeline extracts structured fields (specialties, equipment, bed counts, ICU beds, certifications) from free-text facility records with per-field confidence scores and evidence snippets, then scores each facility for trust and aggregates by region.

Unity Catalog tables written by the pipeline:
- `workspace.default.facility_app` — full extraction + scoring output
- `workspace.default.facility_refined` — reclassification agent output
- `workspace.default.facility_confidence` — per-field confidence scores
