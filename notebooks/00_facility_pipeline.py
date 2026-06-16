# Databricks notebook source
# MAGIC %md
# MAGIC # Team EP — Facility Intelligence Pipeline (single notebook, incremental)
# MAGIC **Replaces `01` + `02`.** raw `facilities` ➜ **silver** `facility_refined` ➜ **gold** `facility_confidence` ➜ `district_gaps` + `facility_app` view.
# MAGIC
# MAGIC - The LLM (`ai_query`) extraction runs **once**, and **only on new/changed rows** (detected by a text hash).
# MAGIC - Everything else is deterministic SQL. Writes are **idempotent** via `MERGE`, so it's safe to re-run / schedule.
# MAGIC - Designed to run as a **Lakeflow Job** (see last cell).
# MAGIC
# MAGIC **Medallion:** bronze=`facilities` · silver=`facility_refined` · gold=`facility_confidence` + `district_gaps` · serving=`facility_app` view.

# COMMAND ----------

# MAGIC %md ## Parameters (a Lakeflow Job can override these)

# COMMAND ----------

dbutils.widgets.dropdown("mode", "incremental", ["incremental", "full"])
dbutils.widgets.text("sample_limit", "0")     # >0 = process only N rows (use to validate the prompt cheaply)
dbutils.widgets.text("backfill_low_quality", "true")
MODE   = dbutils.widgets.get("mode")
SAMPLE = int(dbutils.widgets.get("sample_limit") or "0")
BACKFILL_LOW_QUALITY = dbutils.widgets.get("backfill_low_quality").lower() == "true"
print("mode:", MODE, "| sample_limit:", SAMPLE, "| backfill_low_quality:", BACKFILL_LOW_QUALITY)

# COMMAND ----------

# MAGIC %md ## CONFIG

# COMMAND ----------

CAT, SCH = "databricks_virtue_foundation_dataset_dais_2026", "virtue_foundation_dataset"
FACILITY_TABLE = f"{CAT}.{SCH}.facilities"
PINCODE_TABLE  = f"{CAT}.{SCH}.india_post_pincode_directory"
NFHS_TABLE     = f"{CAT}.{SCH}.nfhs_5_district_health_indicators"
OUT_REFINED    = "workspace.default.facility_refined"
OUT_CONFIDENCE = "workspace.default.facility_confidence"
OUT_GAPS       = "workspace.default.district_gaps"
OUT_QUALITY    = "workspace.default.facility_quality_backfill"
OUT_REFINED_STAGE    = "workspace.default._facility_refined_stage"
OUT_CONFIDENCE_STAGE = "workspace.default._facility_confidence_stage"
MODEL_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"   # set to a model available in your workspace
PIPE_VERSION   = "v6_state_backfill"

C = dict(id="unique_id", name="name", description="description", capability="capability",
    procedure="procedure", equipment="equipment", specialties="specialties",
    num_doctors="numberDoctors", capacity="capacity", year_est="yearEstablished",
    a1="address_line1", a2="address_line2", a3="address_line3", city="address_city",
    state="address_stateOrRegion", pin="address_zipOrPostcode", country="address_country",
    lat="latitude", lon="longitude", phones="phone_numbers", ophone="officialPhone",
    email="email", websites="websites", owebsite="officialWebsite", facebook="facebookLink",
    social_count="distinct_social_media_presence_count", followers="engagement_metrics_n_followers",
    last_post="post_metrics_most_recent_social_media_post_date", recency="recency_of_page_update", source="source")

NFHS = dict(district="district_name", inst_birth="institutional_birth_5y_pct",
    stunting="child_u5_who_are_stunted_height_for_age_18_pct", anaemia_women="all_w15_49_who_are_anaemic_pct")

CAP_VOCAB = ["emergency_care","icu","operation_theatre","general_surgery","csection_obstetrics","maternity",
    "dialysis","blood_bank","pharmacy","ambulance","laboratory","radiology","ct_scan","mri","xray","ultrasound",
    "ecg","endoscopy","physiotherapy","vaccination","outpatient","inpatient","teleconsultation"]
SPEC_VOCAB = ["general_medicine","general_surgery","cardiology","orthopedics","pediatrics","obstetrics_gynecology",
    "ophthalmology","ent","dermatology","neurology","nephrology","urology","oncology","gastroenterology",
    "pulmonology","psychiatry","dentistry","radiology","anesthesiology","endocrinology","ayush"]

from pyspark.sql import functions as F, Window
from pyspark.sql.types import StructType, StructField, StringType, ArrayType, BooleanType
import datetime

def parse_int(src, pat="([0-9]+)"):
    return F.expr(f"try_cast(regexp_extract(cast(`{src}` as string), '{pat}', 1) as int)")

def try_double_col(src):
    return F.expr(f"try_cast(`{src}` as double)")

# COMMAND ----------

# MAGIC %md ## [1] Bronze read + deterministic refine (cheap, all rows) — incl. `text_hash` for change detection

# COMMAND ----------

raw = spark.table(FACILITY_TABLE)

det = (raw
    .withColumn("facility_id", F.col(C["id"]))
    .withColumn("name", F.initcap(F.trim(F.col(C["name"]))))
    .withColumn("city",  F.trim(F.col(C["city"])))
    .withColumn("state", F.trim(F.col(C["state"])))
    .withColumn("country", F.trim(F.col(C["country"])))
    .withColumn("pincode", F.regexp_extract(F.col(C["pin"]).cast("string"), r"(\d{6})", 1))
    .withColumn("latitude",  try_double_col(C["lat"]))
    .withColumn("longitude", try_double_col(C["lon"]))
    .withColumn("year_established", parse_int(C["year_est"], "([0-9]{4})"))
    .withColumn("beds",    parse_int(C["capacity"]))
    .withColumn("doctors", parse_int(C["num_doctors"]))
    .withColumn("phones", F.expr(f"regexp_extract_all(concat_ws(' ', coalesce(`{C['phones']}`,''), coalesce(`{C['ophone']}`,'')), '[0-9]{{7,}}', 0)"))
    .withColumn("_official_phone", F.regexp_extract(F.col(C["ophone"]).cast("string"), r"([0-9]{7,})", 1))
    .withColumn("phone_primary", F.coalesce(
        F.when(F.col("_official_phone") != "", F.col("_official_phone")),
        F.expr("get(phones, 0)")
    ))
    .withColumn("email",   F.lower(F.trim(F.col(C["email"]))))
    .withColumn("website", F.coalesce(F.trim(F.col(C["owebsite"])), F.trim(F.col(C["websites"]))))
    .withColumn("facebook", F.trim(F.col(C["facebook"])))
    .withColumn("social_presence_count", parse_int(C["social_count"]))
    .withColumn("social_followers", parse_int(C["followers"]))
    .withColumn("social_last_post_date", F.trim(F.col(C["last_post"]).cast("string")))
    .withColumn("recency_of_page_update", F.trim(F.col(C["recency"]).cast("string")))
    .withColumn("source", F.trim(F.col(C["source"]).cast("string")))
    .withColumn("address_full", F.concat_ws(", ", F.trim(F.col(C["a1"])), F.trim(F.col(C["a2"])), F.trim(F.col(C["a3"])), F.col("city"), F.col("state"), F.col("pincode")))
    .withColumn("has_geo", F.col("latitude").isNotNull() & F.col("longitude").isNotNull()))

# combined clinical text (also the change-detection signal)
det = det.withColumn("all_text", F.lower(F.concat_ws(" . ",
    F.coalesce(F.col(C["description"]), F.lit("")), F.coalesce(F.col(C["capability"]), F.lit("")),
    F.coalesce(F.col(C["procedure"]),   F.lit("")), F.coalesce(F.col(C["equipment"]),  F.lit("")),
    F.coalesce(F.col(C["specialties"]), F.lit("")))))
det = det.withColumn("text_hash", F.sha2(F.col("all_text"), 256))

# data quality (computed BEFORE structured arrays overwrite raw text cols)
qf = [C["description"], C["capability"], C["procedure"], C["equipment"], C["specialties"], C["year_est"], C["capacity"]]
det = (det
    .withColumn("field_completeness_pct", F.round(F.lit(100.0) * sum([(F.col(c).isNotNull() & (F.trim(F.col(c).cast("string")) != "")).cast("double") for c in qf]) / F.lit(len(qf)), 0))
    .withColumn("has_description",     F.length(F.coalesce(F.col(C["description"]), F.lit(""))) > 0)
    .withColumn("has_capability_text", F.length(F.coalesce(F.col(C["capability"]),  F.lit(""))) > 0)
    .withColumn("has_equipment_text",  F.length(F.coalesce(F.col(C["equipment"]),   F.lit(""))) > 0)
    .withColumn("has_specialties_text",F.length(F.coalesce(F.col(C["specialties"]), F.lit(""))) > 0))

# de-dupe richest row per id
det = (det.withColumn("_rn", F.row_number().over(Window.partitionBy("facility_id").orderBy(F.length("all_text").desc())))
          .filter("_rn=1").drop("_rn"))
#det.persist(); print("facilities (deduped):", det.count())

# COMMAND ----------

# MAGIC %md ## [1b] Data quality backfill before LLM extraction
# MAGIC
# MAGIC Backfill is deliberately deterministic and cheap:
# MAGIC - normalize pincode and backfill district/state/lat/lon from `india_post_pincode_directory`
# MAGIC - preserve raw free-text, but create a stronger `all_text_backfilled` from every claim-bearing column
# MAGIC - flag rows that need re-extraction because text, district, geocode, or key clinical fields are sparse
# MAGIC - fold human correction flags from Lakebase `region_annotations` into reclassification priority when available

# COMMAND ----------

pin_ref = (spark.table(PINCODE_TABLE)
    .withColumn("pincode", F.regexp_extract(F.col("pincode").cast("string"), r"(\d{6})", 1))
    .filter(F.col("pincode") != "")
    .groupBy("pincode")
    .agg(
        F.first("district", True).alias("district_backfill"),
        F.first("statename", True).alias("state_backfill"),
        F.avg(F.expr("try_cast(latitude as double)")).alias("lat_backfill"),
        F.avg(F.expr("try_cast(longitude as double)")).alias("lon_backfill"),
    ))

def clean_claim_text(c):
    return F.regexp_replace(
        F.regexp_replace(F.coalesce(F.col(c).cast("string"), F.lit("")), r'[\[\]\{\}"]', " "),
        r"\s+",
        " ",
    )

det = (det.join(pin_ref, "pincode", "left")
    .withColumn("district", F.col("district_backfill"))
    .withColumn("state", F.coalesce(F.col("state_backfill"), F.col("state")))
    .withColumn("latitude", F.coalesce(F.col("latitude"), F.col("lat_backfill")))
    .withColumn("longitude", F.coalesce(F.col("longitude"), F.col("lon_backfill")))
    .withColumn("has_geo", F.col("latitude").isNotNull() & F.col("longitude").isNotNull())
    .withColumn("all_text_backfilled", F.lower(F.concat_ws(" . ",
        clean_claim_text(C["description"]),
        clean_claim_text(C["capability"]),
        clean_claim_text(C["procedure"]),
        clean_claim_text(C["equipment"]),
        clean_claim_text(C["specialties"]),
        F.coalesce(F.col("name"), F.lit("")),
        F.coalesce(F.col("address_full"), F.lit("")),
    )))
    .withColumn("all_text", F.when(F.length("all_text_backfilled") > F.length("all_text"), F.col("all_text_backfilled")).otherwise(F.col("all_text")))
    .withColumn("text_hash", F.sha2(F.col("all_text"), 256))
    .withColumn("has_district", F.col("district").isNotNull() & (F.trim("district") != ""))
    .withColumn("has_clinical_text", F.length(F.trim("all_text")) >= 20)
    .withColumn("quality_issue_flags_raw", F.array(
        F.when(~F.col("has_clinical_text"), F.lit("missing_clinical_text")),
        F.when(~F.col("has_district"), F.lit("missing_district")),
        F.when(~F.col("has_geo"), F.lit("missing_geocode")),
        F.when(F.col("beds").isNull(), F.lit("missing_beds")),
        F.when(F.col("doctors").isNull(), F.lit("missing_doctors"))
    ))
    .withColumn("quality_issue_flags", F.expr("filter(quality_issue_flags_raw, x -> x is not null)"))
    .withColumn("quality_issue_count", F.size("quality_issue_flags"))
    .withColumn("needs_quality_backfill", F.col("quality_issue_count") > 0)
    .drop("district_backfill", "state_backfill", "lat_backfill", "lon_backfill", "all_text_backfilled", "quality_issue_flags_raw"))

try:
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    lb_endpoint = "projects/dbrx-hackathon-2026/branches/production/endpoints/primary"
    lb_token = w.postgres.generate_database_credential(lb_endpoint).token
    lb_user = w.current_user.me().user_name
    ann = (spark.read
        .format("postgresql")
        .option("host", "ep-long-heart-d8anwpz5.database.us-east-2.cloud.databricks.com")
        .option("port", "5432")
        .option("database", "databricks_postgres")
        .option("dbtable", "region_annotations")
        .option("user", lb_user)
        .option("password", lb_token)
        .option("sslmode", "require")
        .load()
        .filter((F.col("is_test") == F.lit(False)) & (F.col("facility_id").isNotNull()))
        .groupBy("facility_id")
        .agg(
            F.max(F.col("reclassification_priority").cast("int")).alias("human_reclass_priority"),
            F.collect_set("human_flag").alias("human_flags"),
        ))
except Exception as ex:
    print("Lakebase annotation backfill signal unavailable:", ex)
    ann = spark.createDataFrame([], "facility_id string, human_reclass_priority int, human_flags array<string>")

quality = (det.select("facility_id", "name", "district", "pincode", "quality_issue_flags", "quality_issue_count", "needs_quality_backfill")
    .join(ann, "facility_id", "left")
    .withColumn("human_reclass_priority", F.coalesce(F.col("human_reclass_priority"), F.lit(0)))
    .withColumn("backfill_priority", F.col("needs_quality_backfill") | (F.col("human_reclass_priority") > 0)))
quality.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(OUT_QUALITY)

# COMMAND ----------

# MAGIC %md ## [2] Incremental detection — only NEW or CHANGED facilities go to the LLM

# COMMAND ----------

first_run = (MODE == "full") or (not spark.catalog.tableExists(OUT_REFINED))
if first_run:
    to_process = det
    print("FULL build" if MODE == "full" else "First run (no target table yet)")
else:
    existing = spark.table(OUT_REFINED).select("facility_id", "text_hash")
    changed = det.join(existing, ["facility_id", "text_hash"], "left_anti")   # new ids + changed text
    if BACKFILL_LOW_QUALITY:
        priority = spark.table(OUT_QUALITY).filter("backfill_priority").select("facility_id")
        to_process = det.join(
            changed.select("facility_id").union(priority).dropDuplicates(),
            "facility_id",
            "inner",
        )
    else:
        to_process = changed
if SAMPLE > 0:
    to_process = to_process.limit(SAMPLE)
n_proc = to_process.count()
print("rows to (re)extract via LLM:", n_proc)

# COMMAND ----------

# MAGIC %md ## [3] LLM extraction — ONCE, only on `to_process`

# COMMAND ----------

cap_v, spec_v = ", ".join(CAP_VOCAB), ", ".join(SPEC_VOCAB)
prompt = ("You extract structured data about an Indian healthcare facility from noisy text "
  "(may be JSON-like lists of claims). Return ONLY one minified JSON object, no markdown, keys: "
  "facility_type (hospital, specialty_hospital, clinic, phc, chc, sub_centre, nursing_home, diagnostic_centre, "
  "medical_college, dispensary, eye_hospital, dental_clinic, maternity_home, ayush, other, unknown), "
  "ownership (public, private, trust_ngo, unknown), "
  f"capabilities (subset of [{cap_v}]), specialties (subset of [{spec_v}]), "
  "key_procedures (array), equipment (array), services (array), accreditations (array), "
  "emergency_24x7, maternity_services, ambulance_available, blood_bank, pharmacy_onsite, teleconsultation (bool or null), "
  "summary (one factual sentence). Use null/empty when unknown; never invent. Text: ")

to_process.createOrReplaceTempView("to_process")
ext = spark.sql(f"""
  SELECT facility_id, ai_query('{MODEL_ENDPOINT}', CONCAT('{prompt}', all_text)) AS j
  FROM to_process WHERE all_text IS NOT NULL AND length(all_text) > 0
""")
ext_schema = StructType([
    StructField("facility_type", StringType()), StructField("ownership", StringType()),
    StructField("capabilities", ArrayType(StringType())), StructField("specialties", ArrayType(StringType())),
    StructField("key_procedures", ArrayType(StringType())), StructField("equipment", ArrayType(StringType())),
    StructField("services", ArrayType(StringType())), StructField("accreditations", ArrayType(StringType())),
    StructField("emergency_24x7", BooleanType()), StructField("maternity_services", BooleanType()),
    StructField("ambulance_available", BooleanType()), StructField("blood_bank", BooleanType()),
    StructField("pharmacy_onsite", BooleanType()), StructField("teleconsultation", BooleanType()),
    StructField("summary", StringType())])
ext = ext.withColumn("e", F.from_json(F.regexp_extract("j", r"(?s)\{.*\}", 0), ext_schema)).select("facility_id", "e")

proc = to_process.join(ext, "facility_id", "left")

# COMMAND ----------

# MAGIC %md ## [4] Build refined rows (silver) — normalized arrays + UI boolean flags + district + provenance

# COMMAND ----------

def norm(c):
    return F.array_distinct(F.expr(f"filter(transform(coalesce(e.{c}, array()), x -> lower(trim(x))), x -> x is not null and x <> '')"))

proc = (proc
    .withColumn("facility_type", F.coalesce(F.lower(F.col("e.facility_type")), F.lit("unknown")))
    .withColumn("ownership",     F.coalesce(F.lower(F.col("e.ownership")), F.lit("unknown")))
    .withColumn("summary",       F.col("e.summary"))
    .withColumn("capabilities",   norm("capabilities"))
    .withColumn("specialties",    norm("specialties"))
    .withColumn("key_procedures", norm("key_procedures"))
    .withColumn("equipment",      norm("equipment"))
    .withColumn("services",       norm("services"))
    .withColumn("accreditations", norm("accreditations"))
    .withColumn("capability_count", F.size("capabilities"))
    .withColumn("display_capabilities", F.array_join("capabilities", ", "))
    .withColumn("display_specialties",  F.array_join("specialties", ", "))
    .withColumn("emergency_24x7", F.col("e.emergency_24x7"))
    .withColumn("maternity_services", F.col("e.maternity_services"))
    .withColumn("ambulance_available", F.col("e.ambulance_available"))
    .withColumn("blood_bank", F.col("e.blood_bank"))
    .withColumn("pharmacy_onsite", F.col("e.pharmacy_onsite"))
    .withColumn("teleconsultation", F.col("e.teleconsultation"))
    .withColumn("all_caps", F.array_distinct(F.array_union(F.coalesce(F.col("capabilities"), F.array()), F.coalesce(F.col("specialties"), F.array())))))

def has(tag, *kw):
    c = F.array_contains(F.col("all_caps"), tag)
    for k in kw: c = c | F.col("all_text").contains(k)
    return F.coalesce(c, F.lit(False))

FLAGS = {
 "has_emergency": ("emergency_care","emergency","casualty"), "has_icu": ("icu","ventilator","intensive care"),
 "has_operation_theatre": ("operation_theatre","operation theat","surgical"), "has_maternity": ("maternity","labour","obstetric"),
 "has_csection": ("csection_obstetrics","c-section","caesar"), "has_dialysis": ("dialysis","nephrolog"),
 "has_blood_bank": ("blood_bank","blood bank","transfusion"), "has_ct_scan": ("ct_scan","ct scan","computed tomography"),
 "has_mri": ("mri","magnetic resonance"), "has_xray": ("xray","x-ray","radiograph"),
 "has_ultrasound": ("ultrasound","sonograph","usg"), "has_laboratory": ("laboratory","pathology","blood test"),
 "has_cardiology": ("cardiology","cardiac","echo"), "has_oncology": ("oncology","cancer","chemotherapy"),
 "has_pediatrics": ("pediatrics","paediatric","child"), "has_orthopedics": ("orthopedics","orthop","fracture"),
 "has_ophthalmology": ("ophthalmology","eye care","cataract"),
}
for col_, kws in FLAGS.items():
    proc = proc.withColumn(col_, has(*kws))

proc = (proc
    .withColumn("extracted_at", F.current_timestamp())
    .withColumn("model_endpoint", F.lit(MODEL_ENDPOINT))
    .withColumn("pipe_version", F.lit(PIPE_VERSION)))

# district via pincode bridge
pin = (spark.table(PINCODE_TABLE)
       .withColumn("pincode", F.regexp_extract(F.col("pincode").cast("string"), r"(\d{6})", 1))
       .groupBy("pincode").agg(F.first("district", True).alias("district_from_pin")))
proc = (proc.join(pin, "pincode", "left")
    .withColumn("district", F.coalesce(F.col("district"), F.col("district_from_pin")))
    .drop("district_from_pin"))

# COMMAND ----------

# MAGIC %md ## [5] Score confidence (gold) for the same rows — deterministic, no LLM
# MAGIC Tuned plausibility: imaging/lab are NOT contradictions; "basic" = small primary-care only.

# COMMAND ----------

EVIDENCE_KEYWORDS = {
    "general_surgery": ["operation theat","ot ","surgeon","anesth","anaesth","surgical"],
    "csection_obstetrics": ["labour","labor room","obstetric","gynae","maternity","c-section","caesar"],
    "icu": ["icu","ventilator","intensive care","critical care"], "dialysis": ["dialysis","nephrolog","hemodialysis"],
    "blood_bank": ["blood bank","transfusion"], "cardiology": ["cardiac","cath lab","ecg","echo"],
    "oncology": ["oncolog","cancer","chemotherapy"],
}
HIGH_ACUITY = ["general_surgery","icu","dialysis","oncology","cardiology"]   # imaging deliberately excluded
BASIC_NAME  = ["clinic","dispensary","sub cent","sub-cent","phc","primary health","polyclinic","health post","health centre","health center"]

sc = proc.select("facility_id","name","state","pincode","district","all_text","all_caps",
                 F.col("doctors").alias("num_doctors"), F.col("beds").alias("capacity"),
                 "year_established","field_completeness_pct")
exp = sc.withColumn("cap", F.explode_outer("all_caps"))
name_l = F.lower(F.coalesce(F.col("name"), F.lit("")))
looks_basic = (F.coalesce(F.col("num_doctors") <= 3, F.lit(False)) | F.coalesce(F.col("capacity") <= 10, F.lit(False)))
for kw in BASIC_NAME: looks_basic = looks_basic | name_l.contains(kw)
sup = F.lit(0.5)
for capk, kws in EVIDENCE_KEYWORDS.items():
    anyk = F.lit(False)
    for kw in kws: anyk = anyk | F.col("all_text").contains(kw)
    sup = F.when(F.col("cap") == capk, anyk.cast("double")).otherwise(sup)
exp = (exp.withColumn("s_equipment_support", sup)
          .withColumn("s_type_plausibility", F.when(F.col("cap").isin(HIGH_ACUITY) & looks_basic & (F.col("s_equipment_support") < 1.0), F.lit(0.0)).otherwise(F.lit(1.0))))
sig = exp.groupBy("facility_id").agg(
    F.collect_list("cap").alias("claimed_caps"),
    F.avg("s_equipment_support").alias("s_equipment_support"),
    F.min("s_type_plausibility").alias("s_type_plausibility"),
    F.count(F.when(F.col("s_type_plausibility") == 0, True)).alias("n_contradictions"))

yr = datetime.date.today().year
base = (sc.dropDuplicates(["facility_id"])
    .withColumn("s_coverage", F.col("field_completeness_pct")/F.lit(100.0))
    .withColumn("s_recency", F.when(F.col("year_established").isNull(), F.lit(None))
        .otherwise(F.greatest(F.lit(0.0), F.least(F.lit(1.0), (F.col("year_established")-F.lit(1950))/F.lit(yr-1950)))))
    .join(sig, "facility_id", "left"))
W = {"s_equipment_support":0.35, "s_type_plausibility":0.25, "s_coverage":0.25, "s_recency":0.15}
num=F.lit(0.0); den=F.lit(0.0); navail=F.lit(0)
for s, wt in W.items():
    num = num + F.when(F.col(s).isNotNull(), F.col(s)*F.lit(wt)).otherwise(F.lit(0.0))
    den = den + F.when(F.col(s).isNotNull(), F.lit(wt)).otherwise(F.lit(0.0))
    navail = navail + F.col(s).isNotNull().cast("int")
conf_new = (base
    .withColumn("confidence", F.round(F.when(den>0, (num/den)*100).otherwise(F.lit(0.0)), 1))
    .withColumn("confidence_band", F.round(F.lit(40.0)*(F.lit(1.0)-(navail/F.lit(len(W)))), 0))
    .withColumn("evidence_level", F.when(navail>=3,"High").when(navail==2,"Medium").otherwise("Low"))
    .withColumn("trust_bucket", F.when(F.col("n_contradictions")>0,"Contradicted")
        .when((F.col("confidence")>=75)&(F.col("evidence_level")!="Low"),"Verified")
        .when(F.col("confidence")>=50,"Plausible").otherwise("Unverified"))
    .withColumn("scored_at", F.current_timestamp()).withColumn("model_endpoint", F.lit(MODEL_ENDPOINT)).withColumn("score_version", F.lit(PIPE_VERSION))
    .select("facility_id","name","state","pincode","district","confidence","confidence_band","evidence_level",
            "trust_bucket","n_contradictions","claimed_caps","scored_at","model_endpoint","score_version"))

# COMMAND ----------

# MAGIC %md ## [6] Upsert silver + gold (idempotent `MERGE`; full create on first run)

# COMMAND ----------

refined_cols = ["facility_id","name","facility_type","ownership","summary","year_established",
 "phone_primary","phones","email","website","facebook",
 "address_full","city","state","district","pincode","country","latitude","longitude","has_geo",
 "beds","doctors","capabilities","specialties","key_procedures","equipment","services","accreditations",
 "capability_count","display_capabilities","display_specialties",
 "emergency_24x7","maternity_services","ambulance_available","blood_bank","pharmacy_onsite","teleconsultation",
 *FLAGS.keys(),
 "field_completeness_pct","has_description","has_capability_text","has_equipment_text","has_specialties_text",
 "social_presence_count","social_followers","social_last_post_date","recency_of_page_update","source",
 "extracted_at","model_endpoint","pipe_version","text_hash"]
refined_new = proc.select(*refined_cols)

# Materialize once before final writes. Without this, serverless may replay the
# expensive upstream LLM extraction lineage separately for silver and gold.
refined_new.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(OUT_REFINED_STAGE)
conf_new.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(OUT_CONFIDENCE_STAGE)
refined_new = spark.table(OUT_REFINED_STAGE)
conf_new = spark.table(OUT_CONFIDENCE_STAGE)
print("staged", n_proc, "rows for final refined + confidence writes")

def upsert(df, table, key="facility_id"):
    df.createOrReplaceTempView("_src")
    spark.sql(f"MERGE INTO {table} t USING _src s ON t.{key}=s.{key} WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")

if first_run:
    refined_new.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(OUT_REFINED)
    conf_new.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(OUT_CONFIDENCE)
    print("created", OUT_REFINED, "and", OUT_CONFIDENCE)
elif n_proc > 0:
    upsert(refined_new, OUT_REFINED); upsert(conf_new, OUT_CONFIDENCE)
    print(f"merged {n_proc} new/changed rows into refined + confidence")
else:
    print("no new/changed facilities — refined + confidence already current")

# COMMAND ----------

# MAGIC %md ## [7] District gaps (full recompute — cheap, no LLM). Normalized district join => need_index populates.

# COMMAND ----------

sd = lambda c: F.expr(f"try_cast(`{c}` as double)")
conf = spark.table(OUT_CONFIDENCE).withColumn("district_key", F.upper(F.trim("district")))   # confidence already carries district
supply = (conf.withColumn("w", F.col("confidence")/F.lit(100.0)).groupBy("district_key").agg(
    F.first("district", True).alias("district"), F.count("*").alias("facilities_raw"),
    F.round(F.sum("w"),1).alias("facilities_trusted_equiv"), F.round(F.avg("confidence"),1).alias("avg_confidence"),
    F.sum(F.when(F.col("trust_bucket").isin("Verified","Plausible"),1).otherwise(0)).alias("facilities_usable")))
nf = spark.table(NFHS_TABLE)
need = ((F.lit(100.0)-sd(NFHS["inst_birth"]))/100.0 + sd(NFHS["stunting"])/100.0 + sd(NFHS["anaemia_women"])/100.0)/F.lit(3.0)
nf_need = (nf.select(F.upper(F.trim(F.col(NFHS["district"]))).alias("district_key"), need.alias("need_index"))
             .groupBy("district_key").agg(F.avg("need_index").alias("need_index")))
gaps = (supply.join(nf_need, "district_key", "left")
    .withColumn("hidden_desert", F.col("facilities_trusted_equiv") < F.col("facilities_raw")*0.5)
    .withColumn("priority_score", F.round(F.col("need_index")/(F.col("facilities_trusted_equiv")+F.lit(1)), 4)))
gaps.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(OUT_GAPS)

# COMMAND ----------

# MAGIC %md ## [8] `facility_app` view (refined + gold) — the UI binds here

# COMMAND ----------

spark.sql(f"""CREATE OR REPLACE VIEW workspace.default.facility_app AS
  SELECT r.*, c.confidence, c.confidence_band, c.evidence_level, c.trust_bucket, c.n_contradictions
  FROM {OUT_REFINED} r LEFT JOIN {OUT_CONFIDENCE} c USING (facility_id)""")
print("Pipeline complete →  UI binds to  workspace.default.facility_app")
display(spark.table(OUT_GAPS).orderBy(F.col("priority_score").desc_nulls_last()).limit(20))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Schedule as a Lakeflow Job
# MAGIC **UI:** Workflows → **Create job** → Task type **Notebook** (this notebook) → Compute **Serverless** →
# MAGIC Parameters `mode=incremental`, `sample_limit=0` → **Add schedule** (e.g. daily 06:00) → Create.
# MAGIC `MERGE` makes every run idempotent, so reruns/backfills are safe.
# MAGIC
# MAGIC Or run the cell below **once** to register the job programmatically.

# COMMAND ----------

# OPTIONAL — create/update the Lakeflow Job from code (run once). Idempotent: updates if it already exists.
try:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service import jobs
    w = WorkspaceClient()
    nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    JOB_NAME = "facility_intelligence_pipeline"
    task = jobs.Task(task_key="pipeline",
        notebook_task=jobs.NotebookTask(notebook_path=nb_path, base_parameters={"mode": "incremental", "sample_limit": "0"}))
    sched = jobs.CronSchedule(quartz_cron_expression="0 0 6 * * ?", timezone_id="Asia/Kolkata")  # daily 06:00 IST
    hits = [j for j in w.jobs.list(name=JOB_NAME)]
    if hits:
        w.jobs.reset(job_id=hits[0].job_id, new_settings=jobs.JobSettings(name=JOB_NAME, tasks=[task], schedule=sched))
        print("updated Lakeflow Job", hits[0].job_id)
    else:
        print("created Lakeflow Job", w.jobs.create(name=JOB_NAME, tasks=[task], schedule=sched).job_id)
except Exception as ex:
    print("Job API note:", ex, "\nIf this errors (e.g. compute spec), create the job via the Workflows UI as described above.")
