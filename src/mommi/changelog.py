from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from mommi.config import ChangelogConfig

LOGGER = logging.getLogger(__name__)


HEADER_RE = re.compile(r"(?::cl:|🆑) *\r?\n(.+)$", re.DOTALL)
ENTRY_RE = re.compile(
    r"^ *[*-]? *(bugfix|wip|tweak|soundadd|sounddel|rscdel|rscadd|imageadd|imagedel"
    r"|spellcheck|experiment|tgs): *(\S[^\n\r]+)\r?$",
    re.MULTILINE,
)

GIT_TIMEOUT = 300
COMMIT_MESSAGE = "[ci skip] Automatic changelog update."


def parse_body_changelog(body: str | None) -> list[dict[str, str]]:
    if not body:
        return []
    header = HEADER_RE.search(body)
    if header is None:
        return []
    return [
        {match.group(1): match.group(2).strip()} for match in ENTRY_RE.finditer(header.group(1))
    ]


class ChangelogManager:

    def __init__(self, config: ChangelogConfig, on_entries: Any = None) -> None:
        self.config = config

        self._on_entries = on_entries
        self._task: asyncio.Task[None] | None = None
        self._deadline: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.repo_path is not None

    def repo_matches(self, full_name: str) -> bool:
        return self.config.repo_name is None or self.config.repo_name == full_name

    async def handle_pull_request(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        pull_request = payload.get("pull_request", {})
        if payload.get("action") != "closed" or not pull_request.get("merged"):
            return
        if not self.repo_matches(payload.get("repository", {}).get("full_name", "")):
            return

        entries = parse_body_changelog(pull_request.get("body"))
        if not entries:
            return

        number = payload.get("number") or pull_request.get("number")
        document = {
            "author": pull_request.get("user", {}).get("login", "unknown"),
            "changes": entries,
            "delete-after": True,
        }

        assert self.config.repo_path is not None
        target = self.config.repo_path / self.config.changelog_dir / f"PR-{number}-temp.yml"
        try:
            await asyncio.to_thread(_write_yaml, target, document)
        except OSError:
            LOGGER.exception("Could not write the changelog temp file %s.", target)
            return

        LOGGER.info("Staged %d changelog entries from PR #%s.", len(entries), number)
        self.schedule()

    async def handle_push(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        if not self.repo_matches(payload.get("repository", {}).get("full_name", "")):
            return

        pattern = re.compile(rf"^{re.escape(self.config.changelog_dir)}/[^.].*\.yml$")
        for commit in payload.get("commits", []):
            for filename in list(commit.get("added", [])) + list(commit.get("modified", [])):
                if pattern.match(filename):
                    self.schedule()
                    return

    def schedule(self) -> None:
        loop = asyncio.get_running_loop()
        self._deadline = loop.time() + self.config.delay
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._wait_then_run())

    async def _wait_then_run(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            while True:
                remaining = self._deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)

            async with self._lock:
                entries = await asyncio.to_thread(self._run_git)
            for document in entries:
                if self._on_entries is not None:
                    await self._on_entries(document)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Changelog run failed.")

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


    def _git_env(self) -> dict[str, str] | None:
        if self.config.ssh_key is None:
            return None
        import os

        env = dict(os.environ)
        env["GIT_SSH_COMMAND"] = f"ssh -i {self.config.ssh_key}"
        return env

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        assert self.config.repo_path is not None
        LOGGER.debug("Running %s", args)
        result = subprocess.run(
            args,
            cwd=self.config.repo_path,
            env=self._git_env(),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
        if check and result.returncode != 0:


            raise RuntimeError(
                f"{args[0]} {' '.join(args[1:])} exited {result.returncode}: {result.stderr[:500]}"
            )
        return result

    def _run_git(self) -> list[dict[str, Any]]:
        assert self.config.repo_path is not None
        repo = self.config.repo_path
        if not (repo / ".git").exists():
            LOGGER.error("Changelog repo path %s is not a git checkout.", repo)
            return []

        LOGGER.info("Running changelog generation in %s.", repo)
        self._run("git", "pull", "origin", "--rebase")

        entries = _collect_entries(repo / self.config.changelog_dir)

        script = repo / self.config.script
        if script.is_file():
            self._run(str(script), self.config.changelog_html, self.config.changelog_dir)
        else:
            LOGGER.error("Changelog script %s is missing; skipping regeneration.", script)
            return entries

        self._run("git", "update-index", "--refresh", check=False)
        dirty = self._run("git", "diff-index", "--exit-code", "HEAD", check=False)
        if dirty.returncode == 0:
            LOGGER.info("No changelog changes to commit.")
            return entries

        self._run("git", "add", "-A", ".")
        self._run("git", "commit", "-m", COMMIT_MESSAGE)
        self._run("git", "push", "origin")
        LOGGER.info("Pushed an automatic changelog update.")
        return entries


def _write_yaml(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, default_flow_style=False, allow_unicode=True)


def _collect_entries(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        LOGGER.error("Changelog directory %s does not exist.", directory)
        return []

    out = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.suffix != ".yml" or path.name == "example.yml":
            continue
        try:
            with path.open(encoding="utf-8") as handle:

                document = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError):


            LOGGER.exception("Skipping unreadable changelog file %s.", path)
            continue

        if isinstance(document, dict) and document.get("changes"):
            out.append(document)
    return out
