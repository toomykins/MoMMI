from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from mommi.config import Config, ConfigError
from mommi.logsetup import setup_logs

LOGGER = logging.getLogger("mommi")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mommi", description="MoMMI, the /vg/station Discord bot.")
    parser.add_argument(
        "--config-dir", "-c", default=Path("./config"), type=Path, dest="config",
        help="Directory holding main.toml, servers.toml and modules.toml.",
    )
    parser.add_argument(
        "--data-dir", "-s", default=Path("./data"), type=Path, dest="data",
        help="Directory for the database and logs.",
    )
    parser.add_argument(
        "--log-level", "-l", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level.",
    )
    parser.add_argument(
        "--check-config", action="store_true",
        help="Validate the config and exit without connecting to Discord.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    setup_logs(args.log_level, args.data / "logs")

    try:
        config = Config.load(args.config)
    except ConfigError as e:
        LOGGER.critical("Cannot start: %s", e)
        return 1

    if args.check_config:
        LOGGER.info(
            "Config OK: %d server(s) configured: %s",
            len(config.servers),
            ", ".join(s.name for s in config.servers),
        )
        return 0

    if not config.main.bot.token.strip():
        LOGGER.critical("bot.token is empty in main.toml; MoMMI cannot log in.")
        return 1


    from mommi.bot import run_blocking

    LOGGER.info("MoMMI starting!")
    run_blocking(config, args.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
