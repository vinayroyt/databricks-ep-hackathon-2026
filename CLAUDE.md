# Care Gap Atlas

**Track:** Medical Desert Planner — built like a real product, not just a ranking tool.

## The Idea

Most submissions to this track will rank districts by raw facility count. This project goes further by chaining **extraction → validation → aggregation → visualization → persistence**:

1. **Extract** structured capability data (specialties, equipment, bed counts, certifications) from messy free-text facility records using an LLM — with a **confidence score per field** and a **cited evidence snippet** from the source text.
2. **Validate** by cross-referencing claimed capabilities against the extracted/structured fields to flag unreliable facilities (e.g., a facility claims an ICU but lists zero ICU beds).
3. **Aggregate** verified capabilities geographically into a "true care gap score" per region — not "how many hospitals" but "how many hospitals can actually do X."
4. **Visualize** via an interactive map/dashboard: a non-technical planner clicks a region, sees the gap, sees *why* (which facilities, what's missing, how confident the system is).
5. **Persist**: planners can annotate/save notes on regions for later sessions.

The judging brief explicitly calls out "uncertainty communication" and "persist their work" — most teams will skip both. This project leads with them.

## Build Order (Do This, In Order)

1. **Explore the data first (30-45 min, no AI yet).** Pull 20-30 random rows of the free-text columns and actually read them. The extraction prompt design depends entirely on knowing what the messiness looks like — inconsistent units, abbreviations, typos, etc.
2. **Build the extraction pipeline on a small sample (1-2 hrs).** Pick ~50 records, write the LLM prompt to extract structured fields + confidence + evidence snippet, and manually validate the output format. Do **not** run on all 10,000 yet — get the schema and prompt right first.
3. **Scale extraction to the full dataset.** Once the prompt is solid, batch/parallelize across all ~10,000 records. Run this in the background while building the next layers.
4. **Build the cross-reference / trust-scoring layer.** Can start against mocked data matching the schema while extraction runs. Compares claimed vs. structured fields and computes a reliability flag.
5. **Build the geographic aggregation.** Group by region, compute gap scores from *verified* capability data (not raw claims). This is the core differentiator — get it right.
6. **Build the Databricks App UI last, and keep it simple.** Map view + region drill-down + evidence panel + save/annotate for persistence. A clean working map beats a fancy half-broken dashboard.
7. **Reserve the last chunk of time for the demo narrative.** Pick 2-3 concrete regions/facilities as the story. Judges remember "this clinic claims an ICU but has zero ICU beds listed — here's the evidence" far more than abstract metrics.

## Team Ownership

- **Person 1 — Data Exploration & Schema Design** (first 60-90 min, then floats to support whichever stage is behind). Profiles all 51 columns, samples free-text fields, documents messiness patterns. Deliverable: shared schema doc (structured fields to extract + example input/output pairs). Unblocks Person 2 immediately.
- **Person 2 — Extraction Pipeline (LLM-based)**. Owns the prompt turning messy free text into structured fields + confidence + evidence snippet. Starts on the 50-record sample as soon as a rough schema exists, iterates on prompt quality, then batches the full ~10,000. Highest-risk, highest-value piece — don't let it bottleneck everyone else.
- **Person 3 — Trust/Validation Logic**. Builds the claimed-vs-structured cross-reference logic against the schema, using mocked/synthetic data initially. Swaps in real extracted data once Person 2 has output, and re-validates.
- **Person 4 — Geographic Aggregation & Care Gap Scoring**. Designs the group-by-region gap score logic, initially against mocked data. Also researches external reference data needs (district boundaries, population figures) early — this is an external dependency worth resolving fast.
- **Person 5 — Databricks App / Frontend**. Starts the app shell, map component, and UI layout immediately with placeholder data shaped like the eventual schema, so later integration is a data-source swap, not a rebuild.

## Critical Sync Points

- **~90 min:** Schema finalized (Person 1 → everyone). Without this, Persons 2-5 are guessing.
- **~3 hr:** First real extracted batch ready (Person 2 → 3, 4, 5). Everyone swaps mock data for real data — riskiest integration point, block time for it.
- **~5-6 hr:** Full pipeline integration test — run the whole chain end-to-end on a moderate sample, fix breakages.
- **Last 1-2 hr:** Demo prep — pick 2-3 concrete facility/region stories, polish UI, rehearse the narrative.

## Tech Stack

- Databricks Apps for the dashboard/UI (see `databricks-apps` skill before scaffolding).
- LLM-based extraction over facility free-text fields (likely via Databricks Model Serving — see `databricks-model-serving` skill).
- Batch/parallel processing for scaling extraction across ~10,000 records (Lakeflow Jobs/Pipelines — see `databricks-jobs` / `databricks-pipelines` skills).
- Persistence for planner annotations — evaluate Lakebase (synced tables / OLTP) for the save/annotate feature (see `databricks-lakebase` skill).

## Working Notes

- This repo is freshly initialized — no schema, data, or code exists yet. The schema doc from Person 1 (step 1/2 above) should become the single source of truth that the extraction, trust-scoring, aggregation, and UI layers are all built against.
- Always store the evidence snippet and confidence score alongside every extracted field — these are core to the "uncertainty communication" story, not an afterthought.
- Care gap scores must be computed from *verified* capabilities, not raw claimed text — that's the whole point of the trust-scoring layer.

## Ops / Deployment Notes

These are hard-won — read before touching anything deployment-related.

### Model Serving: SP identity changes per version

Every `databricks.agents.deploy()` call assigns a **new internal service principal** to the served model version. When you deploy v6, it will have a different SP UUID than v5's `b2d4081a-e07c-4546-975c-78d3fa5748bd`. After each new deployment, you must re-run the grants below for the new SP UUID (find it from the `planner_notes_error` field in any Lakebase-touching tool call, or from the `w.current_user.me().user_name` value inside the served model).

### Granting UC permissions to the serving SP

The serving SP is not a normal workspace user — `databricks service-principals list` won't show it. Grant via SQL warehouse (warehouse.run_sql or a notebook):

```sql
-- Core tables
GRANT SELECT ON TABLE workspace.default.facility_app TO `<sp-uuid>`;
GRANT SELECT ON TABLE workspace.default.facility_refined TO `<sp-uuid>`;
GRANT SELECT ON TABLE workspace.default.facility_confidence TO `<sp-uuid>`;
GRANT SELECT ON TABLE workspace.default.district_gaps TO `<sp-uuid>`;
GRANT MODIFY ON TABLE workspace.default.facility_refined TO `<sp-uuid>`;
GRANT MODIFY ON TABLE workspace.default.facility_confidence TO `<sp-uuid>`;

-- Bronze catalog (needed by get_facility_detail's raw text query)
GRANT USE CATALOG ON CATALOG `databricks_virtue_foundation_dataset_dais_2026` TO `<sp-uuid>`;
GRANT USE SCHEMA ON SCHEMA databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset TO `<sp-uuid>`;
GRANT SELECT ON TABLE databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities TO `<sp-uuid>`;
```

### Granting Lakebase Postgres access to the serving SP

Two steps — control plane (role creation) then data plane (GRANTs):

**Step 1 — Create the Postgres role (control plane, always works):**
```bash
databricks postgres create-role projects/dbrx-hackathon-2026/branches/production \
  --role-id sp-<sp-uuid> \
  --json '{"spec": {"identity_type": "SERVICE_PRINCIPAL", "postgres_role": "<sp-uuid>", "auth_method": "LAKEBASE_OAUTH_V1"}}' \
  --profile dbrx-hackathon-2026
```
Note: `role-id` must start with a letter, hence the `sp-` prefix; `postgres_role` is the raw UUID (no prefix).

**Step 2 — Grant table privileges (data plane, needs the endpoint to be enabled first):**
```python
# Run via agents/lakebase.py get_connection() or psql
GRANT USAGE ON SCHEMA public TO "<sp-uuid>";
GRANT SELECT, INSERT, UPDATE ON region_annotations TO "<sp-uuid>";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "<sp-uuid>";
```

### Fixing a disabled Lakebase endpoint

If `databricks postgres get-endpoint ... -o json` shows `"disabled": true`, connections will fail with `"The endpoint has been disabled. Enable it using the API and retry."` The CLI's `update-endpoint` command rejects `disabled` and `status.disabled` as unknown mask fields. Use the raw REST API with `spec.disabled`:

```python
import urllib.request, json, subprocess

result = subprocess.run(['databricks','auth','token','--profile','dbrx-hackathon-2026','-o','json'], capture_output=True, text=True)
token = json.loads(result.stdout)['access_token']

req = urllib.request.Request(
    'https://dbc-90959f59-20ca.cloud.databricks.com/api/2.0/postgres/projects/dbrx-hackathon-2026/branches/production/endpoints/primary?update_mask=spec.disabled',
    method='PATCH',
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
    data=json.dumps({'spec': {'disabled': False}}).encode(),
)
with urllib.request.urlopen(req) as r:
    print(r.status)  # 200 = success
```

Key facts: REST base path is `/api/2.0/postgres/` (not `/api/2.0/lakebase/postgres/v1/`). The working update_mask is `spec.disabled`. After the PATCH, verify with `get-endpoint` that `disabled` is now `false` before retrying connections.
