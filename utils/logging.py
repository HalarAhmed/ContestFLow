"""Structured logging with optional request ID."""
import logging
import sys
from typing import Any

from config import settings


def setup_logging(level: str | None = None) -> None:
    level = level or settings.LOG_LEVEL
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_extra(logger: logging.Logger, msg: str, **kwargs: Any) -> None:
    if kwargs:
        logger.info("%s %s", msg, kwargs)
    else:
        logger.info("%s", msg)
