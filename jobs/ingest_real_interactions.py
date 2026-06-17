import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pymongo import ASCENDING, MongoClient, UpdateOne
from tqdm import tqdm

from src.core.logging_utils import setup_logging
from src.core.normalize import offer_id_from_source_link, stable_fingerprint
from src.core.settings import Settings

logger = setup_logging("jobs.ingest_real_interactions")

ALLOWED_EVENT_TYPES = {"view", "click", "save", "purchase"}
EVENT_WEIGHTS = {
    "view": 1.0,
    "click": 2.0,
    "save": 3.0,
    "purchase": 5.0,
}


def pick_first(record: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return default


def detect_format(path: Path, fmt: str) -> str:
    if fmt != "auto":
        return fmt
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    raise ValueError(f"Unable to auto-detect format for {path.name}. Use --format.")


def iter_records(path: Path, fmt: str):
    if fmt == "jsonl":
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"Line {line_no}: record must be a JSON object.")
                yield line_no, row
        return

    if fmt == "json":
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            rows = payload.get("events")
            if rows is None:
                raise ValueError("JSON object input must contain an 'events' array.")
        elif isinstance(payload, list):
            rows = payload
        else:
            raise ValueError("JSON input must be an array or an object with an 'events' array.")
        for idx, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"Item {idx}: record must be an object.")
            yield idx, row
        return

    if fmt == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for idx, row in enumerate(reader, start=1):
                yield idx, dict(row)
        return

    raise ValueError(f"Unsupported format: {fmt}")


def parse_event_ts(value: Any) -> datetime:
    if value in (None, ""):
        return datetime.now(timezone.utc)

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        ts = float(value)
        if abs(ts) >= 1_000_000_000_000:  # milliseconds
            ts = ts / 1000.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        raw = str(value).strip()
        if not raw:
            return datetime.now(timezone.utc)
        try:
            ts = float(raw)
            if abs(ts) >= 1_000_000_000_000:  # milliseconds
                ts = ts / 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except ValueError:
            iso = raw.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError as exc:
                raise ValueError(f"Invalid event timestamp: {value}") from exc

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_event(
    record: dict[str, Any],
    *,
    default_event_type: str,
    default_source: str | None,
) -> dict[str, Any]:
    user_id = str(pick_first(record, ["user_id", "user", "uid"], default="")).strip()
    if not user_id:
        raise ValueError("user_id is required")

    event_type = str(
        pick_first(
            record,
            ["event_type", "event", "action", "type"],
            default=default_event_type,
        )
    ).strip().lower()
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"Unsupported event_type={event_type}")

    source_raw = pick_first(record, ["source", "site", "store"], default=default_source)
    source = str(source_raw).strip().lower() if source_raw not in (None, "") else None
    link = pick_first(record, ["link", "url", "product_url"], default=None)
    link = str(link).strip() if link not in (None, "") else None
    offer_id = pick_first(record, ["offer_id", "product_id", "item_id"], default=None)
    offer_id = str(offer_id).strip() if offer_id not in (None, "") else None

    if not offer_id:
        if not (source and link):
            raise ValueError("offer_id is required unless both source and link are provided")
        offer_id = offer_id_from_source_link(source, link)

    event_ts = parse_event_ts(pick_first(record, ["event_ts", "timestamp", "ts", "time", "datetime"], default=None))

    raw_weight = pick_first(record, ["weight", "event_weight"], default=None)
    if raw_weight in (None, ""):
        weight = EVENT_WEIGHTS[event_type]
    else:
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid weight={raw_weight}") from exc

    explicit_event_id = pick_first(record, ["event_id", "client_event_id"], default=None)
    event_id = str(explicit_event_id).strip() if explicit_event_id not in (None, "") else None
    if not event_id:
        event_id = stable_fingerprint(
            {
                "user_id": user_id,
                "offer_id": offer_id,
                "event_type": event_type,
                "event_ts": event_ts.isoformat(),
                "source": source,
                "link": link,
            }
        )

    known_keys = {
        "user_id",
        "user",
        "uid",
        "event_type",
        "event",
        "action",
        "type",
        "source",
        "site",
        "store",
        "link",
        "url",
        "product_url",
        "offer_id",
        "product_id",
        "item_id",
        "event_ts",
        "timestamp",
        "ts",
        "time",
        "datetime",
        "weight",
        "event_weight",
        "event_id",
        "client_event_id",
    }
    meta = {k: v for k, v in record.items() if k not in known_keys}

    doc: dict[str, Any] = {
        "event_id": event_id,
        "user_id": user_id,
        "offer_id": offer_id,
        "event_type": event_type,
        "weight": weight,
        "source": source,
        "link": link,
        "event_ts": event_ts,
        "is_synthetic": False,
        "ingested_at": datetime.now(timezone.utc),
    }
    if meta:
        doc["meta"] = meta
    return doc


def flush_batch(collection, ops: list[UpdateOne]) -> tuple[int, int]:
    if not ops:
        return 0, 0
    result = collection.bulk_write(ops, ordered=False)
    inserted = int(result.upserted_count)
    deduped = len(ops) - inserted
    ops.clear()
    return inserted, deduped


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest real (non-synthetic) user interactions from CSV/JSON/JSONL.")
    parser.add_argument("--input", required=True, help="Path to interaction file (csv/json/jsonl).")
    parser.add_argument("--format", choices=["auto", "csv", "json", "jsonl"], default="auto")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-events", type=int, default=0, help="Stop after N processed rows (0=all).")
    parser.add_argument("--default-event-type", choices=sorted(ALLOWED_EVENT_TYPES), default="view")
    parser.add_argument("--default-source", default=None)
    parser.add_argument("--strict", action="store_true", help="Stop on first invalid row.")
    parser.add_argument("--dry-run", action="store_true", help="Validate input without writing to Mongo.")
    args = parser.parse_args()

    source_path = Path(args.input)
    if not source_path.exists():
        raise FileNotFoundError(f"Input file not found: {source_path}")

    fmt = detect_format(source_path, args.format)
    settings = Settings()
    db = MongoClient(settings.mongo_uri)[settings.app_db_name]
    col = db[settings.interactions_collection]
    if not args.dry_run:
        col.create_index([("event_id", ASCENDING)], unique=True, sparse=True, name="uq_event_id")

    logger.info(
        "Starting real interaction ingest: input=%s format=%s dry_run=%s",
        source_path,
        fmt,
        args.dry_run,
    )

    processed = 0
    inserted = 0
    deduped = 0
    invalid = 0
    ops: list[UpdateOne] = []

    rows = iter_records(source_path, fmt)
    progress = tqdm(rows, desc="ingest_real_interactions", unit="row")
    for row_no, record in progress:
        if args.max_events > 0 and processed >= args.max_events:
            break
        try:
            doc = normalize_event(
                record,
                default_event_type=args.default_event_type,
                default_source=args.default_source,
            )
        except Exception as exc:
            invalid += 1
            logger.warning("Skipping invalid row=%s error=%s", row_no, exc)
            if args.strict:
                raise RuntimeError(f"Invalid row {row_no}: {exc}") from exc
            continue

        processed += 1
        if args.dry_run:
            progress.set_postfix(processed=processed, inserted=0, deduped=0, invalid=invalid)
            continue

        ops.append(UpdateOne({"event_id": doc["event_id"]}, {"$setOnInsert": doc}, upsert=True))
        if len(ops) >= args.batch_size:
            b_ins, b_dedup = flush_batch(col, ops)
            inserted += b_ins
            deduped += b_dedup
        if processed % 100 == 0:
            progress.set_postfix(processed=processed, inserted=inserted, deduped=deduped, invalid=invalid)

    if not args.dry_run and ops:
        b_ins, b_dedup = flush_batch(col, ops)
        inserted += b_ins
        deduped += b_dedup

    progress.set_postfix(processed=processed, inserted=inserted, deduped=deduped, invalid=invalid)
    progress.close()

    summary = {
        "processed": processed,
        "inserted": inserted,
        "deduped": deduped,
        "invalid": invalid,
        "dry_run": args.dry_run,
        "collection": settings.interactions_collection,
        "app_db": settings.app_db_name,
    }
    logger.info("Real interaction ingest complete: %s", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
