from __future__ import annotations

import json

from src.core.logging_utils import setup_logging
from src.core.normalize import normalize_search_query
from src.core.query_parser import ParsedQuery, parse_query
from src.core.settings import Settings

logger = setup_logging("core.llm_query_parser")


SYSTEM_PROMPT = (
    "You parse e-commerce search queries into strict JSON. "
    "Return only valid JSON with keys: cleaned_query, brand, max_price_pkr, "
    "min_rating, storage_gb, ram_gb, prefer_value. "
    "Use null for unknown values."
)


def _fallback(query: str) -> ParsedQuery:
    return parse_query(query)


def _extract_json_payload(text: str) -> dict:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object in model response")


def parse_query_with_llm(query: str, settings: Settings) -> ParsedQuery:
    if not settings.llm_enabled or not settings.groq_api_key:
        return _fallback(query)
    try:
        from groq import Groq  # type: ignore

        client = Groq(api_key=settings.groq_api_key)
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0,
            max_tokens=300,
        )
        text = (response.choices[0].message.content or "").strip()
        payload = _extract_json_payload(text)
        cleaned_query = normalize_search_query(str(payload.get("cleaned_query") or query))
        return ParsedQuery(
            cleaned_query=cleaned_query,
            brand=payload.get("brand"),
            max_price_pkr=float(payload["max_price_pkr"]) if payload.get("max_price_pkr") is not None else None,
            min_rating=float(payload["min_rating"]) if payload.get("min_rating") is not None else None,
            storage_gb=int(payload["storage_gb"]) if payload.get("storage_gb") is not None else None,
            ram_gb=int(payload["ram_gb"]) if payload.get("ram_gb") is not None else None,
            prefer_value=bool(payload.get("prefer_value", False)),
        )
    except Exception as exc:
        logger.warning(
            "LLM parse failed; using fallback parser. model=%s error=%s",
            settings.llm_model,
            str(exc),
        )
        logger.debug("LLM parse exception details", exc_info=True)
        return _fallback(query)
