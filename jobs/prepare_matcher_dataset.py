import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from src.core.logging_utils import setup_logging
from src.core.settings import Settings


logger = setup_logging("jobs.prepare_matcher_dataset")


def parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def is_cross_source(row: dict) -> bool:
    a = str(row.get("source_a") or "").strip().lower()
    b = str(row.get("source_b") or "").strip().lower()
    return bool(a and b and a != b)


def load_pairs_from_mongo(settings: Settings, limit: int) -> list[dict]:
    col = MongoClient(settings.mongo_uri)[settings.app_db_name][settings.match_pairs_collection]
    cursor = col.find(
        {"label": {"$in": [0, 1]}},
        {
            "_id": 0,
            "pair_id": 1,
            "title_a": 1,
            "title_b": 1,
            "source_a": 1,
            "source_b": 1,
            "label": 1,
            "label_source": 1,
            "labeled_at": 1,
            "created_at": 1,
        },
    ).limit(limit)
    rows = []
    for row in cursor:
        labeled_at = parse_dt(row.get("labeled_at"))
        created_at = parse_dt(row.get("created_at"))
        rows.append(
            {
                "pair_id": str(row.get("pair_id") or ""),
                "text_a": str(row.get("title_a") or ""),
                "text_b": str(row.get("title_b") or ""),
                "source_a": row.get("source_a"),
                "source_b": row.get("source_b"),
                "label": int(row.get("label")),
                "label_source": row.get("label_source"),
                "labeled_at": to_iso(labeled_at),
                "created_at": to_iso(created_at),
                "_ts": labeled_at or created_at,
            }
        )
    return rows


def load_pairs_from_jsonl(path: Path, limit: int) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("label") is None:
                continue
            pair_id = str(row.get("pair_id") or "").strip()
            if not pair_id:
                raw = f"{row.get('text_a', '')}|{row.get('text_b', '')}"
                pair_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()
            labeled_at = parse_dt(row.get("labeled_at"))
            created_at = parse_dt(row.get("created_at"))
            rows.append(
                {
                    "pair_id": pair_id,
                    "text_a": str(row["text_a"]),
                    "text_b": str(row["text_b"]),
                    "source_a": row.get("source_a"),
                    "source_b": row.get("source_b"),
                    "label": int(float(row["label"]) >= 0.5),
                    "label_source": row.get("label_source"),
                    "labeled_at": to_iso(labeled_at),
                    "created_at": to_iso(created_at),
                    "_ts": labeled_at or created_at,
                }
            )
            if len(rows) >= limit:
                break
    return rows


def stable_hash(value: str) -> int:
    return int(hashlib.sha1(value.encode("utf-8")).hexdigest(), 16)


def _target_val_size(n: int, val_ratio: float) -> int:
    if n <= 1:
        return 0
    n_val = int(round(n * val_ratio))
    if n_val <= 0:
        n_val = 1
    if n_val >= n:
        n_val = n - 1
    return n_val


def split_stratified_hash(
    rows: list[dict],
    *,
    val_ratio: float,
    seed: int,
    cross_source_val_only: bool,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    by_label: dict[int, list[dict]] = {0: [], 1: []}
    for row in rows:
        by_label[int(row["label"])].append(row)

    train: list[dict] = []
    val: list[dict] = []
    stats: dict[str, Any] = {"mode": "stratified_hash", "cross_source_val_only": cross_source_val_only, "labels": {}}

    for label, group in by_label.items():
        keyed = []
        for row in group:
            pid = row["pair_id"] or hashlib.sha1(f"{row['text_a']}|{row['text_b']}".encode("utf-8")).hexdigest()
            h = stable_hash(f"{seed}|{pid}")
            keyed.append((h, row))
        keyed.sort(key=lambda x: x[0])
        ordered = [row for _, row in keyed]
        n_val_target = _target_val_size(len(ordered), val_ratio)

        if cross_source_val_only:
            cross = [r for r in ordered if is_cross_source(r)]
            non_cross = [r for r in ordered if not is_cross_source(r)]
            n_val = min(n_val_target, len(cross))
            val_rows = cross[:n_val]
            train_rows = cross[n_val:] + non_cross
        else:
            n_val = n_val_target
            val_rows = ordered[:n_val]
            train_rows = ordered[n_val:]

        val.extend(val_rows)
        train.extend(train_rows)
        stats["labels"][str(label)] = {
            "total": len(group),
            "train": len(train_rows),
            "val": len(val_rows),
            "val_target": n_val_target,
            "val_cross_source": sum(1 for r in val_rows if is_cross_source(r)),
        }
        logger.info(
            "Split(label=%s): total=%s train=%s val=%s mode=%s cross_only=%s",
            label,
            len(group),
            len(train_rows),
            len(val_rows),
            "stratified_hash",
            cross_source_val_only,
        )

    train.sort(key=lambda r: r["pair_id"])
    val.sort(key=lambda r: r["pair_id"])
    return train, val, stats


def split_time_based(
    rows: list[dict],
    *,
    val_ratio: float,
    cross_source_val_only: bool,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    by_label: dict[int, list[dict]] = {0: [], 1: []}
    for row in rows:
        by_label[int(row["label"])].append(row)

    train: list[dict] = []
    val: list[dict] = []
    stats: dict[str, Any] = {"mode": "time_based", "cross_source_val_only": cross_source_val_only, "labels": {}}
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    for label, group in by_label.items():
        eligible = [r for r in group if (is_cross_source(r) or not cross_source_val_only)]
        ineligible = [r for r in group if r not in eligible]
        eligible.sort(key=lambda r: r.get("_ts") or epoch)
        n_val_target = _target_val_size(len(group), val_ratio)
        n_val = min(n_val_target, len(eligible))
        val_rows = eligible[-n_val:] if n_val > 0 else []
        train_rows = eligible[:-n_val] + ineligible if n_val > 0 else eligible + ineligible

        val.extend(val_rows)
        train.extend(train_rows)
        stats["labels"][str(label)] = {
            "total": len(group),
            "train": len(train_rows),
            "val": len(val_rows),
            "val_target": n_val_target,
            "val_cross_source": sum(1 for r in val_rows if is_cross_source(r)),
        }
        logger.info(
            "Split(label=%s): total=%s train=%s val=%s mode=%s cross_only=%s",
            label,
            len(group),
            len(train_rows),
            len(val_rows),
            "time_based",
            cross_source_val_only,
        )

    train.sort(key=lambda r: r["pair_id"])
    val.sort(key=lambda r: r["pair_id"])
    return train, val, stats


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            clean = {k: v for k, v in row.items() if k != "_ts"}
            f.write(json.dumps(clean, ensure_ascii=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare fixed train/validation JSONL for matcher.")
    parser.add_argument("--input-jsonl", default="", help="Optional labeled JSONL input. If omitted, reads from Mongo.")
    parser.add_argument("--output-dir", default="artifacts/matcher_data")
    parser.add_argument("--limit", type=int, default=200000)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--split-mode",
        choices=["stratified_hash", "time_based"],
        default="stratified_hash",
        help="Validation split strategy.",
    )
    parser.add_argument(
        "--cross-source-val-only",
        action="store_true",
        help="Build validation set using cross-source pairs only where possible.",
    )
    args = parser.parse_args()

    if not (0.05 <= args.val_ratio <= 0.5):
        raise SystemExit("val-ratio must be between 0.05 and 0.5")

    settings = Settings()
    if args.input_jsonl:
        rows = load_pairs_from_jsonl(Path(args.input_jsonl), args.limit)
        source = "jsonl"
    else:
        rows = load_pairs_from_mongo(settings, args.limit)
        source = "mongo"

    if len(rows) < 100:
        raise RuntimeError("Need at least 100 labeled pairs before creating train/val split.")

    dedup: dict[str, dict] = {}
    for row in rows:
        key = row["pair_id"] or hashlib.sha1(f"{row['text_a']}|{row['text_b']}".encode("utf-8")).hexdigest()
        prev = dedup.get(key)
        if prev is None:
            dedup[key] = row | {"pair_id": key}
            continue
        prev_ts = prev.get("_ts")
        cur_ts = row.get("_ts")
        if (cur_ts or datetime(1970, 1, 1, tzinfo=timezone.utc)) >= (prev_ts or datetime(1970, 1, 1, tzinfo=timezone.utc)):
            dedup[key] = row | {"pair_id": key}
    rows = list(dedup.values())

    if args.split_mode == "time_based":
        train_rows, val_rows, split_stats = split_time_based(
            rows, val_ratio=args.val_ratio, cross_source_val_only=args.cross_source_val_only
        )
    else:
        train_rows, val_rows, split_stats = split_stratified_hash(
            rows,
            val_ratio=args.val_ratio,
            seed=args.seed,
            cross_source_val_only=args.cross_source_val_only,
        )

    out_dir = Path(args.output_dir)
    all_path = out_dir / "labeled_all.jsonl"
    train_path = out_dir / "train.jsonl"
    val_path = out_dir / "val.jsonl"
    manifest_path = out_dir / "manifest.json"

    write_jsonl(all_path, rows)
    write_jsonl(train_path, train_rows)
    write_jsonl(val_path, val_rows)

    manifest = {
        "source": source,
        "seed": args.seed,
        "val_ratio": args.val_ratio,
        "split_mode": args.split_mode,
        "cross_source_val_only": args.cross_source_val_only,
        "split_stats": split_stats,
        "counts": {
            "all": len(rows),
            "train": len(train_rows),
            "val": len(val_rows),
            "all_pos": sum(1 for r in rows if int(r["label"]) == 1),
            "all_neg": sum(1 for r in rows if int(r["label"]) == 0),
            "train_pos": sum(1 for r in train_rows if int(r["label"]) == 1),
            "train_neg": sum(1 for r in train_rows if int(r["label"]) == 0),
            "val_pos": sum(1 for r in val_rows if int(r["label"]) == 1),
            "val_neg": sum(1 for r in val_rows if int(r["label"]) == 0),
            "val_cross_source": sum(1 for r in val_rows if is_cross_source(r)),
            "val_same_source": sum(1 for r in val_rows if not is_cross_source(r)),
        },
        "files": {
            "all": str(all_path),
            "train": str(train_path),
            "val": str(val_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

