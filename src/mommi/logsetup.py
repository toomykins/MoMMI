from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
RESET = "\033[0m"
DIM = "\033[2m"

FORMAT = "%(asctime)s %(levelname)-8s %(name)-24s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        colour = LEVEL_COLORS.get(record.levelno, "")
        if not colour:
            return formatted
        return f"{colour}{formatted}{RESET}"


def setup_logs(level: str = "INFO", directory: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))

    if sys.stdout.isatty():
        console.setFormatter(ColorFormatter(FORMAT, DATE_FORMAT))
    else:
        console.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    root.addHandler(console)


    chat = logging.getLogger("chat")
    chat.propagate = False

    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)

        main_file = logging.handlers.RotatingFileHandler(
            directory / "mommi.log", maxBytes=16 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        main_file.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
        main_file.setLevel(logging.DEBUG)
        root.addHandler(main_file)

        chat_file = logging.handlers.TimedRotatingFileHandler(
            directory / "chat.log", when="midnight", backupCount=30, encoding="utf-8", utc=True
        )
        chat_file.setFormatter(logging.Formatter("%(asctime)s %(message)s", DATE_FORMAT))
        chat.addHandler(chat_file)
        chat.setLevel(logging.INFO)
    else:
        chat.addHandler(logging.NullHandler())


    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
