"""Placeholder data shaped like the eventual extraction + trust-scoring + aggregation
output (Care Gap Atlas schema). Swap for real pipeline output once it lands."""

FACILITIES = [
    {
        "facility_id": "F001",
        "name": "District General Hospital, Bidar",
        "region_id": "R01",
        "raw_text": "24-hour multi-specialty hospital with ICU, ventilators, and dialysis unit. 50 beds.",
        "extracted": {
            "specialties": ["multi-specialty", "ICU", "dialysis"],
            "equipment": ["ventilators"],
            "bed_count": 50,
            "icu_beds": 0,
            "confidence": {
                "specialties": 0.95,
                "equipment": 0.9,
                "bed_count": 0.99,
                "icu_beds": 0.35,
            },
            "evidence": {
                "specialties": "24-hour multi-specialty hospital with ICU, ventilators, and dialysis unit.",
                "icu_beds": "ICU is mentioned but no explicit ICU bed count is given in the text.",
            },
        },
        "trust_score": 0.42,
        "trust_flags": ["claims ICU capability but reports 0 ICU beds"],
    },
    {
        "facility_id": "F002",
        "name": "Bidar Rural Health Clinic",
        "region_id": "R01",
        "raw_text": "Primary health centre offering general OPD and basic maternity services.",
        "extracted": {
            "specialties": ["general OPD", "maternity"],
            "equipment": [],
            "bed_count": None,
            "icu_beds": 0,
            "confidence": {
                "specialties": 0.9,
                "equipment": 0.0,
                "bed_count": 0.0,
                "icu_beds": 0.0,
            },
            "evidence": {
                "specialties": "Primary health centre offering general OPD and basic maternity services.",
            },
        },
        "trust_score": 0.95,
        "trust_flags": [],
    },
    {
        "facility_id": "F003",
        "name": "Bidar City Nursing Home",
        "region_id": "R01",
        "raw_text": "Multi-specialty nursing home, claims ICU and dialysis, 12 beds total.",
        "extracted": {
            "specialties": ["multi-specialty", "ICU", "dialysis"],
            "equipment": [],
            "bed_count": 12,
            "icu_beds": 0,
            "confidence": {
                "specialties": 0.85,
                "equipment": 0.0,
                "bed_count": 0.9,
                "icu_beds": 0.3,
            },
            "evidence": {
                "specialties": "Multi-specialty nursing home, claims ICU and dialysis, 12 beds total.",
                "icu_beds": "Total bed count (12) is given, but no ICU-specific beds are broken out.",
            },
        },
        "trust_score": 0.38,
        "trust_flags": ["claims ICU capability but reports 0 ICU beds", "total beds (12) too low to plausibly include an ICU"],
    },
    {
        "facility_id": "F004",
        "name": "Aurad Taluk Hospital",
        "region_id": "R02",
        "raw_text": "Taluk-level hospital with general medicine, surgery, and a 10-bed ICU. 80 beds total.",
        "extracted": {
            "specialties": ["general medicine", "surgery", "ICU"],
            "equipment": ["ventilators"],
            "bed_count": 80,
            "icu_beds": 10,
            "confidence": {
                "specialties": 0.95,
                "equipment": 0.8,
                "bed_count": 0.95,
                "icu_beds": 0.92,
            },
            "evidence": {
                "specialties": "general medicine, surgery, and a 10-bed ICU",
                "icu_beds": "a 10-bed ICU",
            },
        },
        "trust_score": 0.91,
        "trust_flags": [],
    },
]

REGIONS = [
    {
        "region_id": "R01",
        "region_name": "Bidar",
        "gap_score": 0.78,
        "claimed_icu_facilities": 2,
        "verified_icu_facilities": 0,
        "summary": "2 of 3 facilities claim ICU capability, but 0 have any verified ICU beds.",
    },
    {
        "region_id": "R02",
        "region_name": "Aurad",
        "gap_score": 0.15,
        "claimed_icu_facilities": 1,
        "verified_icu_facilities": 1,
        "summary": "1 facility claims ICU capability and its claim is verified by reported ICU beds.",
    },
]


def get_regions():
    return REGIONS


def _resolve_region_id(region_id_or_name):
    key = region_id_or_name.strip().lower()
    for r in REGIONS:
        if r["region_id"].lower() == key or r["region_name"].lower() == key:
            return r["region_id"]
    return region_id_or_name


def get_region(region_id):
    region_id = _resolve_region_id(region_id)
    return next((r for r in REGIONS if r["region_id"] == region_id), None)


def get_facilities(region_id):
    region_id = _resolve_region_id(region_id)
    return [f for f in FACILITIES if f["region_id"] == region_id]


def get_facility(facility_id):
    key = facility_id.strip().lower()
    return next((f for f in FACILITIES if f["facility_id"].lower() == key or f["name"].lower() == key), None)
