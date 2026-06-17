import json
import os
import warnings
from lakebase import get_workspace_client

warnings.filterwarnings("ignore", category=DeprecationWarning)

ENDPOINT_NAME = os.getenv("RECLASSIFY_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")


def get_client():
    return get_workspace_client().serving_endpoints.get_open_ai_client()


def run_agent(client, system_prompt, tools_spec, tool_impls, user_message, max_turns=5, verbose=True):
    """Run a tool-calling agent loop until the model returns a final text answer."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for _ in range(max_turns):
        resp = client.chat.completions.create(
            model=ENDPOINT_NAME,
            messages=messages,
            tools=tools_spec,
            tool_choice="auto",
            temperature=0,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            return msg.content

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            if verbose:
                print(f"  [tool call] {tc.function.name}({args})")
            result = tool_impls[tc.function.name](**args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    return "(max turns reached without a final answer)"
