"""Evidence-grounded care gap agent: explains *why* a region has a gap by
reasoning over verified capability data, trust scores, and cited evidence
snippets - the part Genie's NL-to-SQL can't do on free-text evidence.
"""
import mock_data
from common import get_client, run_agent


def list_regions():
    return mock_data.get_regions()


def get_region_summary(region_id: str):
    region = mock_data.get_region(region_id)
    if region is None:
        return {"error": f"unknown region_id {region_id}"}
    return region


def get_facility_details(region_id: str):
    return mock_data.get_facilities(region_id)


TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "list_regions",
            "description": "List all regions with their care gap scores.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_region_summary",
            "description": "Get the aggregated care gap score and summary for a region.",
            "parameters": {
                "type": "object",
                "properties": {"region_id": {"type": "string"}},
                "required": ["region_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_facility_details",
            "description": (
                "Get per-facility extracted capabilities, confidence scores, cited "
                "evidence snippets, and trust scores/flags for a region."
            ),
            "parameters": {
                "type": "object",
                "properties": {"region_id": {"type": "string"}},
                "required": ["region_id"],
            },
        },
    },
]

TOOL_IMPLS = {
    "list_regions": list_regions,
    "get_region_summary": get_region_summary,
    "get_facility_details": get_facility_details,
}

SYSTEM_PROMPT = (
    "You are a planner's assistant for the Care Gap Atlas. Explain care gaps by "
    "looking up the region's gap score and the underlying facility-level data. "
    "When a facility's claims look unreliable (low trust_score or trust_flags), "
    "call that out explicitly and cite the relevant evidence snippet and "
    "confidence score. Be concise and concrete - name specific facilities."
)


if __name__ == "__main__":
    client = get_client()

    print("--- Why does Bidar have a high care gap score for ICU access? ---")
    answer = run_agent(
        client,
        SYSTEM_PROMPT,
        TOOLS_SPEC,
        TOOL_IMPLS,
        "Why does the Bidar region have a high care gap score for ICU access? "
        "Which facilities claim ICU capability and how reliable are those claims?",
    )
    print(answer)

    print("\n--- Compare Bidar and Aurad ---")
    answer = run_agent(
        client,
        SYSTEM_PROMPT,
        TOOLS_SPEC,
        TOOL_IMPLS,
        "Compare the ICU care gap between Bidar and Aurad regions.",
    )
    print(answer)
