from __future__ import annotations

import json
from pathlib import Path


def _registry_file(registry_dir: str) -> Path:
    p = Path(registry_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p / "active_models.json"


def load_registry(registry_dir: str) -> dict:
    fp = _registry_file(registry_dir)
    if not fp.exists():
        return {}
    return json.loads(fp.read_text(encoding="utf-8"))


def save_registry(registry_dir: str, data: dict) -> None:
    fp = _registry_file(registry_dir)
    fp.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_active_model(registry_dir: str, model_type: str, model_path: str) -> dict:
    data = load_registry(registry_dir)
    data[model_type] = model_path
    save_registry(registry_dir, data)
    return data


def get_active_model(registry_dir: str, model_type: str, default: str | None = None) -> str | None:
    data = load_registry(registry_dir)
    return data.get(model_type, default)

