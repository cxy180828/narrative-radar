"""
Structured logging with rotation support.
"""

import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler


class JsonFormatter(logging.Formatter):
    """JSON structured log formatter."""

    def format(self, record):
        log_data = {
            "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_data["data"] = record.extra_data
        return json.dumps(log_data, ensure_ascii=False)


class SimpleFormatter(logging.Formatter):
    """Human-readable log formatter."""

    def __init__(self):
        super().__init__("[%(asctime)s] %(levelname)s %(module)s: %(message)s")


def setup_logger(
    name: str = "radar",
    log_dir: str = None,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    json_format: bool = True,
) -> logging.Logger:
    """Set up logger with file rotation and console output."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid adding duplicate handlers
    if logger.handlers:
        return logger

    # Console handler (always human-readable)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(SimpleFormatter())
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    # File handler (with rotation)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "radar.log")
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count
        )
        formatter = JsonFormatter() if json_format else SimpleFormatter()
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    """Get the radar logger instance."""
    return logging.getLogger("radar")
