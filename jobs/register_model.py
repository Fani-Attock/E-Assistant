import argparse
import json

from src.core.model_registry import load_registry, set_active_model
from src.core.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Register active model path in registry.")
    parser.add_argument("--type", choices=["matcher", "cf"], required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()

    settings = Settings()
    state = set_active_model(settings.model_registry_dir, args.type, args.path)
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()

