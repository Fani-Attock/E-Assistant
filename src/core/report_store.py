from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import secrets
from typing import Any

from src.core.pdf_report import write_simple_pdf
from src.core.settings import Settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def serialize_report(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    for key in ("created_at", "updated_at"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.astimezone(timezone.utc).isoformat()
    return out


def _report_pdf_sections(report: dict[str, Any]) -> list[tuple[str, list[str]]]:
    payload = dict(report.get("payload") or {})
    summary = dict(payload.get("summary") or {})
    filters = dict(payload.get("filters") or {})
    items = list(payload.get("products") or payload.get("items") or [])
    meta_lines = []
    for key, value in filters.items():
        meta_lines.append(f"{key}: {value}")
    kpi_lines = [f"{key}: {value}" for key, value in summary.items()]
    product_lines: list[str] = []
    for row in items[:20]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("product_id") or "Product")
        source = str(row.get("source") or row.get("store_name") or "").strip()
        rating = row.get("source_rating", row.get("rating"))
        app_rating = row.get("app_rating")
        units_sold = row.get("units_sold", 0)
        revenue = row.get("revenue_pkr", 0)
        best_months = ", ".join(list(row.get("best_month_labels") or [])[:4])
        parts = [title]
        if source:
            parts.append(f"Source {source}")
        if rating is not None:
            parts.append(f"Source rating {rating}")
        if app_rating is not None:
            parts.append(f"App rating {app_rating}")
        parts.append(f"Units sold {units_sold}")
        parts.append(f"Revenue PKR {revenue}")
        if best_months:
            parts.append(f"Best months {best_months}")
        product_lines.append(" | ".join(parts))
    sections = []
    if meta_lines:
        sections.append(("CONTEXT", meta_lines))
    if kpi_lines:
        sections.append(("SUMMARY", kpi_lines))
    if product_lines:
        sections.append(("PRODUCTS", product_lines))
    notes = list(payload.get("notes") or [])
    if notes:
        sections.append(("NOTES", [str(x) for x in notes]))
    return sections


def save_report(
    *,
    settings: Settings,
    db,
    owner_user_id: str,
    report_type: str,
    title: str,
    payload: dict[str, Any],
    conversation_id: str | None = None,
    seller_id: str | None = None,
    source_kind: str | None = None,
) -> dict[str, Any]:
    now = utcnow()
    report_id = f"rpt_{secrets.token_hex(12)}"
    pdf_path = Path(settings.report_artifacts_dir) / f"{report_id}.pdf"
    write_simple_pdf(pdf_path, title, _report_pdf_sections({"payload": payload}))
    doc = {
        "report_id": report_id,
        "owner_user_id": owner_user_id,
        "report_type": report_type,
        "title": title,
        "conversation_id": conversation_id,
        "seller_id": seller_id,
        "source_kind": source_kind,
        "payload": payload,
        "pdf_path": str(pdf_path),
        "created_at": now,
        "updated_at": now,
    }
    db[settings.saved_reports_collection].insert_one(doc)
    return serialize_report(doc)


def list_reports(*, settings: Settings, db, owner_user_id: str, report_type: str | None = None) -> list[dict[str, Any]]:
    flt: dict[str, Any] = {"owner_user_id": owner_user_id}
    if report_type:
        flt["report_type"] = report_type
    rows = list(db[settings.saved_reports_collection].find(flt, {"_id": 0}).sort([("created_at", -1)]))
    return [serialize_report(row) for row in rows]


def get_report(*, settings: Settings, db, report_id: str, owner_user_id: str) -> dict[str, Any] | None:
    row = db[settings.saved_reports_collection].find_one({"report_id": report_id, "owner_user_id": owner_user_id}, {"_id": 0})
    return serialize_report(row) if row else None
