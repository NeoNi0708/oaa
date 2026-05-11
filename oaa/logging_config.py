"""Centralized logging configuration for OAA."""
import logging
import os
import sys

LOG_FORMAT = "[%(asctime)s] [%(levelname)-5s] [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_log_initialized = False


def setup_logging(level: int = logging.INFO, log_file: str = ""):
    """Configure root logger with console handler and optional file handler."""
    global _log_initialized

    root = logging.getLogger("oaa")
    root.setLevel(level)

    if not root.handlers:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        root.addHandler(console)

        if log_file:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
            root.addHandler(file_handler)

    _log_initialized = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module, ensuring setup has been run."""
    if not _log_initialized:
        setup_logging()
    return logging.getLogger(f"oaa.{name}")
