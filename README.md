# MoMMI v3

Discord bot for the [/vg/station13](http://ss13.moe/) Discord server. A
reimplementation of [MoMMI v2](https://github.com/toomykins/MoMMI) for modern
Python, keeping the same feature set, the same commands, and the same
configuration files.

**It is a drop-in replacement.** Your existing `main.toml`, `servers.toml` and
`modules.toml` load unchanged, and the commloop protocol the game server speaks
is byte-for-byte identical, so nothing on the BYOND side needs editing.

---

## What changed, and why

| | v2 | v3 |
|---|---|---|
| Python | 3.6 (EOL 2021) | 3.10+ |
| Discord library | discord.py 1.7.3 (2021) | discord.py 2.x |
| Module system | `os.walk` + `importlib.reload()` + a race-workaround queue | standard discord.py cogs |
| Storage | `pickle` files, one per module per guild | SQLite |
| Webhook front end | a separate Rust service on a pinned 2023 nightly | folded into the bot |
| Tests | none | 287 |

The Rust `WebMoMMI` component is gone. It needed a specific Rust nightly,
Rocket 0.4, and the unmaintained `rust-crypto`, and it only verified GitHub
webhooks with SHA-1, which GitHub deprecated. Its routes are served by the bot
now, on the same paths, verifying `X-Hub-Signature-256` with a SHA-1 fallback.

### Things that were broken in v2 and now aren't

These had all rotted quietly rather than failing loudly:

- **Mute-to-retract on the channel mirror.** `on_reaction_add` only fires for
  messages in discord.py's cache, so after a restart, reacting 🔇 did nothing.
  Now uses the raw events.
- **`userinfo`** called `get_user_info`, renamed in discord.py 1.0 (2018).
- **The round-end channel lock** called `edit_channel_permissions`, also removed
  in 1.0, so the `#ick` lock had not worked in years.
- **The GitHub response cache** wrote entries under a 2-tuple key and read them
  back under a 3-tuple key, so it never hit once — every `[1234]` in chat spent
  fresh API rate limit.
- **`autolabels`** referenced an undefined variable and raised `NameError` on
  every invocation.
- **Removing the secret-repo-conflict label** referenced an undefined variable,
  so the label went on but never came off.
- **`ids`** used `message.server`, gone since discord.py 1.0.
- **`dance` / `away`** pointed at `/home/gutter/MoMMI/Files/`.
- **Reminder dates.** A bare `2026/01/01` kept the current time of day, and an
  empty time specifier silently meant "now".
- **Commloop reads** trusted a single `read()` call, truncating large payloads,
  and would try to allocate whatever body length a client claimed.
- **GitHub state emoji** were hardcoded to a server MoMMI is not necessarily in.
  A bot can only render a custom emoji from a guild it shares, so these rendered
  as literal `<:PRopened:2459...>` text. They now fall back to Unicode, and the
  IDs are configurable via `[github.emoji]`.
- **DM code execution ran with `-trusted`** — "any file and shell access."
  Verified against BYOND 516: a snippet posted in a configured channel could
  `file2text()` the bot's config (the token) and `shell()` arbitrary commands.
  It runs under `-safe` now, which BYOND enforces; both were confirmed blocked
  against the real toolchain. See **Code execution** below.

## New commands

- **`@MoMMI who [server]`** — who's currently connected. Read from the
  `player0..N` keys vgstation already returns in its `?status` response, so it
  needs nothing new on the game side. Disable per-server with
  `who_enabled = false`.
- **`@MoMMI players`** — counts across every configured server at once.
- **`@MoMMI graph [server] [24h|7d|2w]`** — player numbers over time, as a
  chart. `graph all 7d` overlays every server.
- **`@MoMMI highpop [server] [30d]`** — when the server is actually busy: the
  peak and when it happened, the overall average, and the busiest hour and day
  of the week by average population, with a by-hour bar chart. Hour and weekday
  averages ignore buckets with too few samples, so one stray reading at 04:00
  can't be crowned the busiest hour of the month.

Nothing upstream stores player-count history — ss13.moe's poller just rewrites a
JSON cache every second — so the graph accumulates from the moment you deploy
this. It samples every 5 minutes and keeps 90 days, both configurable.

Slash commands are registered with `@MoMMI sync`, which targets the current
server and takes effect immediately. `@MoMMI sync global` publishes everywhere
but Discord can take an hour to propagate it.

Slash-command equivalents exist for the common commands (`/status`, `/who`,
`/players`, `/graph`, `/remind`, `/roll`, `/pick`, `/convert`, `/markov`,
`/help`) alongside the `@MoMMI` prefix style, which keeps working exactly as
before. Run `@MoMMI sync` once after deploying to register them.

---

## Installing

### Docker (recommended)

The host's Python version stops mattering, which is the failure mode that
actually killed v2.

```sh
cp -r config/example/* config/     # then fill in the tokens
docker compose up -d
```

### Directly

```sh
python3 -m venv .venv
.venv/bin/pip install ".[charts]"    # drop [charts] to skip graphs
.venv/bin/mommi --config-dir ./config --data-dir ./data
```

Check a config without connecting to Discord:

```sh
.venv/bin/mommi --config-dir ./config --check-config
```

### Discord setup

In the Discord developer portal, enable both **Message Content Intent** and
**Server Members Intent**. Without them MoMMI sees empty messages and no roles.

---

## Upgrading from v2

1. **Copy your config across unchanged.** `main.toml`, `servers.toml` and
   `modules.toml` all still work. The only addition is a `[web]` block in
   `main.toml`, which replaces WebMoMMI's `Rocket.toml`:

   ```sh
   python -m mommi.migrate --rocket-toml /path/to/WebMoMMI/Rocket.toml
   ```

   That prints a `[web]` section to paste into `main.toml`.

2. **Migrate the data.** This converts the pickle jars — markov chains, `$resp`
   responses, pending reminders and pending mirrors — into SQLite:

   ```sh
   python -m mommi.migrate --old-data /path/to/v2/data --new-data ./data --dry-run
   python -m mommi.migrate --old-data /path/to/v2/data --new-data ./data
   ```

   Keep the old directory until you're satisfied. Nothing writes to it.

3. **Point the webhooks at the new port.** The paths are unchanged
   (`/mommi/github`, `/changelog`, `/dev/git_hooks/webmommi`, `/mommi/nudge`,
   `/discord`, `/mommi/ss14/<id>`), so if the bot listens where WebMoMMI did,
   there is nothing to change at all.

4. **Stop the Rust service.** It has no job any more.

---

## Configuration

See `config/example/` — the files are commented throughout.

Three keys are worth calling out:

- `[web] verify-github` — leave it `true`. It only exists for local testing.
- `modules.serverstatus.<name>.who_enabled` — set `false` to hide the `who`
  player list on a server, if your community treats it as metagame-adjacent.
  `status` still works.
- `[codehandling]` — see **Code execution** below. Both runners are off unless a
  server also opts in, and each was verified against a real toolchain.

## Code execution

The `runcode` handlers run user-supplied code, so containment is the whole game.
Both are disabled unless a server sets `modules.codehandling.enabled = true`.

**DM** runs behind three layers, because BYOND's `-safe` only covers DreamDaemon
at runtime — the *compiler* runs unsandboxed, and I confirmed against BYOND 516
that `#include "/etc/passwd"` leaks a file's contents through compile errors:

1. **Static reject** (always on): snippets containing `#include` or file-resource
   literals are refused, since those read files during compilation.
2. **OS chroot** (`sandbox = "bwrap"`, strongly recommended): both the compiler
   and the daemon run in a throwaway namespace holding only the BYOND binaries,
   system libraries, and a scratch dir — no `/etc`, no `/home`, no network. This
   is the layer that also stops `world.Export()` network egress (SSRF/exfil),
   which `-safe` permits.
3. **`-safe`** (always on): `shell()` and any file access outside the scratch dir
   raise a "Safety violation". Never `-trusted` — which is what v2 used, exposing
   the token and a shell.

Runaway snippets are capped: niced to the floor (so they never starve the bot or
the game), killed at a CPU-seconds and memory limit via `nice`/`prlimit` — which
ship with every Linux, nothing to install — and killed as a process group on the
wall-clock timeout, leaving nothing behind. Every layer is covered by integration tests that run real hostile
snippets — including `#include` and path-traversal attempts — against a live
toolchain when `MOMMI_BYOND_SYSTEM` points at a BYOND root; they skip otherwise.

**Python** has no in-process path. The snippet is handed to an external sandbox
command (`python-sandbox`) on stdin — never interpolated into a shell line — and
does nothing at all if none is configured. `config/example/modules.toml` gives
working `bwrap` and `docker` commands that isolate the filesystem and network.
The sandbox quality is the operator's responsibility; MoMMI's guarantee is that
it never executes the code itself.

## Developing

```sh
.venv/bin/pip install -e ".[dev,charts]"
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

The test suite covers the BYOND packet codec, the commloop wire format
(including a test that runs *v2's own sender* against the new server), reminder
time parsing, the storage layer, the migration path, GitHub helpers, changelog
parsing, the webhook routes, and the population aggregates.

`tests/conftest.py` stands up a real bot with real cogs and feeds it fake
messages, so `tests/test_commands.py` exercises command bodies through the
genuine `on_message` dispatch path -- prefix matching, regex routing, role
checks and all. What it can't cover is anything needing external kit: the IRC
bridge, DM compilation (needs BYOND's toolchain), TGS restart, the runtime-log
condenser, and live GitHub API calls.
