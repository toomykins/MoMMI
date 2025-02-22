import json
import logging
import re
import asyncio
import subprocess
from typing import Match, Tuple, List, Optional, Any, Set, Dict, DefaultDict, cast, Union
from urllib.parse import quote, quote_plus
from collections import defaultdict
import aiohttp
import bottom
from colorhash import ColorHash
from discord import Color, Embed, Message, User
from MoMMI.channel import MChannel
from MoMMI.commloop import comm_event, global_comm_event
from MoMMI.commands import always_command
from MoMMI.master import master
from MoMMI.server import MServer
from MoMMI.Modules.irc import irc_transform
from MoMMI import command
import random

logger = logging.getLogger(__name__)

REG_PATH = re.compile(r"\[(?:(\S+)\/\/)?(.+?)(?:(?::|#L)(\d+)(?:-L?(\d+))?)?\]", re.I)
REG_ISSUE = re.compile(r"\[(?:(\S+)#|#)?([0-9]+)\]")
REG_COMMIT = re.compile(r"\[(?:(\S+)@)?([0-9a-f]{40})\]", re.I)
REG_GIT_HEADER_PAGENUM = re.compile(r"[?&]page=(\d+)[^,]+rel=\"last\"")

REG_AUTOLABEL = re.compile(r"\[(\w+?)\]", re.I)

COLOR_GITHUB_RED = Color(0xFF4444)
COLOR_GITHUB_GREEN = Color(0x6CC644)
COLOR_GITHUB_PURPLE = Color(0x6E5494)
MAX_BODY_LENGTH = 500
MAX_COMMIT_LENGTH = 67
MD_COMMENT_RE = re.compile(r"<!--.*-->", flags=re.DOTALL)
DISCORD_CODEBLOCK_RE = re.compile(
    r"```(?:([^\n]*)\n)?(.*)```", flags=re.DOTALL)

GITHUB_SESSION = "github_session"
GITHUB_CACHE = "github_cache"

GITHUB_ISSUE_MAX_MESSAGES = 5

VALID_ISSUES_ACTIONS = {"opened", "closed", "reopened"}

KNOWN_MERGE_COMMITS: Set[str] = set()

EVENT_MUTED_REPOS: Set[str] = set()

def is_repo_muted(repo: str) -> bool:
    return repo in EVENT_MUTED_REPOS


async def load(loop: asyncio.AbstractEventLoop) -> None:
    if not master.has_cache(GITHUB_SESSION):
        headers = {
            "Authorization": f"token {master.config.get_module('github.token')}",
            "User-Agent": "MoMMIv2 (@PJBot, @PJB3005)",
            "Accept": "application/vnd.github.v3+json",
        }
        session = aiohttp.ClientSession(headers=headers)
        master.set_cache(GITHUB_SESSION, session)

    if not master.has_cache(GITHUB_CACHE):
        master.set_cache(GITHUB_CACHE, {})

    gh_register_help()


async def shutdown(loop: asyncio.AbstractEventLoop) -> None:
    await master.get_cache(GITHUB_SESSION).close()
    master.del_cache(GITHUB_SESSION)


def github_url(sub: str) -> str:
    return f"https://api.github.com{sub}"


def colour_extension(filename: str) -> Color:
    ext = filename.split(".")[-1]
    c = ColorHash(ext)
    return Color(int(c.hex[1:], 16))


@comm_event("github")
async def github_event(channel: MChannel, message: Any, meta: str) -> None:
    event = message['event']
    logger.debug(
        f"Handling GitHub event '$YELLOW{event}$RESET' to '$YELLOW{meta}$RESET'")

    # Don't do anything on private repos for now.
    if message["content"]["repository"]["private"]:
        return

    # Find a function by the name of `on_github_{event}` in globals and call it.
    func = globals().get(f"on_github_{event}")
    if func is None:
        logger.debug("No handler for this event, ignoring.")
        return

    async def wrap():
        try:
            await func(channel, message["content"], meta)
        except:
            logger.exception("Caught exception inside GitHub event handler.")

    asyncio.ensure_future(wrap())


async def on_github_push(channel: MChannel, message: Any, meta: str) -> None:
    commits = message["commits"]
    if not commits:
        return

    # We need to sleep for a few because
    # the PR event needs to update commit hashes to ignore
    # and it does a network request.
    await asyncio.sleep(3)

    embed = Embed()
    embed.url = message["compare"]
    embed.set_author(name=message["sender"]["login"], url=message["sender"]
                     ["html_url"], icon_url=message["sender"]["avatar_url"])
    embed.set_footer(text=message["repository"]["full_name"])
    commits_count = ""
    if len(commits) == 1:
        commits_count = "**1** New Commit"
    else:
        commits_count = f"**{len(commits)}** New Commits"

    embed.title = f"{commits_count} to **{message['ref']}**"

    if message["forced"]:
        embed.title = f"[FORCE PUSHED] {embed.title}"
        embed.color = Color(0xFF0000)

    content = ""

    found = False
    count = 0
    for commit in commits:
        message = commit["message"]
        # Don't do these to reduce spam.
        if message != "[ci skip] Automatic changelog update." and commit["id"] not in KNOWN_MERGE_COMMITS:
            found = True

        if len(message) > 67:
            message = message[:67] + "..."
        content += f"[`{commit['id'][:7]}`]({commit['url']}) {message}\n"
        count += 1
        if count > 10:
            content += "<.....>"
            break

    if not found:
        return

    embed.description = content

    await channel.send(embed=embed)


async def on_github_issues(channel: MChannel, message: Any, meta: str) -> None:
    if message["action"] not in VALID_ISSUES_ACTIONS:
        return

    await post_embedded_issue_or_pr(channel, message["repository"]["full_name"], message["issue"]["number"], message["sender"])


async def on_github_pull_request(channel: MChannel, message: Any, meta: str) -> None:
   # logger.debug("fuck you python")
    action = message["action"]
    if action not in VALID_ISSUES_ACTIONS:
        return

    #logger.debug("fuck")

    pull_request = message["pull_request"]
    repository = message["repository"]

    if action == "closed" and pull_request["merged"]:
        KNOWN_MERGE_COMMITS.add(pull_request["merge_commit_sha"])
        asyncio.ensure_future(add_known_merge_commits(
            repository["full_name"], pull_request["number"]))

    #logger.debug("yes???")

    if is_repo_muted(repository["full_name"]):
        return

    await post_embedded_issue_or_pr(channel, repository["full_name"], pull_request["number"], message["sender"])


async def add_known_merge_commits(repo: str, number: int) -> None:
    url = github_url(f"/repos/{repo}/pulls/{number}/commits")
    commits = await get_github_object(url)
    for commit in commits:
        sha = commit["sha"]
        KNOWN_MERGE_COMMITS.add(sha)


async def on_github_issue_comment(channel: MChannel, message: Any, meta: str) -> None:
    if message["action"] != "created":
        return

    issue = message["issue"]
    comment = message["comment"]
    repo_name = message["repository"]["full_name"]

    if not channel.module_config(f"github.repos.{repo_name}.show_comments", True):
        return

    embed = Embed()
    embed.set_author(name=message["sender"]["login"], url=message["sender"]
                     ["html_url"], icon_url=message["sender"]["avatar_url"])
    embed.set_footer(
        text=f"{repo_name}#{issue['number']} by {issue['user']['login']}")
    embed.title = f"New Comment: {issue['title']}"
    embed.url = message["comment"]["html_url"]

    embed.description = format_desc(comment["body"])

    await channel.send(embed=embed)


@global_comm_event("secret_repo_pr_checker")
async def secret_repo_check(type: str, message: Any, meta: str) -> None:
    if type != "github":
        return
    if "event" not in message or message["event"] != "pull_request":
        return

    message = message["content"]
    action = message["action"]
    if action != "synchronize" and action != "opened" and action != "reopened":
        return

    repo_name = message["repository"]["full_name"]
    conflict_files: List[str] = master.config.get_module(
        f"github.repos.{repo_name}.secret_repo_files", [])
    if not conflict_files:
        return

    files = await get_github_object(message["pull_request"]["url"] + "/files")
    found = False
    for fileobject in files:
        if fileobject["filename"] in conflict_files:
            found = True
            break

    label_name = master.config.get_module(f"github.repos.{repo_name}.labels.secret_conflicts", "Secret Repo Conflict")
    url = message["pull_request"]["issue_url"] + "/labels"
    labels = await get_github_object(url)
    haslabel = False
    for label in labels:
        if label["name"] == label_name:
            haslabel = True
            break

    if haslabel == found:
        return

    session: aiohttp.ClientSession = master.get_cache(GITHUB_SESSION)

    url = message["pull_request"]["issue_url"] + "/labels"

    session = master.get_cache(GITHUB_SESSION)
    if found:
        postdata = json.dumps(
            [label_name])
        async with session.post(url, data=postdata) as postresp:
            logger.info("Setting secret repo conflicts label on PR #%s returned status code %s!",
                        message["number"], postresp.status)
    else:
        async with session.delete(url + "/" + quote_plus(label_name)):
            logger.info("Deleting secret repo conflicts label on PR #%s returned status code %s!",
                        message["number"], postresp.status)

@global_comm_event("issue_auto_label")
async def issue_auto_label(type: str, message: Any, meta: str) -> None:
    if type != "github":
        return
    if "event" not in message or (message["event"] != "pull_request" and message["event"] != "issues"):
        return

    event = message["event"]
    message = message["content"]
    action = message["action"]
    if action != "opened":
        return

    repo_name = message["repository"]["full_name"]
    autolabels: Dict[str, str] = master.config.get_module(
        f"github.repos.{repo_name}.autolabels", {})
    if not autolabels:
        return

    if event == "pull_request":
        body_content = message["pull_request"]["body"]
        issue_url = message["pull_request"]["issue_url"]
    else:
        body_content = message["issue"]["body"]
        issue_url = message["issue"]["url"]

    label_url = issue_url + "/labels"

    to_add = set()

    for match in REG_AUTOLABEL.finditer(body_content):
        label = match.group(1)

        matched_label = autolabels.get(label.lower())
        if matched_label:
            to_add.add(matched_label)

    if not to_add:
        return

    to_add_list = [*to_add]

    session: aiohttp.ClientSession = master.get_cache(GITHUB_SESSION)
    headers = {
        "Accept": "application/vnd.github.symmetra-preview+json"
    }


    async with session.post(label_url, json=to_add_list, headers=headers) as req:
        logger.info(f"{req.status}")


# Indent 2: the indent
# handling of stuff like [2000] and [world.dm]


@always_command("github_issue")
async def issue_command(channel: MChannel, match: Match, message: Message) -> None:
    try:
        cfg: List[Dict[str, Any]] = channel.server_config("modules.github.repos")
    except:
        # Server has no config settings for GitHub.
        return

    asyncio.ensure_future(try_handle_file_embeds(message.content, channel, cfg))

    messages = 0

    for repo_config in cfg:
        #logger.debug(repo_config)
        repo = repo_config["repo"]

        for match in REG_ISSUE.finditer(message.content):
            prefix = match.group(1)
            if not is_repo_valid_for_command(repo_config, channel, prefix):
                continue

            issueid = int(match.group(2))
            if not prefix and issueid < 30:
                continue

            #logger.debug("You what")
            await post_embedded_issue_or_pr(channel, repo, issueid)

            messages += 1
            if messages >= GITHUB_ISSUE_MAX_MESSAGES:
                return

        for match in REG_COMMIT.finditer(message.content):
            prefix = match.group(1)

            if not is_repo_valid_for_command(repo_config, channel, prefix):
                continue

            sha = match.group(2)
            url = github_url(f"/repos/{repo}/git/commits/{sha}")
            try:
                commit = await get_github_object(url)
            except:
                continue

            split = commit["message"].split("\n")
            title = split[0]
            desc = "\n".join(split[1:])
            if len(desc) > MAX_BODY_LENGTH:
                desc = desc[:MAX_BODY_LENGTH] + "..."

            embed = Embed()
            embed.set_footer(
                text=f"{repo} {sha} by {commit['author']['name']}")
            embed.url = commit["html_url"]
            embed.title = title
            embed.description = format_desc(desc)

            await channel.send(embed=embed)

            messages += 1
            if messages >= GITHUB_ISSUE_MAX_MESSAGES:
                return


async def try_handle_file_embeds(message: str, channel: MChannel, cfg: List[Dict[str, str]]) -> bool:
    if not REG_PATH.search(message):
        return False

    prefixes: List[Optional[str]] = [None]
    paths: List[Tuple[str, Optional[str], Optional[str], bool, Optional[str]]]
    paths = []
    color: Union[str, Color] = None
    for match in REG_PATH.finditer(message):
        prefix = match.group(1)
        if prefix is not None and prefix not in prefixes:
            prefixes.append(prefix)
        path = match.group(2).lower()
        # Ignore tiny paths, too common accidentally in code blocks.
        if len(path) <= 3:
            continue

        rooted = False
        if path.startswith("^"):
            path = path[1:]
            rooted = True

        linestart = None
        lineend = None
        if match.group(3):
            linestart = match.group(3)
            if match.group(4):
                lineend = match.group(4)

        paths.append((path, linestart, lineend, rooted, prefix))

    # That's reponame: list((title, url))
    output: DefaultDict[str, List[Tuple[str, str]]] = defaultdict(list)

    for repocfg in cfg:
        for theprefix in prefixes:
            if is_repo_valid_for_command(repocfg, channel, theprefix):
                break

        else:
            continue

        repo = repocfg["repo"]
        branchname = repocfg.get("branch", "master")

        url = github_url(f"/repos/{repo}/branches/{branchname}")
        branch = await get_github_object(url)

        url = github_url(
            f"/repos/{repo}/git/trees/{branch['commit']['sha']}")
        tree = await get_github_object(url, params={"recursive": "1"})

        for path, linestart, lineend, rooted, nottheprefix in paths:
            if not is_repo_valid_for_command(repocfg, channel, nottheprefix):
                continue

            for filehash in tree["tree"]:
                if rooted:
                    if not filehash["path"].lower().startswith(path):
                        continue

                else:
                    if not filehash["path"].lower().endswith(path):
                        continue

                thepath = filehash["path"]  # type: str
                file_url_part = quote(thepath)
                if linestart is not None:
                    file_url_part += f"#L{linestart}"
                    if lineend is not None:
                        file_url_part += f"-L{lineend}"

                url = f"https://github.com/{repo}/blob/{branchname}/{file_url_part}"
                title = thepath
                if lineend is not None:
                    title += f" lines {linestart}-{lineend}"

                elif linestart is not None:
                    title += f" line {linestart}"

                output[repo].append((title, url))
                thiscolor = colour_extension(thepath)
                if color is None:
                    color = thiscolor
                elif color != thiscolor:
                    color = "Nope"

    if not output:
        return False

    embed = Embed()
    if color != "Nope" and color is not None:
        embed.color = color

    for repo, hits in output.items():
        value = ""
        count = 0
        for title, url in hits:
            count += 1
            entry = f"[`{title}`]({url})\n"
            if len(value) > 800:
                if not count:
                    value = f"Good job even a single entry is too long to fit within Discord's embed field limits. There were {len(hits)}."
                    break

                value += f"...and {len(hits)-count} more."
                break

            value += entry

        embed.add_field(name=repo, value=value)

    await channel.send(embed=embed)

    return True


@irc_transform("convert_code_blocks")
async def convert_code_blocks(message: str, author: User, irc_client: bottom.Client, discord_channel: MChannel) -> str:
    try:
        last = 0
        out = ""
        # print("yes!")
        for match in DISCORD_CODEBLOCK_RE.finditer(message):
            # print("my man!")
            out += message[last:match.start()]

            gist_contents = ""
            extension = "txt"
            # Has a space on first line of code block (the ``` line).
            # Read first line as part of the contents.
            language = match.group(1)
            if language is None or language == "":
                pass

            elif " " in language.strip() or match.group(2).strip() == "":
                gist_contents = match.group(1)

            else:
                extension = language.strip()

            gist_contents += match.group(2).strip()

            url = await make_gist(gist_contents, f"file.{extension}", f"Code snippet from Discord -> IRC relay.")

            out += url
            last = match.end()

        out += message[last:len(message)]

        return out

    except:
        logger.exception("Failed to turn code block into gist.")
        return "<MoMMI error tell PJB>"


async def make_gist(contents: str, name: str, desc: str) -> str:
    session = master.get_cache(GITHUB_SESSION)

    # POST /gists
    url = github_url("/gists")
    post_data = {
        "description": desc,
        "public": False,
        "files": {
            name: {
                "content": contents
            }
        }
    }
    async with session.post(url, data=json.dumps(post_data)) as resp:
        if resp.status != 201:
            return f"[GIST ERROR: {resp.status} ({resp.reason})]"

        output = await resp.json()
        return cast(str, output["html_url"])


async def get_github_object(url: str, *, params: Optional[Dict[str, str]] = None, accept: Optional[str] = None) -> Any:
    logger.debug(f"Fetching github object at URL {url}...")

    session = master.get_cache(GITHUB_SESSION)
    cache = master.get_cache(GITHUB_CACHE)

    response = None
    paramstr = str(params)

    if (url, paramstr, accept) in cache:
        contents, date = cache[(url, paramstr, accept)]
        headers = {"If-Modified-Since": date}
        if accept:
            headers["Accept"] = accept
        response = await session.get(url, headers=headers, params=params)
        if response.status == 304:
            return contents

    else:
        headers = {}
        if accept:
            headers["Accept"] = accept
        response = await session.get(url, params=params, headers=headers)

    if response.status != 200:
        txt = await response.text()
        raise Exception(f"GitHub API call returned non-200 code: {txt}")

    contents = await response.json()
    if "Last-Modified" in response.headers:
        cache[(url, paramstr)] = contents, response.headers["Last-Modified"], accept
    #print(contents)
    return contents


def is_repo_valid_for_command(repo_config: Dict[str, Any], channel: MChannel, prefix: Optional[str]) -> bool:
    """
    Checks to see whether commands like [123] are valid for a certain repo on a certain channel.
    """
    repo_prefix = repo_config.get("prefix")
    repo_prefix_required = repo_config.get("prefix_required", True)
    repo_prefix_whitelist = repo_config.get("prefix_whitelist", [])

    if channel.internal_name is not None and channel.internal_name in repo_prefix_whitelist:
        repo_prefix_required = False

    if prefix is not None and repo_prefix != prefix:
        return False

    if prefix is None and repo_prefix_required:
        return False

    return True


async def get_gh_help(channel: MChannel, message: Message) -> str:
    try:
        cfg: List[Dict[str, Any]] = channel.server_config("modules.github.repos")
    except:
        return "This server has no repo configs. Sorry lad."

    has_nonprefixed = False

    for repo_config in cfg:
        if not repo_config.get("prefix_required", True):
            has_nonprefixed = True
            break

    if has_nonprefixed:
        desc = """MoMMI can look up issues, commits and files in GitHub repos for you. Syntax is as follows:
    `[number]`: issue/PR lookup.
    `[commit hash]`: commit lookup.
    `[end of filepath]` or `[^begin of file path]`: look up files.

For refinement (and to look up in certain repos) you can specify "prefixes":
    `[prefix#number]`: issue/PR lookup with prefix.
    `[prefix@commit hash]`: commit lookup with prefix.
    `[prefix//end of filepath]` or `[prefix//^begin of file path]`: look up files with prefix.

FOR THIS DISCORD SERVER, the following repos are available, including their prefix and if it's required or not.
"""

        for repo_config in cfg:
            desc += f"* `{repo_config['repo']}`: `{repo_config['prefix']}`"
            if repo_config.get("prefix_required", True):
                desc += ", prefix required\n"
            else:
                desc += "\n"

    else:
        desc = """MoMMI can look up issues, commits and files in GitHub repos for you. Syntax is as follows:
    `[prefix#number]`: issue/PR lookup.
    `[prefix@commit hash]`: commit lookup.
    `[prefix//end of filepath]` or `[prefix//^begin of file path]`: look up files.

These prefixes are per-repo identifiers. FOR THIS DISCORD SERVER, the following repos are available:
"""

        for repo_config in cfg:
            desc += f"* `{repo_config['repo']}`: `{repo_config['prefix']}`\n"

    return desc


def gh_register_help() -> None:
    from MoMMI.Modules.help import register_help

    register_help(__name__, "github", get_gh_help)



@global_comm_event("jenkins_handicapping")
async def jenkins_handicap_support(type: str, message: Any, meta: str) -> None:
    if type != "github":
        return
    if "event" not in message or message["event"] != "push":
        return

    message = message["content"]
    repo_name = message["repository"]["full_name"]
    command = master.config.get_module(
        f"github.shell_exec_on_push.{repo_name}.command", "")

    if command:
        subprocess.Popen([command])

@command("giveissue", r"giveissue(?:\s+(-\w+=\w+(?:\s+-\w+=\w+)*))?")
async def giveissue_command(channel: MChannel, match: Match, message: Message) -> None:
    try:
        cfg: List[Dict[str, Any]] = channel.server_config("modules.github.repos")
    except:
        # Server has no config settings for GitHub.
        logger.error(f"giveissue didn't find server config")
        await master.client.add_reaction(message, "❌")
        return

    await master.client.add_reaction(message, "⏳")

    prefix = None
    shortlabels = set()

    #logger.debug("thinking")

    if match.group(1):
        # getting params
        text_params = [x.strip() for x in match.group(1)[1:].split(" -")] # strip whitespaces

        #logger.debug(f"{repr(text_params)}: {len(text_params)}")

        for param in text_params:
            temp = param.split("=")
            if temp[0] == "prefix":
                prefix = temp[1].strip()
                continue
            if temp[0] == "labels":
                param_labels = temp[1].strip().split(",")
                for p_label in param_labels:
                    shortlabels.add(p_label)
                continue

            await channel.send(f"⚠ Unknown parameter: {temp[0]}")


    #logger.debug("uh oh")
    didthedeed = 0
    for repo_config in cfg:
        repo = repo_config["repo"]

        if not is_repo_valid_for_command(repo_config, channel, prefix):
            continue
        didthedeed = 1

        url = github_url(f"/repos/{repo}/issues")

        labels = ""
        if shortlabels:
            autolabels: Dict[str, str] = master.config.get_module(
                f"github.repos.{repo}.autolabels", {})
            if not autolabels:
                await channel.send("⚠ Could not find autolabel config, skipping labels param")
                labels = ""
            else:
                to_add = set()

                for s_label in shortlabels:
                    matched_label = autolabels.get(s_label.lower())
                    if matched_label:
                        to_add.add(matched_label)
                    else:
                        await channel.send(f"⚠ Unknown autolabel: '{s_label.lower()}'. repo: '{repo}'")

                labels = ",".join(to_add)

        session: aiohttp.ClientSession = master.get_cache(GITHUB_SESSION)
        reqparams = {}
        if labels:
            reqparams["labels"] = labels
        #logger.debug(f"reqparams are {repr(reqparams)}")
        page_get = await session.get(url, params=reqparams, headers={"Accept": "application/vnd.github.symmetra-preview+json"})
        #logger.debug(f"response link header: {page_get.headers['Link']}")
        lastpagematch = REG_GIT_HEADER_PAGENUM.search(page_get.headers["Link"])
        if not lastpagematch:
            await master.client.remove_reaction(message, "⏳", channel.server.get_server().me)
            await master.client.add_reaction(message, "❌")
            raise Exception("GitHub returned a weird Link header!")

        maxpage = int(lastpagematch.group(1))
        pagenum = random.randrange(1, maxpage)

        params = {"page" : str(pagenum)}
        if labels:
            params["labels"] = labels

        issue_page = await get_github_object(url, params=params, accept="application/vnd.github.symmetra-preview+json")
        if len(issue_page) == 0:
            continue

        rand_issue = random.choice(issue_page)["number"]

        await post_embedded_issue_or_pr(channel, repo, rand_issue)

    await master.client.remove_reaction(message, "⏳", channel.server.get_server().me)

    if not didthedeed:
        await master.client.add_reaction(message, "👎")
        await channel.send("😕 No random issue found")
        return

    await master.client.add_reaction(message, "👍")

@command("autolabels", r"(?:(\S+)#)?(?:autolabels|autolabel)")
async def autolabels_command(channel: MChannel, match: Match, message: Message) -> None:
    prefix = match.group(1)

    for repo_config in cfg:
        repo = repo_config["repo"]

        if not is_repo_valid_for_command(repo_config, channel, prefix):
            continue

        autolabels: Dict[str, str] = master.config.get_module(
                f"github.repos.{repo}.autolabels", {})
        if not autolabels:
            await master.client.add_reaction(message, "❌")
        else:
            embed = Embed()
            embed.title = f"Autolabels for {repo}"
            for label in autolabels:
                embed.description += f"{label} <> {autolabel.get(label)}\n"

            await channel.send(embed=embed)

def format_desc(desc: str) -> str:
    res = MD_COMMENT_RE.sub("", desc) # we need to use subn so it actually gets all the comments, not just the first

    if len(res) > MAX_BODY_LENGTH:
        res = res[:MAX_BODY_LENGTH] + "..."
    return res

async def post_embedded_issue_or_pr(channel: MChannel, repo: str, issueid: int, sender_data: Optional[Dict[str, Any]] = None) -> None:
    #logger.debug(f"shitposting {issueid}")
    url = github_url(f"/repos/{repo}/issues/{issueid}")
    try:
        content: Dict[str, Any] = await get_github_object(url)
    except:
        return

    #logger.debug("yes!")

    if content.get("pull_request") is not None:
        pr_url = github_url(f"/repos/{repo}/pulls/{issueid}")
        prcontent = await get_github_object(pr_url)

    # God forgive me.
    embed = Embed()
    emoji = ""
    if content["state"] == "open":
        if content.get("pull_request") is not None:
            emoji = "<:PRopened:245910125041287168>"
        else:
            emoji = "<:ISSopened:246037149873340416>"
        embed.color = COLOR_GITHUB_GREEN

    elif content.get("pull_request") is not None:
        if prcontent["merged"]:
            emoji = "<:PRmerged:437316952772444170>"
            embed.color = COLOR_GITHUB_PURPLE
        else:
            emoji = "<:PRclosed:246037149839917056>"
            embed.color = COLOR_GITHUB_RED

    else:
        emoji = "<:ISSclosed:246037286322569216>"
        embed.color = COLOR_GITHUB_RED

    embed.title = emoji + content["title"]
    embed.url = content["html_url"]
    embed.set_footer(
        text=f"{repo}#{content['number']} by {content['user']['login']}", icon_url=content["user"]["avatar_url"])

    if sender_data is not None:
        embed.set_author(
            name=sender_data["login"], url=sender_data["html_url"], icon_url=sender_data["avatar_url"])

    embed.description = format_desc(content["body"]) + "\n"

    #we count all reactions, alternative would be to make one request for each reaction by adding content=myreaction as a param
    reactions = await get_github_object(f"{url}/reactions?per_page=100", accept="application/vnd.github.squirrel-girl-preview+json")
    all_reactions: DefaultDict[str, int] = defaultdict(int)
    for react in reactions:
        all_reactions[react["content"]] += 1

    did_up = False
    if all_reactions.get("+1"):
        up = all_reactions["+1"]
        embed.description += f"<:upvote:590257887826411590> {up}"
        did_up = True


    if all_reactions.get("-1"):
        down = all_reactions["-1"]
        if did_up:
            embed.description += "   "
        embed.description += f"<:downvote:590257835447812207> {down}"


    if content.get("pull_request") is not None:
        merge_sha = prcontent["head"]["sha"]
        check_content = await get_github_object(github_url(f"/repos/{repo}/commits/{merge_sha}/check-runs"), accept="application/vnd.github.antiope-preview+json")

        #logger.debug(check_content)
        #get the admemes to add icons for all the checks so we can do this prettier
        checks = ""
        for check in check_content["check_runs"]:
            status = "❓"
            if check["status"] == "queued":
                status = "😴"
            elif check["status"] == "in_progress":
                status = "🏃"
            elif check["status"] == "completed":
                if check["conclusion"] == "neutral": #would sure be nice to just know these huh GITHUB
                    status = "😐"
                elif check["conclusion"] == "success":
                    status = "😄"
                elif check["conclusion"] == "failure":
                    status = "😭"
                elif check["conclusion"] == "cancelled":
                    status = "🛑"
                elif check["conclusion"] == "timed_out":
                    status = "⌛"
                elif check["conclusion"] == "action_required":
                    status = "🚧"

            cname = check["name"]
            checks += f"`{cname} {status}`\n" #will only need \n as long as we got no icons

        if checks:
            embed.add_field(name="Checks", value=checks)

        if prcontent["mergeable"] is not None and not prcontent["mergeable"]:
            embed.add_field(name="status", value="🚨CONFLICTS🚨")

    embed.description += "\u200B"

    await channel.send(embed=embed)
