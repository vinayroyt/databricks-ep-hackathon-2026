"""Care Gap Atlas planner agent: combines the evidence-grounded explanation
tools and the region/facility annotation (write-back) tools into a single
MLflow ResponsesAgent, deployable to Databricks Model Serving.
"""
import json
import os
import sys
import uuid
import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

# When logged with code_paths=["agents"], MLflow copies this directory's
# contents to <model_root>/code/agents/ but loads this file itself from
# <model_root>/planner_agent.py - so sibling modules aren't on sys.path
# by default. Add both possible sibling locations.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (_THIS_DIR, os.path.join(_THIS_DIR, "code", "agents")):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

import annotation_agent, evidence_agent, reclassification_agent
from common import get_client, ENDPOINT_NAME

MAX_TURNS = 6

TOOLS_SPEC = []
TOOL_IMPLS = {}
for spec, impls in (
    (evidence_agent.TOOLS_SPEC, evidence_agent.TOOL_IMPLS),
    (annotation_agent.TOOLS_SPEC, annotation_agent.TOOL_IMPLS),
    (reclassification_agent.TOOLS_SPEC, reclassification_agent.TOOL_IMPLS),
):
    for tool in spec:
        if tool["function"]["name"] not in TOOL_IMPLS:
            TOOLS_SPEC.append(tool)
    TOOL_IMPLS.update(impls)

SYSTEM_PROMPT = (
    "You are the Care Gap Atlas planner assistant. You help planners understand "
    "regional healthcare capability gaps and manage their notes.\n\n"
    "- Explain care gaps by looking up a region's gap score and the underlying "
    "facility-level capability, confidence, and trust data. When a facility's "
    "claims look unreliable (low trust_score or trust_flags), call that out "
    "explicitly and cite the relevant evidence snippet and confidence score.\n"
    "- Save and recall planner notes at the region or facility level using the "
    "annotation tools, for use across sessions.\n"
    "- Facilities in facility_app carry a confidence score and trust_bucket "
    "(Verified/Plausible/Unverified/Contradicted). When a planner flags a facility "
    "as needing review, or asks to find facilities with unreliable data, use "
    "list_reclassification_candidates and get_facility_detail to investigate, and "
    "reclassify_facility to re-run extraction (folding in any correction the planner "
    "gives you) and update its confidence/trust_bucket. Always report the before/after.\n\n"
    "Be concise and concrete - name specific facilities and regions."
)


class CareGapPlannerAgent(ResponsesAgent):
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        client = get_client()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.prep_msgs_for_cc_llm(request.input)
        output_items = []

        for _ in range(MAX_TURNS):
            resp = client.chat.completions.create(
                model=ENDPOINT_NAME,
                messages=messages,
                tools=TOOLS_SPEC,
                tool_choice="auto",
                temperature=0,
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                output_items.append(self.create_text_output_item(msg.content or "", str(uuid.uuid4())))
                return ResponsesAgentResponse(output=output_items)

            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = TOOL_IMPLS[tc.function.name](**args)
                result_json = json.dumps(result)

                output_items.append(self.create_function_call_item(
                    id=str(uuid.uuid4()), call_id=tc.id, name=tc.function.name, arguments=tc.function.arguments,
                ))
                output_items.append(self.create_function_call_output_item(call_id=tc.id, output=result_json))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_json})

        output_items.append(self.create_text_output_item("(max turns reached without a final answer)", str(uuid.uuid4())))
        return ResponsesAgentResponse(output=output_items)


AGENT = CareGapPlannerAgent()
mlflow.models.set_model(AGENT)
