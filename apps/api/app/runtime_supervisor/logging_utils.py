from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class ComponentAdapter(logging.LoggerAdapter):
    def process(self, msg: object, kwargs: dict[str, object]) -> tuple[object, dict[str, object]]:
        kwargs.setdefault("extra", {})
        extra = kwargs["extra"]
        if isinstance(extra, dict):
            extra.setdefault("component", self.extra["component"])
        return msg, kwargs


def configure_logging(directory: Path, max_bytes: int, backup_count: int) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jarvis.supervisor")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(
        directory / "supervisor.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s [%(component)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = __import__("time").gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def component_logger(logger: logging.Logger, component: str) -> ComponentAdapter:
    return ComponentAdapter(logger, {"component": component})


def child_output_logger(
    directory: Path, component: str, max_bytes: int, backup_count: int
) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"jarvis.supervisor.child.{component}.{hash(directory.resolve())}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(
        directory / f"{component}.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)sZ %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = __import__("time").gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
