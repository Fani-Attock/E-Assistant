import json

from src.core.settings import Settings
from src.ml.marketplace_dl.infer import persist_marketplace_predictions


def main() -> None:
    settings = Settings()
    result = persist_marketplace_predictions(settings)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
