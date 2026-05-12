"""Loguru-based structured logger."""
from loguru import logger as log
import sys


def setup_logger() -> None:
    log.remove()
    log.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
               "<cyan>{name}:{function}:{line}</cyan> - <level>{message}</level>",
        colorize=True,
        backtrace=False,
        diagnose=False,
    )


__all__ = ["log", "setup_logger"]
