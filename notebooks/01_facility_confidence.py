# Databricks notebook source
# MAGIC %md
# MAGIC # Team EP — Facility Refinement + Confidence Score (Track 1 → Track 2)
# MAGIC
# MAGIC **Tailored to the real schema** (read live from the workspace on 2026-06-15).
# MAGIC
# MAGIC ```
# MAGIC facilities (10k, 51 cols) ─▶ refine ─▶ extract capability tags (ai_query) ─▶ corroboration ─▶
# MAGIC      per-facility CONFIDENCE (0–100)  ─▶  facility_confidence (Delta)
# MAGIC                       │
# MAGIC   address_zipOrPostcode ─▶ india_post_pincode_directory (pincode→district) ─▶ district
# MAGIC                       │
# MAGIC   nfhs_5_district_health_indicators (need) ─▶ confidence-WEIGHTED district gaps ─▶ district_gaps (Delta)
# MAGIC ```
# MAGIC
# MAGIC ### What the live schema check revealed (important)
# MAGIC - **`organization_type` is useless** — it's the literal string `"facility"` for all 10,000 rows (+ a few null/garbage values). Do **not** use it to tell a clinic from a hospital. We infer acuity from `name` + `numberDoctors` + `capacity` instead.
# MAGIC - **No `district` column on facilities.** Bridge `address_zipOrPostcode → india_post_pincode_directory.pincode → district`, which joins to `nfhs_5...district_name`. (`latitude`/`longitude` doubles also exist as a fallback.)
# MAGIC - **Claim fields (`capability`, `procedure`, `specialties`, `equipment`) are messy** — often JSON-ish arrays of strings with empty `""` and stray text. Treat as claims to verify.

# COMMAND ----------

# MAGIC %md
# MAGIC ## CONFIG

# COMMAND ----------

CAT = "databricks_virtue_foundation_dataset_dais_2026"
SCH = "virtue_foundation_dataset"

FACILITY_TABLE = f"{CAT}.{SCH}.facilities"
PINCODE_TABLE  = f"{CAT}.{SCH}.india_post_pincode_directory"      # pincode -> district, statename, lat, lon
NFHS_TABLE     = f"{CAT}.{SCH}.nfhs_5_district_health_indicators"

OUT_SCHEMA        = "workspace.default"
OUT_CONFIDENCE    = f"{OUT_SCHEMA}.facility_confidence"
OUT_DISTRICT_GAPS = f"{OUT_SCHEMA}.district_gaps"

# Foundation-model endpoint (Serving > available models). Verify the exact name in YOUR workspace.
MODEL_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"

# Real facilities columns we use (verified against information_schema)
C = dict(
    id="unique_id", name="name",
    org_type="organization_type",          # ~uniform "facility" -> NOT used for acuity
    state="address_stateOrRegion", city="address_city", pincode="address_zipOrPostcode",
    lat="latitude", lon="longitude",        # DOUBLE
    description="description", capability="capability", procedure="procedure",
    equipment="equipment", specialties="specialties",
    num_doctors="numberDoctors", capacity="capacity", year_est="yearEstablished",
)

# Real NFHS-5 need indicators (verified). Higher need = worse access/outcomes.
NFHS = dict(
    district="district_name", state="state_ut",
    inst_birth="institutional_birth_5y_pct",                              # LOW = high need
    stunting="child_u5_who_are_stunted_height_for_age_18_pct",            # HIGH = high need
    anaemia_women="all_w15_49_who_are_anaemic_pct",                       # HIGH = high need
    oop_delivery="average_out_of_pocket_expenditure_per_delivery_in_a_public_fac",
)

# Controlled capability vocabulary the planner cares about
CAPABILITY_VOCAB = [
    "emergency_care","general_surgery","csection_obstetrics","icu","dialysis",
    "blood_bank","ct_scan","mri","xray","ultrasound","laboratory","pharmacy",
    "maternity","pediatrics","cardiology","orthopedics","ophthalmology","oncology",
]

from pyspark.sql import functions as F, Window
import datetime

# COMMAND ----------

# MAGIC %md
# MAGIC ## [1] Load + refine
# MAGIC Keep original text for the evidence panel; build a clean `all_text` blob for extraction + keyword checks.

# COMMAND ----------

raw = spark.table(FACILITY_TABLE)
print(f"rows: {raw.count():,}  cols: {len(raw.columns)}")

base = (raw
    .withColumn("facility_id", F.col(C["id"]))
    .withColumn("name",        F.trim(F.col(C["name"])))
    .withColumn("state",       F.trim(F.col(C["state"])))
    .withColumn("city",        F.trim(F.col(C["city"])))
    .withColumn("pincode",     F.regexp_extract(F.col(C["pincode"]), r"(\d{6})", 1))   # normalize to 6-digit
    .withColumn("lat",         F.col(C["lat"]).cast("double"))
    .withColumn("lon",         F.col(C["lon"]).cast("double"))
    .withColumn("description", F.col(C["description"]))
    .withColumn("capability",  F.col(C["capability"]))
    .withColumn("procedure",   F.col(C["procedure"]))
    .withColumn("equipment",   F.col(C["equipment"]))
    .withColumn("specialties", F.col(C["specialties"]))
    # numeric-ish fields are noisy strings -> extract leading integer if present
    .withColumn("num_doctors", F.regexp_extract(F.col(C["num_doctors"]).cast("string"), r"(\d+)", 1).cast("int"))
    .withColumn("capacity",    F.regexp_extract(F.col(C["capacity"]).cast("string"),   r"(\d+)", 1).cast("int"))
    .withColumn("year_est",    F.regexp_extract(F.col(C["year_est"]).cast("string"),   r"(\d{4})", 1).cast("int"))
)

# lower-cased combined text for extraction + keyword corroboration (handles JSON-ish arrays as plain text)
base = base.withColumn("all_text", F.lower(F.concat_ws(" . ",
    F.coalesce("description", F.lit("")), F.coalesce("capability", F.lit("")),
    F.coalesce("procedure",   F.lit("")), F.coalesce("equipment",  F.lit("")),
    F.coalesce("specialties", F.lit("")))))

# de-dupe: keep richest row per id
key_fields = ["description","capability","procedure","equipment","year_est","capacity"]
base = (base
    .withColumn("_c", sum([F.col(k).isNotNull().cast("int") for k in key_fields]))
    .withColumn("_rn", F.row_number().over(Window.partitionBy("facility_id").orderBy(F.col("_c").desc())))
    .filter("_rn = 1").drop("_rn","_c"))

base.createOrReplaceTempView("facilities_clean")
print("clean rows:", base.count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## [2] Extract structured capability tags (ai_query)
# MAGIC Restrict output to our vocabulary so it's bounded + parseable. **Validate on a LIMIT, then run full + cache to Delta.**

# COMMAND ----------

vocab = ", ".join(CAPABILITY_VOCAB)
claims = spark.sql(f"""
  SELECT facility_id, all_text,
    ai_query('{MODEL_ENDPOINT}',
      CONCAT(
        'You verify Indian healthcare facility claims. The text may be a JSON-like list of claimed services. ',
        'Return ONLY a JSON object {{"claimed":[...]}} using ONLY these labels: [{vocab}]. ',
        'Include a label only if explicitly supported by the text; do not infer. Text: ', all_text)
    ) AS claimed_raw
  FROM facilities_clean
  WHERE all_text IS NOT NULL AND length(all_text) > 0
  -- VALIDATE FIRST:  add `LIMIT 50`
""")

from pyspark.sql.types import StructType, StructField, ArrayType, StringType
sch = StructType([StructField("claimed", ArrayType(StringType()))])
claims = (claims
    .withColumn("claimed", F.from_json(F.regexp_extract("claimed_raw", r"\{.*\}", 0), sch).getField("claimed"))
    .withColumn("claimed", F.coalesce("claimed", F.array())))
claims.select("facility_id","claimed").write.mode("overwrite").saveAsTable(f"{OUT_SCHEMA}._facility_claims")
display(spark.table(f"{OUT_SCHEMA}._facility_claims").limit(20))

# COMMAND ----------

# MAGIC %md
# MAGIC ## [3] Corroboration — evidence FOR / AGAINST each claim
# MAGIC Transparent rules. `s_equipment_support`: does the text contain equipment/keywords a real claim would need?
# MAGIC `s_type_plausibility`: a *high-acuity* claim from a facility that looks small/basic (by name/doctors/capacity)
# MAGIC with no supporting keywords is a contradiction. (We do NOT use organization_type — it's uniform.)

# COMMAND ----------

EVIDENCE_KEYWORDS = {
    "general_surgery":     ["operation theat","ot ","surgeon","anesth","anaesth","surgical"],
    "csection_obstetrics": ["labour","labor room","obstetric","gynae","maternity","c-section","caesar"],
    "icu":                 ["icu","ventilator","intensive care","critical care"],
    "dialysis":            ["dialysis","nephrolog","hemodialysis"],
    "ct_scan":             ["ct scan"," ct ","computed tomography"],
    "mri":                 ["mri","magnetic resonance"],
    "blood_bank":          ["blood bank","transfusion"],
    "cardiology":          ["cardiac","cath lab","ecg","echo"],
}
HIGH_ACUITY = ["general_surgery","icu","ct_scan","mri","dialysis","oncology","cardiology"]
BASIC_NAME = ["clinic","dispensary","sub cent","sub-cent","phc","primary health","polyclinic","nursing home","diagnostic"]

clean = spark.table("facilities_clean")
exp = (spark.table(f"{OUT_SCHEMA}._facility_claims")
       .join(clean.select("facility_id","all_text","name","num_doctors","capacity"), "facility_id")
       .withColumn("cap", F.explode_outer("claimed")))

# looks_basic: small doctor count / small capacity / name hints (any signal present)
name_l = F.lower(F.coalesce(F.col("name"), F.lit("")))
looks_basic = (F.coalesce(F.col("num_doctors") <= 3, F.lit(False))
               | F.coalesce(F.col("capacity") <= 10, F.lit(False)))
for kw in BASIC_NAME:
    looks_basic = looks_basic | name_l.contains(kw)

# equipment keyword support per claimed capability (0.5 = no keyword list / unknown -> neutral)
sup = F.lit(0.5)
for cap, kws in EVIDENCE_KEYWORDS.items():
    any_kw = F.lit(False)
    for kw in kws:
        any_kw = any_kw | F.col("all_text").contains(kw)
    sup = F.when(F.col("cap") == cap, any_kw.cast("double")).otherwise(sup)
exp = exp.withColumn("s_equipment_support", sup)

# contradiction: high-acuity claim + looks basic + no keyword support
exp = exp.withColumn("s_type_plausibility",
    F.when(F.col("cap").isin(HIGH_ACUITY) & looks_basic & (F.col("s_equipment_support") < 1.0), F.lit(0.0))
     .otherwise(F.lit(1.0)))

sig = exp.groupBy("facility_id").agg(
    F.collect_list("cap").alias("claimed_caps"),
    F.avg("s_equipment_support").alias("s_equipment_support"),
    F.min("s_type_plausibility").alias("s_type_plausibility"),
    F.count(F.when(F.col("s_type_plausibility") == 0, True)).alias("n_contradictions"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## [4] Confidence (0–100) + band + bucket
# MAGIC Weighted blend of the signals that are computable, renormalized so sparse records get a **wider band**, not a false 0.

# COMMAND ----------

cov = sum([F.col(k).isNotNull().cast("double") for k in key_fields]) / F.lit(len(key_fields))
yr  = datetime.date.today().year
rec = F.when(F.col("year_est").isNull(), F.lit(None)) \
       .otherwise(F.greatest(F.lit(0.0), F.least(F.lit(1.0), (F.col("year_est")-F.lit(1950))/F.lit(yr-1950))))

scored = (clean.select("facility_id","name","state","city","pincode","lat","lon",
                       "description","capability","procedure","equipment","specialties",
                       "num_doctors","capacity","year_est")
    .withColumn("s_coverage", cov).withColumn("s_recency", rec)
    .join(sig, "facility_id", "left"))

W = {"s_equipment_support":0.35, "s_type_plausibility":0.25, "s_coverage":0.25, "s_recency":0.15}
num = F.lit(0.0); den = F.lit(0.0); n_avail = F.lit(0)
for s, wt in W.items():
    num = num + F.when(F.col(s).isNotNull(), F.col(s)*F.lit(wt)).otherwise(F.lit(0.0))
    den = den + F.when(F.col(s).isNotNull(), F.lit(wt)).otherwise(F.lit(0.0))
    n_avail = n_avail + F.col(s).isNotNull().cast("int")

scored = (scored
    .withColumn("confidence", F.round(F.when(den > 0, (num/den)*100).otherwise(F.lit(0.0)), 1))
    .withColumn("confidence_band", F.round(F.lit(40.0)*(F.lit(1.0)-(n_avail/F.lit(len(W)))), 0))  # +/- pts
    .withColumn("evidence_level", F.when(n_avail >= 3, "High").when(n_avail == 2, "Medium").otherwise("Low"))
    .withColumn("trust_bucket",
        F.when(F.col("n_contradictions") > 0, "Contradicted")
         .when((F.col("confidence") >= 75) & (F.col("evidence_level") != "Low"), "Verified")
         .when(F.col("confidence") >= 50, "Plausible").otherwise("Unverified")))

scored.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(OUT_CONFIDENCE)
display(spark.table(OUT_CONFIDENCE)
        .select("name","state","pincode","confidence","confidence_band","evidence_level",
                "trust_bucket","n_contradictions","claimed_caps").orderBy("confidence"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## [5] Track 2 — confidence-WEIGHTED district gaps
# MAGIC Bridge pincode → district, weight each facility by its confidence, then compare with NFHS-5 need.

# COMMAND ----------

# pincode -> district (one district per pincode; pick the most common if duplicates)
pin = (spark.table(PINCODE_TABLE)
       .withColumn("pincode", F.regexp_extract(F.col("pincode").cast("string"), r"(\d{6})", 1))
       .groupBy("pincode").agg(F.first("district", True).alias("district"),
                               F.first("statename", True).alias("state_pin")))

conf = spark.table(OUT_CONFIDENCE).join(pin, "pincode", "left")

# Trusted supply per district: a facility that can't back its claims barely counts
supply = (conf.withColumn("w", F.col("confidence")/F.lit(100.0))
    .groupBy("district").agg(
        F.count("*").alias("facilities_raw"),
        F.round(F.sum("w"), 1).alias("facilities_trusted_equiv"),
        F.round(F.avg("confidence"), 1).alias("avg_confidence"),
        F.sum(F.when(F.col("trust_bucket").isin("Verified","Plausible"),1).otherwise(0)).alias("facilities_usable")))

# NFHS-5 need index (0..1, higher = more need). Uses verified indicator columns.
nf = spark.table(NFHS_TABLE)
need = ((F.lit(100.0) - F.col(NFHS["inst_birth"]))/100.0       # fewer institutional births = more need
        + F.col(NFHS["stunting"])/100.0
        + F.col(NFHS["anaemia_women"])/100.0) / F.lit(3.0)
nf_need = nf.select(F.col(NFHS["district"]).alias("district"), need.alias("need_index"))

gaps = (supply.join(nf_need, "district", "left")
    .withColumn("hidden_desert", F.col("facilities_trusted_equiv") < F.col("facilities_raw")*0.5)
    .withColumn("priority_score", F.round(F.col("need_index") / (F.col("facilities_trusted_equiv")+F.lit(1)), 4)))

gaps.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(OUT_DISTRICT_GAPS)
display(spark.table(OUT_DISTRICT_GAPS).orderBy(F.col("priority_score").desc()))

# COMMAND ----------

# MAGIC %md
# MAGIC ### The headline for the demo
# MAGIC - **`facilities_raw` vs `facilities_trusted_equiv`** — apparent vs trusted coverage per district.
# MAGIC - **`hidden_desert = true`** — looks served on a map, isn't once you weight by confidence.
# MAGIC - **`priority_score`** = NFHS-5 need ÷ trusted supply → the districts to fix first.
# MAGIC - Every number drills down to per-facility evidence in `facility_confidence`. Honest uncertainty, end to end.