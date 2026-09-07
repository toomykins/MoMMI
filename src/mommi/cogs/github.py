from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import shlex
from collections import defaultdict
from typing import Any
from urllib.parse import quote

import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, always_command, comm_event, global_comm_event, regex_command
from mommi.github_api import GitHubClient, GitHubError

LOGGER = logging.getLogger(__name__)

REG_ISSUE = re.compile(r"\[(?:(\S+)#|#)?(\d+)\]")
REG_COMMIT = re.compile(r"\[(?:(\S+)@)?([0-9a-f]{40})\]", re.IGNORECASE)
REG_PATH = re.compile(r"\[(?:(\S+)//)?(.+?)(?:(?::|#L)(\d+)(?:-L?(\d+))?)?\]", re.IGNORECASE)
REG_AUTOLABEL = re.compile(r"\[(\w+?)\]", re.IGNORECASE)
MD_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
CODEBLOCK_RE = re.compile(r"```(?:([^\n]*)\n)?(.*?)```", re.DOTALL)

COLOR_RED = discord.Color(0xFF4444)
COLOR_GREEN = discord.Color(0x6CC644)
COLOR_PURPLE = discord.Color(0x6E5494)

MAX_BODY_LENGTH = 500
MAX_COMMIT_LENGTH = 67
MAX_COMMITS_SHOWN = 10

MAX_LOOKUPS = 5

MIN_BARE_ISSUE = 30
MIN_PATH_LENGTH = 4

VALID_ISSUE_ACTIONS = {"opened", "closed", "reopened"}


STATE_EMOJI = {
    "PRopened": (245910125041287168, "🟢"),
    "PRmerged": (437316952772444170, "🟣"),
    "PRclosed": (246037149839917056, "🔴"),
    "ISSopened": (246037149873340416, "🟩"),
    "ISSclosed": (246037286322569216, "🟥"),
    "upvote": (590257887826411590, "👍"),
    "downvote": (590257835447812207, "👎"),
}

CHECK_EMOJI = {
    ("queued", None): "😴",
    ("in_progress", None): "🏃",
    ("completed", "neutral"): "😐",
    ("completed", "success"): "😄",
    ("completed", "failure"): "😭",
    ("completed", "cancelled"): "🛑",
    ("completed", "timed_out"): "⌛",
    ("completed", "action_required"): "🚧",
}


def format_body(body: str | None) -> str:
    if not body:
        return ""
    stripped = MD_COMMENT_RE.sub("", body).strip()
    if len(stripped) > MAX_BODY_LENGTH:
        stripped = stripped[:MAX_BODY_LENGTH] + "..."
    return stripped


def extension_colour(filename: str) -> discord.Color:
    extension = filename.rsplit(".", 1)[-1].lower()
    digest = hashlib.sha256(extension.encode()).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535
    return discord.Color.from_hsv(hue, 0.65, 0.85)


def repo_matches(repo_config: dict[str, Any], ctx: ChannelContext, prefix: str | None) -> bool:
    repo_prefix = repo_config.get("prefix", repo_config.get("repo_prefix"))
    required = repo_config.get("prefix_required", repo_config.get("repo_prefix_required", True))
    whitelist = repo_config.get("prefix_whitelist", [])

    if ctx.alias is not None and ctx.alias in whitelist:
        required = False

    if prefix is not None:
        return bool(repo_prefix) and repo_prefix == prefix
    return not required


class GitHub(MoMMICog):
    def __init__(self, bot: MoMMI) -> None:
        super().__init__(bot)
        token = bot.config.module("github.token", None)
        if not token:
            LOGGER.warning("No `[github] token` in modules.toml; API lookups will be rate limited.")
        self.api = GitHubClient(token)


        self._known_merge_commits: set[str] = set()
        self._muted_repos: set[str] = set()

    async def cog_unload(self) -> None:
        await self.api.close()

    def emoji(self, name: str) -> str:
        default_id, fallback = STATE_EMOJI[name]
        emoji_id = self.bot.config.module(f"github.emoji.{name}", default_id)
        try:
            if emoji_id and self.bot.get_emoji(int(emoji_id)) is not None:
                return f"<:{name}:{int(emoji_id)}>"
        except (TypeError, ValueError):
            LOGGER.warning("github.emoji.%s is not a valid snowflake.", name)
        return fallback

    def repos_for(self, ctx: ChannelContext) -> list[dict[str, Any]]:
        configured = ctx.server_config("modules.github.repos", [])
        return [r for r in configured if isinstance(r, dict) and "repo" in r]


    @comm_event("github")
    async def on_github(self, ctx: ChannelContext, content: Any, meta: str) -> None:
        if not isinstance(content, dict):
            return
        event = content.get("event")
        payload = content.get("content")
        if not isinstance(payload, dict):
            return

        repository = payload.get("repository", {})
        if repository.get("private"):

            return
        if repository.get("full_name") in self._muted_repos:
            return

        handler = {
            "push": self._on_push,
            "issues": self._on_issues,
            "pull_request": self._on_pull_request,
            "issue_comment": self._on_issue_comment,
        }.get(str(event))

        if handler is None:
            LOGGER.debug("No handler for GitHub event %r.", event)
            return
        await handler(ctx, payload)

    async def _on_push(self, ctx: ChannelContext, payload: dict[str, Any]) -> None:
        commits = payload.get("commits") or []
        if not commits:
            return


        await asyncio.sleep(3)

        interesting = [
            c
            for c in commits
            if c.get("message") != "[ci skip] Automatic changelog update."
            and c.get("id") not in self._known_merge_commits
        ]
        if not interesting:
            return

        sender = payload.get("sender", {})
        embed = discord.Embed(url=payload.get("compare"))
        embed.set_author(
            name=sender.get("login", "?"),
            url=sender.get("html_url"),
            icon_url=sender.get("avatar_url"),
        )
        embed.set_footer(text=payload.get("repository", {}).get("full_name", ""))

        count = len(commits)
        embed.title = f"**{count}** New Commit{'' if count == 1 else 's'} to **{payload.get('ref')}**"
        if payload.get("forced"):
            embed.title = f"[FORCE PUSHED] {embed.title}"
            embed.color = discord.Color(0xFF0000)

        lines = []
        for commit in commits[:MAX_COMMITS_SHOWN]:
            text = (commit.get("message") or "").split("\n")[0]
            if len(text) > MAX_COMMIT_LENGTH:
                text = text[:MAX_COMMIT_LENGTH] + "..."
            lines.append(f"[`{commit['id'][:7]}`]({commit['url']}) {discord.utils.escape_markdown(text)}")
        if count > MAX_COMMITS_SHOWN:
            lines.append(f"<...and {count - MAX_COMMITS_SHOWN} more>")

        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    async def _on_issues(self, ctx: ChannelContext, payload: dict[str, Any]) -> None:
        if payload.get("action") not in VALID_ISSUE_ACTIONS:
            return
        await self.post_issue(
            ctx,
            payload["repository"]["full_name"],
            payload["issue"]["number"],
            payload.get("sender"),
        )

    async def _on_pull_request(self, ctx: ChannelContext, payload: dict[str, Any]) -> None:
        if payload.get("action") not in VALID_ISSUE_ACTIONS:
            return

        pull_request = payload["pull_request"]
        repo = payload["repository"]["full_name"]

        if payload["action"] == "closed" and pull_request.get("merged"):
            if sha := pull_request.get("merge_commit_sha"):
                self._known_merge_commits.add(sha)
            asyncio.create_task(self._record_merge_commits(repo, pull_request["number"]))

        await self.post_issue(ctx, repo, pull_request["number"], payload.get("sender"))

    async def _record_merge_commits(self, repo: str, number: int) -> None:
        try:
            commits = await self.api.get(f"/repos/{repo}/pulls/{number}/commits")
        except GitHubError:
            LOGGER.warning("Couldn't fetch commits for %s#%s.", repo, number)
            return
        for commit in commits:
            self._known_merge_commits.add(commit["sha"])

    async def _on_issue_comment(self, ctx: ChannelContext, payload: dict[str, Any]) -> None:
        if payload.get("action") != "created":
            return
        repo = payload["repository"]["full_name"]
        if not ctx.module_config(f"github.repos.{repo}.show_comments", True):
            return

        issue = payload["issue"]
        comment = payload["comment"]
        sender = payload.get("sender", {})

        embed = discord.Embed(
            title=f"New Comment: {issue['title']}"[:256], url=comment["html_url"]
        )
        embed.set_author(
            name=sender.get("login", "?"),
            url=sender.get("html_url"),
            icon_url=sender.get("avatar_url"),
        )
        embed.set_footer(text=f"{repo}#{issue['number']} by {issue['user']['login']}")
        embed.description = format_body(comment.get("body"))
        await ctx.send(embed=embed)


    @global_comm_event
    async def on_any(self, msg_type: str, content: Any, meta: str) -> None:
        if msg_type != "github" or not isinstance(content, dict):
            return
        event = content.get("event")
        payload = content.get("content")
        if not isinstance(payload, dict):
            return

        if event in ("pull_request", "issues"):
            await self._auto_label(event, payload)
        if event == "pull_request":
            await self._secret_repo_check(payload)
        if event == "push":
            await self._shell_exec_on_push(payload)

    async def _auto_label(self, event: str, payload: dict[str, Any]) -> None:
        if payload.get("action") != "opened":
            return
        repo = payload["repository"]["full_name"]
        autolabels: dict[str, str] = self.bot.config.module(f"github.repos.{repo}.autolabels", {})
        if not autolabels:
            return

        if event == "pull_request":
            body = payload["pull_request"].get("body") or ""
            issue_url = payload["pull_request"]["issue_url"]
        else:
            body = payload["issue"].get("body") or ""
            issue_url = payload["issue"]["url"]

        wanted = {
            autolabels[m.group(1).lower()]
            for m in REG_AUTOLABEL.finditer(body)
            if m.group(1).lower() in autolabels
        }
        if not wanted:
            return

        status, _ = await self.api.post(f"{issue_url}/labels", {"labels": sorted(wanted)})
        LOGGER.info("Auto-labelled %s with %s (HTTP %s).", issue_url, sorted(wanted), status)

    async def _secret_repo_check(self, payload: dict[str, Any]) -> None:
        if payload.get("action") not in {"opened", "reopened", "synchronize"}:
            return
        repo = payload["repository"]["full_name"]
        conflict_files: list[str] = self.bot.config.module(
            f"github.repos.{repo}.secret_repo_files", []
        )
        if not conflict_files:
            return

        pull_request = payload["pull_request"]
        try:
            files = await self.api.get(pull_request["url"] + "/files")
            label_name = self.bot.config.module(
                f"github.repos.{repo}.labels.secret_conflicts", "Secret Repo Conflict"
            )
            labels_url = pull_request["issue_url"] + "/labels"
            current = await self.api.get(labels_url)
        except GitHubError:
            LOGGER.exception("Secret repo check failed for %s.", repo)
            return

        conflicts = any(f["filename"] in conflict_files for f in files)
        labelled = any(label["name"] == label_name for label in current)
        if conflicts == labelled:
            return

        number = pull_request["number"]
        if conflicts:
            status, _ = await self.api.post(labels_url, {"labels": [label_name]})
            LOGGER.info("Added secret-conflict label to %s#%s (HTTP %s).", repo, number, status)
        else:


            status = await self.api.delete(f"{labels_url}/{quote(label_name)}")
            LOGGER.info("Removed secret-conflict label from %s#%s (HTTP %s).", repo, number, status)

    async def _shell_exec_on_push(self, payload: dict[str, Any]) -> None:
        repo = payload["repository"]["full_name"]
        command = self.bot.config.module(f"github.shell_exec_on_push.{repo}.command", "")
        if not command:
            return


        try:
            argv = shlex.split(command)
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            if process.returncode != 0:
                LOGGER.error("Push hook for %s exited %s: %s", repo, process.returncode, stderr[:500])
        except (OSError, ValueError, asyncio.TimeoutError):
            LOGGER.exception("Push hook for %s failed to run.", repo)


    @always_command("github_lookup")
    async def lookup(self, ctx: ChannelContext, match: Any, message: discord.Message) -> None:
        repos = self.repos_for(ctx)
        if not repos:
            return

        content = message.content
        asyncio.create_task(self._file_embeds(ctx, content, repos))

        posted = 0
        for repo_config in repos:
            repo = repo_config["repo"]

            for found in REG_ISSUE.finditer(content):
                prefix = found.group(1)
                if not repo_matches(repo_config, ctx, prefix):
                    continue
                number = int(found.group(2))
                if prefix is None and number < MIN_BARE_ISSUE:
                    continue
                await self.post_issue(ctx, repo, number)
                posted += 1
                if posted >= MAX_LOOKUPS:
                    return

            for found in REG_COMMIT.finditer(content):
                if not repo_matches(repo_config, ctx, found.group(1)):
                    continue
                if await self._post_commit(ctx, repo, found.group(2)):
                    posted += 1
                if posted >= MAX_LOOKUPS:
                    return

    async def _post_commit(self, ctx: ChannelContext, repo: str, sha: str) -> bool:
        try:
            commit = await self.api.get(f"/repos/{repo}/git/commits/{sha}")
        except GitHubError:
            return False

        lines = (commit.get("message") or "").split("\n")
        embed = discord.Embed(
            title=lines[0][:256],
            url=commit.get("html_url"),
            description=format_body("\n".join(lines[1:])),
        )
        embed.set_footer(text=f"{repo} {sha[:10]} by {commit.get('author', {}).get('name', '?')}")
        await ctx.send(embed=embed)
        return True

    async def post_issue(
        self, ctx: ChannelContext, repo: str, number: int, sender: dict[str, Any] | None = None
    ) -> None:
        try:
            issue = await self.api.get(f"/repos/{repo}/issues/{number}")
        except GitHubError as e:
            LOGGER.debug("Issue lookup %s#%s failed: %s", repo, number, e)
            return

        is_pr = issue.get("pull_request") is not None
        pr: dict[str, Any] = {}
        if is_pr:
            try:
                pr = await self.api.get(f"/repos/{repo}/pulls/{number}")
            except GitHubError:
                is_pr = False

        embed = discord.Embed(url=issue["html_url"])
        if issue["state"] == "open":
            emoji = self.emoji("PRopened" if is_pr else "ISSopened")
            embed.color = COLOR_GREEN
        elif is_pr and pr.get("merged"):
            emoji = self.emoji("PRmerged")
            embed.color = COLOR_PURPLE
        elif is_pr:
            emoji = self.emoji("PRclosed")
            embed.color = COLOR_RED
        else:
            emoji = self.emoji("ISSclosed")
            embed.color = COLOR_RED

        embed.title = (emoji + issue["title"])[:256]
        embed.set_footer(
            text=f"{repo}#{issue['number']} by {issue['user']['login']}",
            icon_url=issue["user"].get("avatar_url"),
        )
        if sender is not None:
            embed.set_author(
                name=sender.get("login", "?"),
                url=sender.get("html_url"),
                icon_url=sender.get("avatar_url"),
            )

        description = format_body(issue.get("body"))


        reactions = issue.get("reactions") or {}
        votes = []
        if reactions.get("+1"):
            votes.append(f"{self.emoji('upvote')} {reactions['+1']}")
        if reactions.get("-1"):
            votes.append(f"{self.emoji('downvote')} {reactions['-1']}")
        if votes:
            description += "\n" + "   ".join(votes)
        embed.description = description or "​"

        if is_pr:
            await self._add_pr_fields(embed, repo, pr)

        await ctx.send(embed=embed)

    async def _add_pr_fields(self, embed: discord.Embed, repo: str, pr: dict[str, Any]) -> None:
        sha = pr.get("head", {}).get("sha")
        if sha:
            try:
                runs = await self.api.get(f"/repos/{repo}/commits/{sha}/check-runs")
                lines = []
                for check in runs.get("check_runs", []):
                    status = CHECK_EMOJI.get(
                        (check.get("status"), check.get("conclusion")),
                        CHECK_EMOJI.get((check.get("status"), None), "❓"),
                    )
                    lines.append(f"`{check['name']} {status}`")
                if lines:
                    embed.add_field(name="Checks", value="\n".join(lines)[:1024])
            except GitHubError:
                LOGGER.debug("Couldn't fetch check runs for %s@%s.", repo, sha)

        if pr.get("mergeable") is False:
            embed.add_field(name="status", value="🚨CONFLICTS🚨")

    async def _file_embeds(
        self, ctx: ChannelContext, content: str, repos: list[dict[str, Any]]
    ) -> None:
        requests = []
        prefixes: list[str | None] = [None]
        for found in REG_PATH.finditer(content):
            prefix = found.group(1)
            if prefix is not None and prefix not in prefixes:
                prefixes.append(prefix)
            path = found.group(2).lower()
            rooted = path.startswith("^")
            if rooted:
                path = path[1:]

            if len(path) < MIN_PATH_LENGTH:
                continue
            requests.append((path, found.group(3), found.group(4), rooted, prefix))

        if not requests:
            return

        results: dict[str, list[tuple[str, str]]] = defaultdict(list)
        colours: set[int] = set()

        for repo_config in repos:
            if not any(repo_matches(repo_config, ctx, p) for p in prefixes):
                continue
            repo = repo_config["repo"]
            branch_name = repo_config.get("branch", "master")

            try:
                branch = await self.api.get(f"/repos/{repo}/branches/{branch_name}")
                tree = await self.api.get(
                    f"/repos/{repo}/git/trees/{branch['commit']['sha']}",
                    params={"recursive": "1"},
                )
            except GitHubError:
                LOGGER.debug("Couldn't fetch the tree for %s@%s.", repo, branch_name)
                continue

            if tree.get("truncated"):
                LOGGER.warning("Tree for %s is truncated; file lookups may miss.", repo)

            entries = [e for e in tree.get("tree", []) if e.get("type") == "blob"]
            for path, start, end, rooted, prefix in requests:
                if not repo_matches(repo_config, ctx, prefix):
                    continue
                for entry in entries:
                    lowered = entry["path"].lower()
                    if not (lowered.startswith(path) if rooted else lowered.endswith(path)):
                        continue

                    fragment = quote(entry["path"])
                    title = entry["path"]
                    if start is not None:
                        fragment += f"#L{start}"
                        title += f" line {start}"
                        if end is not None:
                            fragment += f"-L{end}"
                            title = f"{entry['path']} lines {start}-{end}"

                    results[repo].append(
                        (title, f"https://github.com/{repo}/blob/{branch_name}/{fragment}")
                    )
                    colours.add(extension_colour(entry["path"]).value)

        if not results:
            return

        embed = discord.Embed()

        if len(colours) == 1:
            embed.color = discord.Color(next(iter(colours)))

        for repo, hits in results.items():
            value = ""
            for index, (title, url) in enumerate(hits):
                entry = f"[`{title}`]({url})\n"
                if len(value) + len(entry) > 900:
                    value += f"...and {len(hits) - index} more."
                    break
                value += entry
            embed.add_field(name=repo, value=value or "(nothing fit)", inline=False)

        await ctx.send(embed=embed)


    @regex_command("giveissue", r"giveissue(?:\s+(.*))?")
    async def giveissue(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        repos = self.repos_for(ctx)
        if not repos:
            await message.add_reaction("❌")
            return

        prefix: str | None = None
        short_labels: set[str] = set()
        for param in re.finditer(r"-(\w+)=(\S+)", match.group(1) or ""):
            key, value = param.group(1), param.group(2)
            if key == "prefix":
                prefix = value
            elif key == "labels":
                short_labels.update(v.strip() for v in value.split(",") if v.strip())
            else:
                await ctx.send(f"⚠ Unknown parameter: {key}")

        await message.add_reaction("⏳")
        found_any = False
        try:
            for repo_config in repos:
                if not repo_matches(repo_config, ctx, prefix):
                    continue
                repo = repo_config["repo"]

                params: dict[str, str] = {"state": "open"}
                if short_labels:
                    autolabels: dict[str, str] = self.bot.config.module(
                        f"github.repos.{repo}.autolabels", {}
                    )
                    resolved = set()
                    for short in short_labels:
                        if short.lower() in autolabels:
                            resolved.add(autolabels[short.lower()])
                        else:
                            await ctx.send(f"⚠ Unknown autolabel: '{short.lower()}'. repo: '{repo}'")
                    if resolved:
                        params["labels"] = ",".join(sorted(resolved))

                try:
                    path = f"/repos/{repo}/issues"
                    pages = await self.api.get_paginated_count(path, params=params)
                    page_params = dict(params, page=str(random.randint(1, pages)))
                    issues = await self.api.get(path, params=page_params)
                except GitHubError:
                    LOGGER.exception("giveissue failed for %s.", repo)
                    continue


                candidates = [i for i in issues if i.get("pull_request") is None]
                if not candidates:
                    continue

                found_any = True
                await self.post_issue(ctx, repo, random.choice(candidates)["number"])
        finally:
            try:
                await message.remove_reaction("⏳", self.bot.user)  # type: ignore[arg-type]
            except discord.HTTPException:
                pass

        if not found_any:
            await message.add_reaction("👎")
            await ctx.send("😕 No random issue found")
        else:
            await message.add_reaction("👍")

    @regex_command("autolabels", r"(?:(\S+)#)?autolabels?\b")
    async def autolabels(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        repos = self.repos_for(ctx)
        prefix = match.group(1)
        sent = False

        for repo_config in repos:
            if not repo_matches(repo_config, ctx, prefix):
                continue
            repo = repo_config["repo"]
            autolabels: dict[str, str] = self.bot.config.module(
                f"github.repos.{repo}.autolabels", {}
            )
            if not autolabels:
                continue
            body = "\n".join(f"`[{k}]` <> {v}" for k, v in sorted(autolabels.items()))
            await ctx.send(embed=discord.Embed(title=f"Autolabels for {repo}", description=body[:4000]))
            sent = True

        if not sent:
            await message.add_reaction("❌")


    async def codeblocks_to_gists(self, text: str) -> str:
        out = ""
        last = 0
        for found in CODEBLOCK_RE.finditer(text):
            out += text[last : found.start()]
            language = (found.group(1) or "").strip()
            body = found.group(2)


            if " " in language or not body.strip():
                body = (found.group(1) or "") + body
                extension = "txt"
            else:
                extension = language or "txt"

            try:
                out += await self.api.create_gist(
                    body.strip(), f"file.{extension}", "Code snippet from Discord -> IRC relay."
                )
            except Exception:
                LOGGER.exception("Failed to turn a code block into a gist.")
                out += "[code block]"
            last = found.end()

        return out + text[last:]

    def help_articles(self) -> dict[str, Any]:
        async def github_help(ctx: ChannelContext) -> str:
            repos = self.repos_for(ctx)
            if not repos:
                return "This server has no repo configs. Sorry lad."

            has_bare = any(
                not r.get("prefix_required", r.get("repo_prefix_required", True)) for r in repos
            )
            if has_bare:
                out = (
                    "MoMMI can look up issues, commits and files in GitHub repos for you:\n"
                    "`[number]`: issue/PR lookup.\n"
                    "`[commit hash]`: commit lookup.\n"
                    "`[end of filepath]` or `[^start of filepath]`: file lookup.\n\n"
                    "You can also name a repo by its prefix:\n"
                    "`[prefix#number]`, `[prefix@commit hash]`, `[prefix//filepath]`\n\n"
                    "On this Discord server:\n"
                )
            else:
                out = (
                    "MoMMI can look up issues, commits and files in GitHub repos for you:\n"
                    "`[prefix#number]`: issue/PR lookup.\n"
                    "`[prefix@commit hash]`: commit lookup.\n"
                    "`[prefix//end of filepath]` or `[prefix//^start of filepath]`: file lookup.\n\n"
                    "On this Discord server:\n"
                )

            for repo_config in repos:
                prefix = repo_config.get("prefix", repo_config.get("repo_prefix", ""))
                required = repo_config.get(
                    "prefix_required", repo_config.get("repo_prefix_required", True)
                )
                out += f"* `{repo_config['repo']}`: `{prefix}`"
                out += ", prefix required\n" if required else "\n"
            return out

        return {"github": github_help}


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(GitHub(bot))
