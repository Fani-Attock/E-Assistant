import argparse
import json

from src.core.settings import Settings
from src.ml.marketplace_dl.train import train_marketplace_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train marketplace deep learning model for demand/rating/seasonality.")
    parser.add_argument("--output", default=None, help="Output model directory. Defaults to settings MARKETPLACE_DL_MODEL_DIR.")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--hidden-dim", type=int, default=128)
    args = parser.parse_args()

    settings = Settings()
    stats = train_marketplace_model(
        settings,
        output_dir=args.output or settings.marketplace_dl_model_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        hidden_dim=args.hidden_dim,
    )
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
