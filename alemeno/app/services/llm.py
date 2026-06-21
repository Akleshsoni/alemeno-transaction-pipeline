"""
LLM service with:
- Gemini Flash (free tier) as primary
- OpenAI as fallback
- Batch calls (not one-per-row)
- Exponential backoff retry (up to 3 attempts)
- Graceful degradation: marks llm_failed=True instead of crashing
"""

import json
import logging
import re
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

VALID_CATEGORIES = [
    "Food", "Shopping", "Travel", "Transport",
    "Utilities", "Cash Withdrawal", "Entertainment", "Other",
]

CATEGORY_PROMPT_TEMPLATE = """You are a financial transaction classifier.

Classify each transaction into exactly one category from this list:
{categories}

Return ONLY a JSON object mapping each transaction_id (string) to its category.
No markdown, no explanation.

Transactions:
{transactions}
"""

NARRATIVE_PROMPT_TEMPLATE = """You are a financial analyst producing a structured summary.

Transaction data:
{data}

Return ONLY a valid JSON object with these exact keys:
- "total_spend_inr": number (sum of INR transactions)
- "total_spend_usd": number (sum of USD transactions)
- "top_merchants": list of top 3 merchant names by total spend
- "anomaly_count": integer
- "narrative": 2-3 sentence spending summary
- "risk_level": one of "low", "medium", "high"

No markdown. Return only the JSON object.
"""


def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(prompt)
    return response.text


def _call_openai(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


@retry(
    stop=stop_after_attempt(settings.LLM_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_llm(prompt: str) -> str:
    """Try Gemini first, fall back to OpenAI."""
    if settings.GEMINI_API_KEY:
        return _call_gemini(prompt)
    if settings.OPENAI_API_KEY:
        return _call_openai(prompt)
    raise RuntimeError("No LLM API key configured (GEMINI_API_KEY or OPENAI_API_KEY)")


def _extract_json(text: str) -> dict:
    """Strip markdown fences and parse JSON."""
    text = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
    return json.loads(text)


def classify_categories_batch(
    transactions: list[dict],
) -> dict[str, str]:
    """
    Classify categories in batches.
    Returns {row_index_str: category}.
    Marks llm_failed for any row that couldn't be classified.
    """
    results: dict[str, str] = {}
    batch_size = settings.LLM_BATCH_SIZE

    for i in range(0, len(transactions), batch_size):
        batch = transactions[i : i + batch_size]
        formatted = "\n".join(
            f"- id={t['_idx']} merchant={t['merchant']} amount={t['amount']} "
            f"currency={t['currency']} notes={t.get('notes', '')}"
            for t in batch
        )
        prompt = CATEGORY_PROMPT_TEMPLATE.format(
            categories=", ".join(VALID_CATEGORIES),
            transactions=formatted,
        )
        try:
            raw = _call_llm(prompt)
            parsed = _extract_json(raw)
            for item in batch:
                idx = str(item["_idx"])
                cat = parsed.get(idx, "Other")
                if cat not in VALID_CATEGORIES:
                    cat = "Other"
                results[idx] = cat
        except Exception as exc:
            logger.error("LLM category batch failed: %s", exc)
            for item in batch:
                results[str(item["_idx"])] = "__FAILED__"

    return results


def generate_narrative(summary_data: dict) -> dict:
    """
    Generate a structured narrative summary via LLM.
    Returns the parsed dict or a safe fallback.
    """
    prompt = NARRATIVE_PROMPT_TEMPLATE.format(data=json.dumps(summary_data, default=str))
    try:
        raw = _call_llm(prompt)
        return _extract_json(raw)
    except Exception as exc:
        logger.error("LLM narrative failed: %s", exc)
        return {
            "total_spend_inr": summary_data.get("total_spend_inr", 0),
            "total_spend_usd": summary_data.get("total_spend_usd", 0),
            "top_merchants": summary_data.get("top_merchants", []),
            "anomaly_count": summary_data.get("anomaly_count", 0),
            "narrative": "Summary unavailable due to LLM failure.",
            "risk_level": "medium",
        }
