import argparse
import json
from pathlib import Path

from src.core.model_registry import load_registry, save_registry
from src.core.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Rollback active model to a previous path.")
    parser.add_argument("--type", choices=["matcher", "cf"], required=True)
    parser.add_argument("--path", required=True, help="Path to rollback to")
    args = parser.parse_args()

    settings = Settings()
    state = load_registry(settings.model_registry_dir)
    state[args.type] = args.path
    save_registry(settings.model_registry_dir, state)
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()

