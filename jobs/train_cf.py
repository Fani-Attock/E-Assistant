import argparse
import json

from src.core.logging_utils import setup_logging
from src.core.model_registry import set_active_model
from src.core.settings import Settings
from src.ml.cf.train_cf import train_cf_model

logger = setup_logging("jobs.train_cf")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train collaborative filtering model from interactions.")
    parser.add_argument("--output", default="artifacts/cf_model")
    parser.add_argument("--components", type=int, default=32)
    parser.add_argument(
        "--only-real",
        action="store_true",
        help="Train CF using only real interactions (is_synthetic != true).",
    )
    args = parser.parse_args()

    logger.info(
        "Training CF model: output=%s components=%s only_real=%s",
        args.output,
        args.components,
        args.only_real,
    )
    settings = Settings()
    stats = train_cf_model(settings, output_dir=args.output, n_components=args.components, only_real=args.only_real)
    set_active_model(settings.model_registry_dir, "cf", args.output)
    logger.info("CF training complete")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
