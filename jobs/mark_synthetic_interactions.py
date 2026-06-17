import argparse

from pymongo import MongoClient

from src.core.logging_utils import setup_logging
from src.core.settings import Settings


logger = setup_logging("jobs.mark_synthetic_interactions")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill is_synthetic flag on legacy interactions.")
    parser.add_argument(
        "--synthetic-user-prefix",
        default="synthetic_user_",
        help="User ID prefix used by synthetic bootstrap events.",
    )
    parser.add_argument(
        "--mark-missing-as-real",
        action="store_true",
        help="Also mark remaining records with missing is_synthetic as False.",
    )
    args = parser.parse_args()

    settings = Settings()
    col = MongoClient(settings.mongo_uri)[settings.app_db_name][settings.interactions_collection]

    result_syn = col.update_many(
        {
            "is_synthetic": {"$exists": False},
            "user_id": {"$regex": f"^{args.synthetic_user_prefix}"},
        },
        {"$set": {"is_synthetic": True}},
    )
    logger.info(
        "Marked synthetic interactions: matched=%s modified=%s prefix=%s",
        result_syn.matched_count,
        result_syn.modified_count,
        args.synthetic_user_prefix,
    )

    if args.mark_missing_as_real:
        result_real = col.update_many(
            {"is_synthetic": {"$exists": False}},
            {"$set": {"is_synthetic": False}},
        )
        logger.info(
            "Marked non-synthetic interactions: matched=%s modified=%s",
            result_real.matched_count,
            result_real.modified_count,
        )


if __name__ == "__main__":
    main()

