from src.core.db import ensure_indexes
from src.core.settings import Settings


def main() -> None:
    settings = Settings()
    ensure_indexes(settings)
    print("Database indexes initialized successfully.")


if __name__ == "__main__":
    main()

