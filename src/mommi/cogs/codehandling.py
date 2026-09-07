from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import discord

from mommi.bot import MoMMI
from mommi.context import ChannelContext
from mommi.framework import MoMMICog, regex_command

LOGGER = logging.getLogger(__name__)

CODEBLOCK_RE = re.compile(r"\s*```(?:(?P<language>[^\r\n]*)\r?\n)?(?P<code>.*)```", re.DOTALL)

MAX_CODE_LENGTH = 8000
MAX_OUTPUT = 1500
RUN_TIMEOUT = 30.0


SANDBOX_WORKDIR = "/work"


DEFAULT_NICE = 19
DEFAULT_CPU_SECONDS = 15
DEFAULT_MEMORY_MB = 512


BYOND_BANNER_RE = re.compile(
    r"^(?:World opened on network port \d+\.|Welcome BYOND!.*|"
    r"[A-Z][a-z]{2} [A-Z][a-z]{2} .*\d{4})$",
    re.MULTILINE,
)


DEFINES_ENTRY_RE = re.compile(r"^\s*/(?:world|proc|client)/", re.MULTILINE)


COMPILE_FILE_READ_RE = re.compile(
    r"""(?xm)
    ^\s*\#\s*include\b            # #include directive
    | '[^']*[\\/][^']*'          # 'resource' literal containing a path separator
    """
)

DM_TEMPLATE = (
    "/proc/mommi_main()\n"
    "{body}\n"
    "/world/New()\n"
    "\tmommi_main()\n"
    "\tdel(src)\n"
)


class SandboxUnavailable(RuntimeError):
    """A configured sandbox can't actually run here."""


class CodeHandling(MoMMICog):
    @regex_command("runcode", r"```(?:(?P<language>[^\r\n]*)\r?\n)?(?P<code>[\s\S]*?)```")
    async def runcode(
        self, ctx: ChannelContext, match: re.Match[str], message: discord.Message
    ) -> None:
        language = (match.group("language") or "").strip().lower()
        code = match.group("code")

        if not ctx.server_config("modules.codehandling.enabled", False):
            return
        if len(code) > MAX_CODE_LENGTH:
            await ctx.send("That's too much code.")
            return

        allowed = ctx.server_config("modules.codehandling.languages", ["dm"])
        if language not in allowed:
            if language:
                await ctx.send(f"I can't run `{language}` here. I can run: {', '.join(allowed)}.")
            return

        runner = {"dm": self._run_dm, "dreammaker": self._run_dm, "python": self._run_python}.get(
            language
        )
        if runner is None:
            return

        try:
            await message.add_reaction("⏳")
        except discord.HTTPException:
            pass

        try:
            output = await runner(ctx, code)
        except asyncio.TimeoutError:
            output = "Timed out."
        except Exception:
            LOGGER.exception("Code execution blew up.")
            output = "Something went badly wrong. Check my logs."

        cleaned = (output or "(no output)").strip()[:MAX_OUTPUT]
        await ctx.send(f"```\n{cleaned}\n```")

    async def _run_dm(self, ctx: ChannelContext, code: str) -> str:
        compiler = ctx.module_config("codehandling.dreammaker", None) or shutil.which("DreamMaker")
        daemon = ctx.module_config("codehandling.dreamdaemon", None) or shutil.which("DreamDaemon")
        if not compiler or not daemon:
            return "No BYOND toolchain configured; I can't compile DM."


        if COMPILE_FILE_READ_RE.search(code):
            return (
                "Rejected: `#include` and file-resource literals aren't allowed -- "
                "they read files during compilation, before the sandbox applies."
            )

        daemon_path = Path(str(daemon))
        env = self._byond_env(ctx, daemon_path)
        system = Path(env["BYOND_SYSTEM"])
        try:
            wrap = _sandbox_wrapper(ctx, system)
        except SandboxUnavailable as e:
            LOGGER.error("DM execution refused: %s", e)
            return str(e)


        if DEFINES_ENTRY_RE.search(code):
            source = code
        else:
            source = DM_TEMPLATE.format(body=_indent(code))

        with tempfile.TemporaryDirectory(prefix="mommi-dm-") as tmpdir:
            directory = Path(tmpdir)
            (directory / "code.dm").write_text(source, encoding="utf-8")
            (directory / "code.dme").write_text('#include "code.dm"\n', encoding="utf-8")


            sandbox = wrap(directory) if wrap else []
            workdir = SANDBOX_WORKDIR if wrap else str(directory)


            throttle = _throttle_prefix(ctx)
            prefix = [*throttle, *sandbox]

            compile_out = await _run_process(
                [*prefix, str(compiler), "code.dme"], directory, RUN_TIMEOUT, env
            )
            if not (directory / "code.dmb").exists():
                return f"Compilation failed:\n{_clean(compile_out)}"

            run_out = await _run_process(
                [
                    *prefix,
                    str(daemon),
                    "code.dmb",


                    "-safe",
                    "-home",
                    workdir,
                    "-close",
                    "-once",

                    "-invisible",
                ],
                directory,
                RUN_TIMEOUT,
                env,
            )
            return _clean(run_out) or "(no output)"

    def _byond_env(self, ctx: ChannelContext, daemon: Path) -> dict[str, str]:  # noqa: D401
        import os

        env = dict(os.environ)
        system = ctx.module_config("codehandling.byond-system", None)
        if not system:

            system = str(daemon.resolve().parent.parent)
        bindir = str(Path(system) / "bin")
        env["BYOND_SYSTEM"] = system
        env["LD_LIBRARY_PATH"] = bindir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        return env

    async def _run_python(self, ctx: ChannelContext, code: str) -> str:
        command = ctx.module_config("codehandling.python-sandbox", None)
        if not command:
            return (
                "No Python sandbox is configured, and I won't run arbitrary code in my own "
                "process. Set `[codehandling] python-sandbox` in modules.toml."
            )

        import shlex


        argv = shlex.split(str(command))
        return _clean_python(
            await _run_process(argv, Path.cwd(), RUN_TIMEOUT, stdin=code.encode("utf-8"))
        )

    def help_articles(self) -> dict[str, Any]:
        return {
            "code": (
                "Post a fenced code block with a language tag and I'll try to run it, "
                "if this server has it enabled.\n\n"
                "DM snippets get compiled with BYOND's DreamMaker and run once under "
                "DreamDaemon. Output is whatever the snippet writes."
            )
        }


def _indent(code: str) -> str:
    import textwrap

    dedented = textwrap.dedent(code.strip("\n"))
    out = []
    for line in dedented.splitlines():
        if not line.strip():
            out.append("")
            continue

        stripped = line.lstrip()
        depth = len(line) - len(stripped)
        out.append("\t" * (1 + depth // 4 + line[:depth].count("\t")) + stripped)
    return "\n".join(out)


def _bwrap_works(bwrap: str) -> bool:
    """Whether bwrap can actually create its namespaces here.

    Cached. In a container bwrap usually can't (Docker blocks unprivileged user
    namespaces without --privileged), so an admin who copies a bare-metal config
    with `sandbox = "bwrap"` into Docker would otherwise get a confusing compile
    failure. We probe once and fail loudly instead.
    """
    import subprocess

    cache: dict[str, bool] = getattr(_bwrap_works, "_cache", {})
    if bwrap in cache:
        return cache[bwrap]
    probe = [bwrap, "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib"]
    for extra in ("/lib64", "/lib32"):
        if Path(extra).exists():
            probe += ["--ro-bind", extra, extra]
    probe += ["--unshare-all", "true"]
    try:
        result = subprocess.run(probe, capture_output=True, timeout=10)
        ok = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    cache[bwrap] = ok
    _bwrap_works._cache = cache  # type: ignore[attr-defined]
    return ok


def _throttle_prefix(ctx: ChannelContext) -> list[str]:
    nice_bin = shutil.which("nice")
    prlimit_bin = shutil.which("prlimit")
    if not nice_bin or not prlimit_bin:
        if not getattr(_throttle_prefix, "_warned", False):
            LOGGER.warning(
                "nice/prlimit not found; code runs without CPU/memory caps, only the "
                "wall-clock timeout. A spin loop can peg a core until it fires."
            )
            _throttle_prefix._warned = True  # type: ignore[attr-defined]
        return []

    niceness = int(ctx.module_config("codehandling.nice", DEFAULT_NICE))
    cpu_seconds = int(ctx.module_config("codehandling.cpu-seconds", DEFAULT_CPU_SECONDS))
    memory_mb = int(ctx.module_config("codehandling.memory-mb", DEFAULT_MEMORY_MB))

    prefix = [nice_bin, "-n", str(max(0, min(19, niceness))), prlimit_bin]
    if cpu_seconds > 0:
        prefix.append(f"--cpu={cpu_seconds}")
    if memory_mb > 0:
        prefix.append(f"--as={memory_mb * 1024 * 1024}")
    prefix.append("--")
    return prefix


def _sandbox_wrapper(ctx: ChannelContext, byond_system: Path) -> Callable[[Path], list[str]] | None:
    sandbox = ctx.module_config("codehandling.sandbox", None)
    if not sandbox:
        if not getattr(_sandbox_wrapper, "_warned", False):
            LOGGER.warning(
                "DM code execution has no `[codehandling] sandbox` set. -safe still "
                "blocks file and shell access, but world.Export() network egress is "
                "open. Set sandbox = \"bwrap\" to isolate it."
            )
            _sandbox_wrapper._warned = True  # type: ignore[attr-defined]
        return None

    bwrap = sandbox if sandbox != "bwrap" else (shutil.which("bwrap") or "bwrap")

    if not _bwrap_works(bwrap):
        # Configured but can't create namespaces. Fail closed: refuse to run DM
        # rather than silently dropping the sandbox the admin asked for.
        raise SandboxUnavailable(
            "The `bwrap` sandbox is configured but can't create namespaces here "
            "(common inside Docker, which blocks unprivileged user namespaces). "
            "Run the bot on bare metal for bwrap, or drop `sandbox` from "
            "modules.toml -- in a container the container itself is the boundary."
        )

    def build(scratch: Path) -> list[str]:
        argv = [
            bwrap,
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
        ]
        for optional in ("/lib64", "/lib32", "/bin", "/sbin"):
            if Path(optional).exists():
                argv += ["--ro-bind", optional, optional]
        argv += [
            "--ro-bind", str(byond_system), str(byond_system),
            "--bind", str(scratch), SANDBOX_WORKDIR,
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",


            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--setenv", "BYOND_SYSTEM", str(byond_system),
            "--setenv", "LD_LIBRARY_PATH", str(byond_system / "bin"),
            "--setenv", "HOME", SANDBOX_WORKDIR,
            "--chdir", SANDBOX_WORKDIR,
        ]
        return argv

    return build


async def _run_process(
    argv: list[str],
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
) -> str:
    import os
    import signal


    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(stdin), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        await process.wait()
        raise
    return stdout.decode("utf-8", errors="replace")


def _clean(output: str) -> str:
    stripped = BYOND_BANNER_RE.sub("", output)
    return "\n".join(line for line in stripped.splitlines() if line.strip())


def _clean_python(output: str) -> str:
    return output.strip() or "(no output)"


async def setup(bot: MoMMI) -> None:
    await bot.add_cog(CodeHandling(bot))
