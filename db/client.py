"""MongoDB connection and database access."""
import os
import sys
from typing import TYPE_CHECKING

from pymongo import MongoClient
from pymongo.database import Database

from config import settings
from utils.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        kwargs = {"serverSelectionTimeoutMS": 10000}
        uri = settings.MONGODB_URI or ""
        if "mongodb+srv://" in uri:
            try:
                import certifi
                ca_path = certifi.where()
                os.environ.setdefault("SSL_CERT_FILE", ca_path)
                os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)
                kwargs["tlsCAFile"] = ca_path
            except ImportError:
                pass
        _client = MongoClient(uri, **kwargs)
        logger.info("MongoDB client connected to %s", uri)
    return _client


def get_db() -> Database:
    return get_client()[settings.MONGODB_DB]


def ensure_indexes() -> None:
    """Create indexes for all collections. Run once at startup or via script."""
    db = get_db()

    db.user_config.create_index("user_id", unique=True)
    db.contests.create_index([("platform", 1), ("external_id", 1)], unique=True)
    db.contests.create_index("start_time_utc")
    db.contest_registrations.create_index([("user_id", 1), ("contest_id", 1)])
    db.contest_registrations.create_index("created_at")
    db.contest_results.create_index("contest_id")
    db.contest_results.create_index([("user_id", 1), ("contest_id", 1)], unique=True)
    db.practice_solves.create_index([("platform", 1), ("user_id", 1), ("problem_id", 1)], unique=True)
    db.practice_solves.create_index("solved_at")
    db.rating_history.create_index([("user_id", 1), ("platform", 1)])
    db.rating_history.create_index("timestamp")
    db.notification_log.create_index("sent_at")
    db.notification_log.create_index("event_type")
    db.analytics_cache.create_index("user_id", unique=True)

    logger.info("MongoDB indexes ensured")
