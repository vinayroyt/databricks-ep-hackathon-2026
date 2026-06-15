import json
import time
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

w = WorkspaceClient(profile="dbrx-hackathon-2026")  # uses CLI auth config automatically

ENDPOINT_NAME = "dbrxhack2026"

EXTRACTION_PROMPT_TEMPLATE = """Extract the following from this healthcare facility description.
Return ONLY valid JSON, no other text.

Fields to extract:
- specialties: list of medical specialties mentioned
- equipment: list of equipment mentioned
- bed_count: number or null
- confidence: object with a 0-1 score for each field above
- evidence: object with a short quote from the text supporting each extraction

Text: {text}

JSON:"""


def extract_one(text: str, max_retries: int = 3, backoff: float = 2.0) -> dict:
    """Call the serving endpoint for a single text record, with retries and JSON parsing."""
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(text=text)

    for attempt in range(max_retries):
        try:
            response = w.serving_endpoints.query(
                name=ENDPOINT_NAME,
                messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
                temperature=0.0,
                max_tokens=600,
            )
            content = response.choices[0].message.content.strip()

            # Strip markdown code fences if the model adds them
            if content.startswith("```"):
                content = content.strip("`")
                if content.startswith("json"):
                    content = content[4:].strip()

            parsed = json.loads(content)
            parsed["_raw_input"] = text
            parsed["_error"] = None
            return parsed

        except json.JSONDecodeError as e:
            if attempt == max_retries - 1:
                return {"_raw_input": text, "_error": f"json_decode_error: {e}", "_raw_response": content}
            time.sleep(backoff * (attempt + 1))

        except Exception as e:
            if attempt == max_retries - 1:
                return {"_raw_input": text, "_error": f"api_error: {e}"}
            time.sleep(backoff * (attempt + 1))


def extract_batch(texts: list[str], delay_between_calls: float = 0.0) -> list[dict]:
    """Process an array of strings, returning a list of extraction results in the same order."""
    results = []
    for i, text in enumerate(texts):
        result = extract_one(text)
        results.append(result)

        status = "OK" if result.get("_error") is None else f"ERROR: {result['_error']}"
        print(f"[{i + 1}/{len(texts)}] {status}")

        if delay_between_calls:
            time.sleep(delay_between_calls)

    return results


if __name__ == "__main__":
    # Example usage with a small sample
    sample_texts = [
        "24-hour multi-specialty hospital with ICU, ventilators, and dialysis unit. 50 beds.",
        "Primary health centre offering general OPD and basic maternity services.",
    ]

    results = extract_batch(sample_texts)

    for r in results:
        print(json.dumps(r, indent=2))

    # To save results:
    # with open("extraction_results.json", "w") as f:
    #     json.dump(results, f, indent=2)