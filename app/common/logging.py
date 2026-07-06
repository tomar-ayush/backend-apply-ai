import logging
from typing import Optional


class SimpleFormatter(logging.Formatter):
    def __init__(self) -> None:
        fmt = "[%(asctime)s][%(filename)s][%(levelname)s] %(message)s"
        # ISO-like timestamp with timezone offset
        datefmt = "%Y-%m-%dT%H:%M:%S%z"
        super().__init__(fmt=fmt, datefmt=datefmt)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with a simple, consistent formatter.

    Format: [timestamp][filename][LEVEL] message
    """
    root = logging.getLogger()
    # avoid double handlers if configure_logging called multiple times
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(SimpleFormatter())
        root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger configured with the common formatting.

    Call `configure_logging()` early (e.g. in `app.main`) to ensure the root
    handler and formatter are installed. If not called explicitly, this will
    still return a logger but the caller should ensure configuration is done.
    """
    return logging.getLogger(name)
