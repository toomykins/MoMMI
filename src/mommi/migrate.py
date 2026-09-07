from __future__ import annotations

import argparse
import asyncio
import logging
import pickle
import sys
from pathlib import Path
from typing import Any

from mommi.config import Config, ConfigError
from mommi.storage import Storage

LOGGER = logging.getLogger("mommi.migrate")


GLOBAL_DIR = "__global__"


def load_pickle(path: Path) -> Any:
    with path.open("rb") as handle:
        return pickle.load(handle)


def normalise_markov(chain: Any) -> dict[str, dict[str, int]]:
    return {str(word): {str(k): int(v) for k, v in nexts.items()} for word, nexts in chain.items()}


async def migrate_storage(data_dir: Path, config: Config, storage: Storage, dry_run: bool) -> None:
    by_name = {server.name: server.id for server in config.servers}

    for guild_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        if guild_dir.name == GLOBAL_DIR:
            continue
        guild_id = by_name.get(guild_dir.name)
        if guild_id is None:
            LOGGER.warning(
                "Skipping %s: no server in servers.toml is named %r.", guild_dir, guild_dir.name
            )
            continue

        for jar in sorted(p for p in guild_dir.iterdir() if p.is_file()):
            try:
                value = load_pickle(jar)
            except Exception:
                LOGGER.exception("Could not read %s; skipping.", jar)
                continue

            if jar.name == "markov":
                value = normalise_markov(value)
                LOGGER.info("%s/markov: %d words", guild_dir.name, len(value))
            elif jar.name == "resp":
                value = {str(k): str(v) for k, v in dict(value).items()}
                LOGGER.info("%s/resp: %d responses", guild_dir.name, len(value))
            else:
                LOGGER.info("%s/%s: copying as-is", guild_dir.name, jar.name)

            if not dry_run:
                await storage.set(guild_id, jar.name, value)

    global_dir = data_dir / GLOBAL_DIR
    if global_dir.is_dir():
        await migrate_global(global_dir, by_name, storage, dry_run)


async def migrate_global(
    global_dir: Path, by_name: dict[str, int], storage: Storage, dry_run: bool
) -> None:
    reminders = global_dir / "reminder_queue"
    if reminders.is_file():
        try:
            queue = load_pickle(reminders)
        except Exception:
            LOGGER.exception("Could not read the reminder queue; skipping.")
            queue = []

        count = 0
        for entry in queue:


            try:
                when, message, guild_id, channel_id, user_id = entry[:5]
                if not dry_run:
                    await storage.add_reminder(
                        when.timestamp(),
                        str(message),
                        int(guild_id),
                        int(channel_id),
                        int(user_id),
                        when.timestamp(),
                    )
                count += 1
            except Exception:
                LOGGER.warning("Skipping malformed reminder %r.", entry)
        LOGGER.info("Reminders migrated: %d", count)

    mirrors = global_dir / "mirror_queue"
    if mirrors.is_file():
        try:
            queue = load_pickle(mirrors)
        except Exception:
            LOGGER.exception("Could not read the mirror queue; skipping.")
            queue = []
        count = 0
        for entry in queue:

            try:
                when, message, sender, avatar, target, source = entry[:6]
                if not dry_run:
                    await storage.add_mirror(
                        when.timestamp(), str(message), str(sender), str(avatar),
                        str(target), int(source),
                    )
                count += 1
            except Exception:
                LOGGER.warning("Skipping malformed mirror entry %r.", entry)
        LOGGER.info("Pending mirrors migrated: %d", count)

    nudges = global_dir / "nudge_mirror_queue"
    if nudges.is_file():
        try:
            queue = load_pickle(nudges)
        except Exception:
            LOGGER.exception("Could not read the nudge mirror queue; skipping.")
            queue = []
        count = 0
        for entry in queue:

            try:
                when, content, guild_id, channel_id = entry[:4]
                if not dry_run:
                    await storage.add_nudge(
                        when.timestamp(), str(content), int(guild_id), int(channel_id)
                    )
                count += 1
            except Exception:
                LOGGER.warning("Skipping malformed nudge entry %r.", entry)
        LOGGER.info("Pending nudge mirrors migrated: %d", count)


def convert_rocket_toml(path: Path) -> str:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib

    with path.open("rb") as handle:
        rocket = tomllib.load(handle)


    merged: dict[str, Any] = {}
    for section in ("development", "production", "global"):
        if isinstance(rocket.get(section), dict):
            merged.update(rocket[section])

    lines = [
        "# Generated from Rocket.toml by `python -m mommi.migrate --rocket-toml`.",
        "# Paste this into main.toml, replacing any existing [web] section.",
        "[web]",
        "enabled = true",
        f'address = "{merged.get("address", "0.0.0.0")}"',
        f"port = {merged.get('port', 40000)}",
        f'github-key = "{merged.get("github-key", "")}"',
        f"verify-github = {str(merged.get('verify-github', True)).lower()}",
    ]

    changelog = [
        ("repo-path", merged.get("repo-path")),
        ("repo-name", merged.get("changelog-repo-name")),
        ("ssh-key", merged.get("ssh-key")),
    ]

    if merged.get("changelog-repo-path"):
        changelog[0] = ("repo-path", merged["changelog-repo-path"])

    lines.append("")
    lines.append("[web.changelog]")
    lines.append(f"delay = {merged.get('changelog-delay', 30)}")
    for key, value in changelog:
        if value:
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"# {key} = ")

    if merged.get("commloop-address"):
        lines.append("")
        lines.append(
            f"# Note: Rocket.toml pointed the commloop at {merged['commloop-address']}. "
            "The bot now handles webhooks in-process, so that setting is no longer needed."
        )
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    parser = argparse.ArgumentParser(
        prog="mommi.migrate", description="Migrate a MoMMI v2 deployment to v3."
    )
    parser.add_argument("--config-dir", "-c", type=Path, default=Path("./config"))
    parser.add_argument(
        "--old-data", type=Path, help="v2's data directory (the one full of pickle files)."
    )
    parser.add_argument("--new-data", type=Path, default=Path("./data"), help="v3 data directory.")
    parser.add_argument(
        "--rocket-toml", type=Path, help="Convert a WebMoMMI Rocket.toml and print the [web] block."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would happen without writing."
    )
    args = parser.parse_args()

    if args.rocket_toml:
        if not args.rocket_toml.is_file():
            LOGGER.error("No such file: %s", args.rocket_toml)
            return 1
        print(convert_rocket_toml(args.rocket_toml))
        return 0

    if not args.old_data:
        LOGGER.error("Nothing to do. Pass --old-data and/or --rocket-toml. See --help.")
        return 1
    if not args.old_data.is_dir():
        LOGGER.error("No such directory: %s", args.old_data)
        return 1

    try:
        config = Config.load(args.config_dir)
    except ConfigError as e:
        LOGGER.error("%s", e)
        return 1

    async def run() -> None:
        storage = Storage(args.new_data / "mommi.sqlite3")
        await storage.open()
        try:
            await migrate_storage(args.old_data, config, storage, args.dry_run)
        finally:
            await storage.close()

    asyncio.run(run())
    if args.dry_run:
        LOGGER.info("Dry run: nothing was written.")
    else:
        LOGGER.info("Migration complete. Keep the old data directory until you're happy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
