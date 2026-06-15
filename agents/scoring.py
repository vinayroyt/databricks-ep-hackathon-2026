"""Single-row Python port of the deterministic confidence/trust scoring from the
facility_intelligence_pipeline notebook (00_facility_pipeline, section [5]).

Used by the reclassification agent to rescore one facility after re-extraction,
without needing Spark. Keep in sync with that notebook if the scoring formula
changes there.
"""
import datetime

CAP_VOCAB = [
    "emergency_care", "icu", "operation_theatre", "general_surgery", "csection_obstetrics", "maternity",
    "dialysis", "blood_bank", "pharmacy", "ambulance", "laboratory", "radiology", "ct_scan", "mri", "xray",
    "ultrasound", "ecg", "endoscopy", "physiotherapy", "vaccination", "outpatient", "inpatient", "teleconsultation",
]
SPEC_VOCAB = [
    "general_medicine", "general_surgery", "cardiology", "orthopedics", "pediatrics", "obstetrics_gynecology",
    "ophthalmology", "ent", "dermatology", "neurology", "nephrology", "urology", "oncology", "gastroenterology",
    "pulmonology", "psychiatry", "dentistry", "radiology", "anesthesiology", "endocrinology", "ayush",
]

EVIDENCE_KEYWORDS = {
    "general_surgery": ["operation theat", "ot ", "surgeon", "anesth", "anaesth", "surgical"],
    "csection_obstetrics": ["labour", "labor room", "obstetric", "gynae", "maternity", "c-section", "caesar"],
    "icu": ["icu", "ventilator", "intensive care", "critical care"],
    "dialysis": ["dialysis", "nephrolog", "hemodialysis"],
    "blood_bank": ["blood bank", "transfusion"],
    "cardiology": ["cardiac", "cath lab", "ecg", "echo"],
    "oncology": ["oncolog", "cancer", "chemotherapy"],
}
HIGH_ACUITY = ["general_surgery", "icu", "dialysis", "oncology", "cardiology"]  # imaging deliberately excluded
BASIC_NAME = ["clinic", "dispensary", "sub cent", "sub-cent", "phc", "primary health", "polyclinic", "health post", "health centre", "health center"]

WEIGHTS = {"s_equipment_support": 0.35, "s_type_plausibility": 0.25, "s_coverage": 0.25, "s_recency": 0.15}

FLAG_KEYWORDS = {
    "has_emergency": ("emergency_care", "emergency", "casualty"),
    "has_icu": ("icu", "ventilator", "intensive care"),
    "has_operation_theatre": ("operation_theatre", "operation theat", "surgical"),
    "has_maternity": ("maternity", "labour", "obstetric"),
    "has_csection": ("csection_obstetrics", "c-section", "caesar"),
    "has_dialysis": ("dialysis", "nephrolog"),
    "has_blood_bank": ("blood_bank", "blood bank", "transfusion"),
    "has_ct_scan": ("ct_scan", "ct scan", "computed tomography"),
    "has_mri": ("mri", "magnetic resonance"),
    "has_xray": ("xray", "x-ray", "radiograph"),
    "has_ultrasound": ("ultrasound", "sonograph", "usg"),
    "has_laboratory": ("laboratory", "pathology", "blood test"),
    "has_cardiology": ("cardiology", "cardiac", "echo"),
    "has_oncology": ("oncology", "cancer", "chemotherapy"),
    "has_pediatrics": ("pediatrics", "paediatric", "child"),
    "has_orthopedics": ("orthopedics", "orthop", "fracture"),
    "has_ophthalmology": ("ophthalmology", "eye care", "cataract"),
}


def compute_flags(all_caps, all_text):
    """Recompute the facility_refined `has_*` UI flags from capabilities/specialties + raw text."""
    caps = set(all_caps or [])
    text = (all_text or "").lower()
    return {
        flag: (keywords[0] in caps) or any(kw in text for kw in keywords[1:])
        for flag, keywords in FLAG_KEYWORDS.items()
    }


def normalize_list(values, vocab=None):
    """Lowercase, trim, dedupe, drop empties. Optionally drop anything outside vocab
    (a safety net against the LLM returning terms outside the prompted vocabulary)."""
    out, seen = [], set()
    for v in values or []:
        if not isinstance(v, str):
            continue
        v = v.strip().lower()
        if not v or v in seen:
            continue
        if vocab is not None and v not in vocab:
            continue
        seen.add(v)
        out.append(v)
    return out


def score_facility(name, all_text, claimed_caps, num_doctors=None, capacity=None,
                    year_established=None, field_completeness_pct=None):
    """Returns confidence/confidence_band/evidence_level/trust_bucket/n_contradictions
    for one facility, mirroring the notebook's gold-table scoring."""
    text = (all_text or "").lower()
    name_l = (name or "").lower()

    looks_basic = (num_doctors is not None and num_doctors <= 3) or (capacity is not None and capacity <= 10)
    looks_basic = looks_basic or any(kw in name_l for kw in BASIC_NAME)

    caps = claimed_caps or []
    if caps:
        equip_supports, plausibilities = [], []
        for cap in caps:
            keywords = EVIDENCE_KEYWORDS.get(cap)
            support = 0.5 if keywords is None else (1.0 if any(kw in text for kw in keywords) else 0.0)
            equip_supports.append(support)
            plausible = 0.0 if (cap in HIGH_ACUITY and looks_basic and support < 1.0) else 1.0
            plausibilities.append(plausible)
        s_equipment_support = sum(equip_supports) / len(equip_supports)
        s_type_plausibility = min(plausibilities)
        n_contradictions = sum(1 for p in plausibilities if p == 0)
    else:
        s_equipment_support = None
        s_type_plausibility = None
        n_contradictions = 0

    s_coverage = (field_completeness_pct / 100.0) if field_completeness_pct is not None else None

    if year_established is None:
        s_recency = None
    else:
        this_year = datetime.date.today().year
        s_recency = max(0.0, min(1.0, (year_established - 1950) / (this_year - 1950)))

    scores = {"s_equipment_support": s_equipment_support, "s_type_plausibility": s_type_plausibility,
              "s_coverage": s_coverage, "s_recency": s_recency}

    num = den = 0.0
    navail = 0
    for key, weight in WEIGHTS.items():
        val = scores[key]
        if val is not None:
            num += val * weight
            den += weight
            navail += 1

    confidence = round((num / den) * 100, 1) if den > 0 else 0.0
    confidence_band = round(40.0 * (1.0 - navail / len(WEIGHTS)), 0)
    evidence_level = "High" if navail >= 3 else "Medium" if navail == 2 else "Low"

    if n_contradictions > 0:
        trust_bucket = "Contradicted"
    elif confidence >= 75 and evidence_level != "Low":
        trust_bucket = "Verified"
    elif confidence >= 50:
        trust_bucket = "Plausible"
    else:
        trust_bucket = "Unverified"

    return {
        "confidence": confidence,
        "confidence_band": confidence_band,
        "evidence_level": evidence_level,
        "trust_bucket": trust_bucket,
        "n_contradictions": n_contradictions,
        "claimed_caps": caps,
    }
