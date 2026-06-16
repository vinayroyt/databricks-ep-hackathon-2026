# VeriCare Map

Databricks hackathon prototype for verified healthcare access planning.

## Runtime Flow

1. Run Anil's `00_facility_pipeline` notebook in Databricks.
   It cleans raw facility records, backfills district/geocode from the pincode directory, reprocesses low-quality or human-flagged rows, writes:
   - `workspace.default.facility_refined`
   - `workspace.default.facility_confidence`
   - `workspace.default.district_gaps`
   - `workspace.default.facility_app`

2. Sync flattened UI data into Lakebase:

   ```bash
   .venv/bin/python scripts/sync_lakebase_ui.py
   ```

   This creates/updates:
   - `cg_facilities`
   - `cg_districts`
   - `cg_district_capability_scores`
   - `region_annotations`

3. Optionally backfill missing UI coordinates with a cached trusted API lookup:

   ```bash
   GEOCODE_LIMIT=50 .venv/bin/python scripts/backfill_external_quality.py
   ```

   Source coordinates and the pincode directory are preferred. External geocoding only fills remaining missing coordinates and stores provenance in `cg_geocode_cache`.

4. Deploy as a Databricks App from this repo. `app.yaml` runs:

   ```bash
   streamlit run app/app.py --server.address=0.0.0.0 --server.port=8000
   ```

## Annotation Path

The Streamlit app does not write annotations directly. It calls `annotation_agent.save_annotation()` / `get_annotations()`, so region notes, facility notes, and human flags all land in `region_annotations`.

Human flags:
- `looks_good`
- `data_wrong`
- `incorrect_capability`
- `missing_capability`

The last three set `reclassification_priority=true`, which the notebook uses to push facilities into the next extraction/backfill pass.
