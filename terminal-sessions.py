#!/usr/bin/env python3
"""terminal-sessions.py — capture and restore Terminal.app agent sessions across a reboot.

Usage:
    terminal-sessions                         same as --list
    terminal-sessions --list [--file PATH]    snapshots, windows, and what restore would do
    terminal-sessions --capture [--file PATH]
    terminal-sessions --restore [--only NAME|tmux ...] [--file PATH] [--dry-run]
    terminal-sessions --prune [--keep N] [--dry-run]
    any mode also takes --color auto|always|never (auto: color only on a
    terminal, and never when NO_COLOR is set)

--capture writes a snapshot of every Terminal.app window and tab: which tab
groups exist, tab order inside each group, and what each tab runs. Nothing is
launched. The snapshot goes to ~/.claude/terminal-sessions/<UTC stamp>.json and
latest.json points at it. It is plain JSON; edit it before restoring if you want.
Each window gets a name: "workers" when most of its tabs sit under a workers/
directory, otherwise "owners"; a repeated name gets a -2, -3 suffix. Rename a
window by editing its "name" in the snapshot.

--list shows the saved snapshots, then each window in the chosen snapshot
(latest by default) with its tabs and what --restore would do to each.

--restore rebuilds only what the snapshot names: one Terminal window per
captured tab group, tabs in captured order, each tab resumed by its own
launcher. It never opens anything the snapshot does not list. Tabs that are
still open are skipped, so a second restore does not double-launch: an agent
tab counts as open when a Terminal tab holds the same session id, a shell tab
when an open shell sits in the same cwd. When any captured tab of a window is
still open, the missing tabs go into that window, each right after the nearest
earlier captured tab. A missing tab with no open tab before it lands after the
first open one, since Terminal cannot insert a tab at the front.
--only NAME restores just that part (repeat it for several): a window name such
as workers, or tmux. An unknown name is refused and the known names are listed.
--dry-run prints the plan and opens nothing.

--prune deletes old snapshots, keeping the newest N (--keep, default 5). The
snapshot latest.json points at is always kept. Only files this tool writes
(<UTC stamp>.json in ~/.claude/terminal-sessions) are touched. --dry-run lists
what would go.

Per tab, restore runs (<opts> = the options the session was started with,
from an allowlist per tool such as --model, --effort, --advisor, --yolo):
  Claude Code   claude-<scope> <opts> --resume <session id>   (id from ~/.claude/sessions/<pid>.json)
  Copilot CLI   copilot-<scope> <opts> --resume=<session id>  (id from ~/.copilot/session-state/*/inuse.<pid>.lock,
                                                              else --resume argv)
  OpenCode      <oc launcher> <opts> -s <session id>          (id from -s argv, else the session in its
                                                              project dir updated last since it started;
                                                              --continue when no id is found)
Options outside the allowlist are not replayed and are listed per tab as
args_not_replayed in the snapshot.
  plain shell   cd <cwd>, then the command that was running (ssh, make, tail, ...)
                typed as ps shows it, so an argument that held a space loses its
                quotes. Not re-run: an agent binary (its launcher resumes it), and
                a command over 1000 characters. If the tab is already open but
                idle, as after a reboot, the command is typed into that tab.
When the scope launcher function does not exist, the bare binary is used from the tab's cwd.

Right before the first tab of a kind opens, restore updates that tool, once:
`claude --update` for Claude Code, `brew upgrade copilot-cli` for Copilot CLI.
A kind with no tab to open (all already open, or filtered out by --only) is not
updated, and OpenCode and tmux windows are never updated. A failed update is reported and the restore
continues. --dry-run lists the updates without running them.

tmux: --capture records every session as a recipe, per window its name, directory
and the command tmux started it with (or the command typed into a shell window).
Only each window's first pane is kept. --restore rebuilds each session that is
not running, exactly as it ran, agent CLIs included and always as a new session:
no resume flag is added. A running session is left alone. A session that must
restart through its owner's script (a start-app.sh that sets its environment,
cleans up the old processes and sizes the window) is named in
~/.claude/terminal-sessions/tmux-launchers.json as {"session": "command"}. Capture
copies that command into the snapshot and restore runs it, in a login shell, in
place of rebuilding the windows.

Tab groups: Terminal's AppleScript sees every tab as its own window, so tabs are
grouped by identical window bounds. Tab order comes from the tab bar via System
Events, which only sees the current Space. A window on another desktop keeps
the order an earlier capture read for it, so with windows on two desktops, run
--capture once from each. With no earlier reading the order is guessed from
window creation, and --list says so on that window's header line.
Restoring tabs sends Cmd-T through System Events, which needs Terminal to hold
Accessibility permission.

Exit codes: 0 success, 1 capture or restore error, 2 usage error.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import datetime
import glob
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVICES_DIR))
try:
    from scope_detect import detect_scope  # noqa: E402
except ImportError:  # optional: without it no launcher is named from a directory, and tabs use the bare binary
    detect_scope = None

HOME = Path.home()
STORE = HOME / ".claude" / "terminal-sessions"
CLAUDE_SESSIONS = HOME / ".claude" / "sessions"
COPILOT_STATE = Path(os.environ.get("COPILOT_HOME", HOME / ".copilot")) / "session-state"
OPENCODE_DBS = [HOME / ".local/share/opencode/opencode-local.db", HOME / ".local/share/opencode/opencode.db"]
WS_PATHS = SERVICES_DIR.parent / ".workspace-paths"
FG_MAX = 1000  # longest foreground command kept; a longer one is cut and never re-run
AGENT_BINARIES = {"claude", "copilot", "opencode"}  # agent tabs resume through their launchers
SEP = " — "  # Terminal title separator (em dash)
LAUNCHERS = STORE / "tmux-launchers.json"
SNAPSHOT_NAME = re.compile(r"\d{8}T\d{6}Z\.json")


# ── shell helpers ────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 60) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({out.returncode}): {out.stderr.strip()[:300]}")
    return out.stdout


def _osascript(script: str) -> str:
    return _run(["osascript", "-e", script])


def _as_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ── capture: Terminal windows ───────────────────────────────────────────────

_TERMINAL_WINDOWS = '''
set TB to (ASCII character 9)
tell application "Terminal"
  set out to ""
  repeat with w in windows
    -- A tab closed by script can linger as an invisible window with no tty.
    if visible of w then
      set b to bounds of w
      set t to selected tab of w
      set out to out & (id of w) & TB & (item 1 of b) & "," & (item 2 of b) & "," & (item 3 of b) & "," & (item 4 of b) & TB & (tty of t) & TB & (custom title of t) & TB & (name of w) & linefeed
    end if
  end repeat
  return out
end tell
'''

_TAB_BARS = '''
tell application "System Events" to tell process "Terminal"
  set out to ""
  repeat with w in windows
    set out to out & "WINDOW" & linefeed
    try
      repeat with tg in (tab groups of w)
        repeat with rb in (radio buttons of tg)
          set out to out & (name of rb) & linefeed
        end repeat
      end repeat
    end try
  end repeat
  return out
end tell
'''


def _terminal_windows() -> list[dict]:
    rows = []
    for line in _osascript(_TERMINAL_WINDOWS).splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        wid, bounds, tty, title, name = parts
        rows.append({"window_id": int(wid), "bounds": [int(x) for x in bounds.split(",")],
                     "tty": tty.replace("/dev/", ""), "custom_title": title, "name": name})
    return rows


def _tab_bars() -> list[list[str]]:
    """Tab names per visible tab group, in tab-bar order. Empty when unreadable."""
    try:
        text = _osascript(_TAB_BARS)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"note: tab order unavailable ({exc}); using window creation order", file=sys.stderr)
        return []
    bars, cur = [], None
    for line in text.splitlines():
        if line == "WINDOW":
            cur = []
            bars.append(cur)
        elif cur is not None and line:
            cur.append(line)
    return [b for b in bars if b]


def _title_tail(name: str) -> str:
    """Stable match key shared by a tab-bar name and a window name.

    The tab bar shows "<cwd> — <title> — ..." and the window name shows
    "<cwd basename> — <title> — ...". Agent titles carry an animated status
    glyph that can change between the two reads, so it is stripped.
    """
    parts = name.split(SEP)
    head = parts[0].rstrip("/").rsplit("/", 1)[-1]
    if head == "~":  # the tab bar shows the home dir as "~", the window name by its basename
        head = HOME.name
    title = re.sub(r"^\W+", "", parts[1]) if len(parts) > 1 else ""
    return f"{head}|{title}"


def _order_by_bar(members: list[dict], bars: list[list[str]]) -> bool:
    """Sort one tab group into tab-bar order. False when no bar matches it."""
    for bar in bars:
        tails = [_title_tail(n) for n in bar]
        hits = [r for r in members if _title_tail(r["name"]) in tails]
        if len(hits) * 2 >= len(members):
            rank = {t: i for i, t in enumerate(tails)}
            members.sort(key=lambda r: rank.get(_title_tail(r["name"]), len(tails) + r["window_id"]))
            return True
    return False


def _order_from_earlier(members: list[dict], earlier: list[dict]) -> bool:
    """Sort a group whose tab bar is unreadable into the order an earlier
    capture read from a bar. Tabs are matched by what they are, not by window
    id, so this survives a Terminal restart: first on directory plus title, then
    on title alone (after a restart a tab's window name can lead with the home
    directory), then on directory alone for tabs whose title moved on. The
    earlier window must be the same group, so most of it has to match too: a
    window that swallowed several groups is never a source. Tabs the earlier
    capture did not see go last. False when no earlier window fits."""
    for snap in earlier:
        for w in snap.get("windows", []):
            if w.get("order") not in ("tab bar", "earlier capture"):
                continue
            saved = [_title_tail(f"{t.get('cwd', '')}{SEP}{t.get('title', '')}").split("|", 1) for t in w["tabs"]]
            rank: dict[int, int] = {}
            for same in (lambda a, b: a == b, lambda a, b: a[1] == b[1] != "", lambda a, b: a[0] == b[0]):
                for k, tail in enumerate(saved):
                    if k in rank.values():
                        continue
                    row = next((r for r in members if r["window_id"] not in rank
                                and same(tail, _title_tail(r["name"]).split("|", 1))), None)
                    if row:
                        rank[row["window_id"]] = k
            if len(rank) * 2 >= len(members) and len(rank) * 2 >= len(saved):
                members.sort(key=lambda r: rank.get(r["window_id"], len(saved) + r["window_id"]))
                return True
    return False


def _split_by_bars(members: list[dict], bars: list[list[str]]) -> list[tuple[list[dict], str]]:
    """Split windows that share one frame into tab groups. Two Terminal windows
    can end up with identical bounds (after a restart, or both resized to the
    same frame), and bounds alone then merge them into one group. A readable tab
    bar names exactly the tabs of one group, so the windows that fill a bar are
    that group; what no bar claims is the rest, a group on a desktop System
    Events cannot read. Each bar claims a tab name no more times than it lists it,
    so a shell tab in the other group that happens to share a title is not taken."""
    left, groups = list(members), []
    for bar in bars:
        tails = [_title_tail(n) for n in bar]
        want = {t: tails.count(t) for t in tails}
        mine = []
        for r in left:
            t = _title_tail(r["name"])
            if want.get(t, 0) > 0:
                want[t] -= 1
                mine.append(r)
        if len(mine) * 2 >= len(bar):
            rank = {t: i for i, t in enumerate(tails)}
            mine.sort(key=lambda r: rank.get(_title_tail(r["name"]), len(tails) + r["window_id"]))
            groups.append((mine, "tab bar"))
            left = [r for r in left if r not in mine]
    if left:
        groups.append((left, ""))
    return groups


def _group_tabs(rows: list[dict]) -> list[tuple[list[dict], str]]:
    """Tab groups in tab order, each with how that order was found.

    System Events reads tab bars only on the current Space. A group on another
    Space keeps the order an earlier capture read for it, or else falls back to
    window creation order. Fronting the group to read its bar is not an option:
    Terminal moves the window onto the current Space instead of switching.
    """
    frames: dict[tuple, list[dict]] = {}
    for r in rows:
        frames.setdefault(tuple(r["bounds"]), []).append(r)
    bars = _tab_bars()
    earlier = []
    for f in _snapshots()[:5]:
        try:
            earlier.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    out = []
    for members in frames.values():
        members.sort(key=lambda r: r["window_id"])
        for group, order in _split_by_bars(members, bars):
            if not order:
                group.sort(key=lambda r: r["window_id"])
                order = "earlier capture" if _order_from_earlier(group, earlier) else "guessed"
            out.append((group, order))
    out.sort(key=lambda g: min(r["window_id"] for r in g[0]))
    return out


# ── capture: what each tab runs ─────────────────────────────────────────────

def _processes() -> list[dict]:
    procs = []
    for line in _run(["ps", "-axo", "pid=,ppid=,stat=,tty=,command="]).splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5:
            procs.append({"pid": int(parts[0]), "ppid": int(parts[1]), "stat": parts[2],
                          "tty": parts[3], "command": parts[4]})
    return procs


def _cwd(pid: int) -> str:
    try:
        out = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], timeout=15)
    except (RuntimeError, subprocess.TimeoutExpired):
        return ""
    return next((ln[1:] for ln in out.splitlines() if ln.startswith("n")), "")


def _env_value(pid: int, key: str) -> str:
    try:
        out = _run(["ps", "-E", "-p", str(pid), "-o", "command="], timeout=15)
    except (RuntimeError, subprocess.TimeoutExpired):
        return ""
    m = re.search(rf"(?:^|\s){re.escape(key)}=(\S+)", out)
    return m.group(1) if m else ""


def _claude_sessions() -> dict[int, dict]:
    out = {}
    for f in CLAUDE_SESSIONS.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data.get("pid"), int) and data.get("sessionId"):
            out[data["pid"]] = data
    return out


def _copilot_locks() -> dict[int, str]:
    """Copilot native pid -> session id. "" when the session has no content yet,
    so such a tab is restored as a fresh launch. Content means session.db (older
    Copilot) or a non-empty events.jsonl: on this node Copilot stopped writing
    session.db after 2026-08-19 (1.0.91 writes only events.jsonl)."""
    out = {}
    for lock in glob.glob(str(COPILOT_STATE / "*" / "inuse.*.lock")):
        m = re.search(r"inuse\.(\d+)\.lock$", lock)
        if not m:
            continue
        sess_dir = Path(lock).parent
        events = sess_dir / "events.jsonl"
        has_content = (sess_dir / "session.db").exists() or (events.exists() and events.stat().st_size > 0)
        out[int(m.group(1))] = sess_dir.name if has_content else ""
    return out


def _opencode_session(directory: str, started: float) -> str:
    """The session this OpenCode process is on: among top-level sessions in its
    project directory created since the process started, the one updated last.
    A user who switched sessions mid-run lands on the one they used last."""
    best: tuple[int, str] | None = None
    for db in OPENCODE_DBS:
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            row = con.execute(
                "SELECT time_updated, id FROM session WHERE directory=? AND parent_id IS NULL "
                "AND time_created>=? ORDER BY time_updated DESC LIMIT 1",
                (directory, int(started * 1000) - 60_000)).fetchone()
            con.close()
        except sqlite3.Error:
            continue
        if row and (best is None or row[0] > best[0]):
            best = row
    return best[1] if best else ""


def _opencode_dir(command: str, cwd: str) -> str:
    """The project directory OpenCode was opened on: launchers pass it as a positional path."""
    tokens = command.split()
    skip = False
    for tok in tokens[1:]:
        if skip:
            skip = False
            continue
        if tok in ("--cwd",):
            skip = True
            continue
        if tok.startswith("/") and Path(tok).is_dir():
            return tok
    return cwd


# Options worth carrying across a restore, per tool: flag -> takes a value.
# Anything else (launcher-owned flags, resume/prompt flags, unknown options) is
# not replayed; unknown ones are listed in the snapshot as args_not_replayed so
# the gap is visible. An allowlist, because ps flattens argv: a multi-word
# prompt cannot be told apart from flags, and replaying it would be wrong.
CARRY = {
    "claude": {"--model": True, "--effort": True, "--advisor": True, "--permission-mode": True,
               "--agent": True, "--fallback-model": True, "--dangerously-skip-permissions": False,
               "--verbose": False},
    "copilot": {"--model": True, "--agent": True, "-i": True, "--interactive": True, "--yolo": False, "--allow-all": False,
                "--allow-all-tools": False, "--allow-all-paths": False, "--autopilot": False},
    "opencode": {"--model": True, "-m": True, "--agent": True, "--variant": True},
}
# Flags the scope launchers add themselves, or that select a session or prompt.
NOT_CARRIED = {
    "claude": {"--dangerously-load-development-channels": True, "--name": True, "-n": True,
               "--resume": None, "-r": None, "--continue": False, "-c": False,
               "--session-id": True, "--fork-session": False},
    "copilot": {"--additional-mcp-config": True, "--no-experimental": False, "--experimental": False,
                "--prompt": None, "-p": None, "--resume": None,
                "--continue": False, "--available-tools": True, "--excluded-tools": True},
    "opencode": {"-s": True, "--session": True, "-c": False, "--continue": False,
                 "--cwd": True, "--conditions": True},
}


def _argv(pid: int, command: str) -> list[str]:
    """A process's real argv. ps joins the arguments with spaces, so a value
    holding spaces, like a prompt, cannot be told from several arguments; the
    kernel's KERN_PROCARGS2 keeps them apart. Falls back to splitting ps's
    text, which is right for everything but such values."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    size = ctypes.c_size_t(0)
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, pid
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return command.split()
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return command.split()
    raw = buf.raw[:size.value]
    argc = int.from_bytes(raw[:4], sys.byteorder)
    rest = raw[4:].split(b"\0")
    i = 1
    while i < len(rest) and rest[i] == b"":  # skip the exec path's padding
        i += 1
    args = [a.decode("utf-8", "replace") for a in rest[i:i + argc]]
    return args if len(args) == argc else command.split()


def _carry_args(kind: str, argv: list[str]) -> tuple[list[str], list[str]]:
    """(options to replay, flag names not replayed) from a process's argv."""
    keep, dropped = [], []
    carry, owned = CARRY.get(kind, {}), NOT_CARRIED.get(kind, {})
    tokens = argv[1:]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        flag, has_eq = (tok.split("=", 1)[0], True) if tok.startswith("--") and "=" in tok else (tok, False)
        if flag in carry:
            if carry[flag] and not has_eq:
                if i + 1 < len(tokens):
                    keep += [flag, tokens[i + 1]]
                i += 2
                continue
            keep.append(tok)
        elif flag in owned:
            takes = owned[flag]
            if takes is True and not has_eq:
                i += 1
            elif takes is None and not has_eq and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                i += 1
        elif tok.startswith("-"):
            dropped.append(flag)
        i += 1
    return keep, sorted(set(dropped))


def _start_epoch(pid: int) -> float:
    try:
        out = _run(["ps", "-o", "lstart=", "-p", str(pid)], timeout=15).strip()
        return time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y"))
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def _scope(cwd: str) -> str:
    try:
        return detect_scope(cwd, WS_PATHS)[0] if cwd and detect_scope else ""
    except Exception as exc:  # scope only names a launcher; a miss falls back to the bare binary
        print(f"note: scope lookup failed for {cwd}: {exc}", file=sys.stderr)
        return ""


def _arg_after(command: str, flags: tuple[str, ...]) -> str:
    """The value given to one of ``flags``, or "". A bare flag followed by
    another option has no value: `--resume --yolo` is a picker, not an id."""
    tokens = command.split()
    for i, tok in enumerate(tokens):
        for f in flags:
            if tok == f and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                return tokens[i + 1]
            if tok.startswith(f + "="):
                return tok.split("=", 1)[1]
    return ""


def _describe_tab(tty: str, procs: list[dict], claude: dict, copilot: dict) -> dict:
    on_tty = [p for p in procs if p["tty"] == tty]
    pids = {p["pid"] for p in on_tty}
    shells = [p for p in on_tty if p["command"].lstrip("-").split()[0].endswith(("zsh", "bash"))]
    shell_pids = {p["pid"] for p in shells}
    # Terminal runs `login` on the tab's tty, which starts the login shell.
    shell = next((p for p in shells if p["ppid"] not in shell_pids), None)
    by_pid = {p["pid"]: p for p in on_tty}
    for pid in sorted(pids):
        if pid in claude:
            d = claude[pid]
            args, dropped = _carry_args("claude", _argv(pid, by_pid[pid]["command"]))
            return {"kind": "claude", "session_id": d["sessionId"], "launcher": d.get("name") or "",
                    "cwd": d.get("cwd", ""), "args": args, "args_not_replayed": dropped}
    for pid in sorted(pids):
        cmd = by_pid[pid]["command"]
        if pid in copilot or Path(cmd.split()[0]).name == "copilot":
            # The inuse lock names the session, a resumed one included. A lock
            # on a session with no content yet maps to "", so the id falls back
            # to a --resume value in argv. Still "" when neither has it: a
            # fresh launch.
            sid = copilot.get(pid) or _arg_after(cmd, ("--resume",))
            cwd = _cwd(pid)
            scope = _scope(cwd)
            args, dropped = _carry_args("copilot", _argv(pid, cmd))
            return {"kind": "copilot", "session_id": sid,
                    "launcher": f"copilot-{scope}" if scope else "", "cwd": cwd,
                    "args": args, "args_not_replayed": dropped}
    for p in on_tty:
        cmd = p["command"]
        if "opencode" in cmd and ("src/index.ts" in cmd or re.search(r"(^|/)opencode(\s|$)", cmd)):
            cwd = _opencode_dir(cmd, _cwd(p["pid"]))
            sid = _arg_after(cmd, ("-s", "--session"))
            method = "argv"
            if not sid:
                sid, method = _opencode_session(cwd, _start_epoch(p["pid"])), "db-latest-updated"
            name = _env_value(p["pid"], "ISAAC_OPENCODE_SESSION_NAME") or _env_value(p["pid"], "ISAAC_OPENCODE_WORKER_NAME")
            args, dropped = _carry_args("opencode", _argv(p["pid"], cmd))
            return {"kind": "opencode", "session_id": sid, "session_id_method": method,
                    "launcher": name, "cwd": cwd, "args": args, "args_not_replayed": dropped}
    # The tab running this capture is not "running" anything worth recording,
    # and neither are the ps/lsof children this capture spawns on that tty.
    me = os.getpid()
    fg = next((p for p in on_tty if "+" in p["stat"] and p is not shell and me not in (p["pid"], p["ppid"])
               and "terminal-sessions" not in p["command"]), None)
    return {"kind": "shell", "cwd": _cwd(shell["pid"]) if shell else "",
            "foreground": fg["command"][:FG_MAX] if fg else ""}


def _replayable(fg: str) -> bool:
    """Whether a tab's foreground command is one restore re-runs. An agent
    binary is not: it would start a second copy of a session its launcher
    already resumes. A command cut at FG_MAX is not: half a command is not
    what was running."""
    if not fg or len(fg) >= FG_MAX or "terminal-sessions" in fg:
        return False
    return Path(fg.split()[0]).name not in AGENT_BINARIES


def _short_cmd(command: str) -> str:
    """A process command with its executable's directory dropped."""
    exe, _, rest = command.partition(" ")
    return f"{Path(exe).name} {rest}".strip()


def _tmux_launchers() -> dict[str, str]:
    """Sessions that restart through their owner's own script, kept by hand in
    LAUNCHERS as {"session": "command"}. Windows alone cannot replay what such
    a script does: environment, cleanup of the old processes, tmux sizing. A
    missing file means none. An unreadable one stops the run: falling back to
    the plain recipe would restart that session without all of it."""
    try:
        data = json.loads(LAUNCHERS.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{_home(str(LAUNCHERS))} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise RuntimeError(f"{_home(str(LAUNCHERS))} must map a session name to a command string")
    return data


def _tmux_capture(procs: list[dict]) -> list[dict]:
    """Every tmux session as the recipe to rebuild it: per window its name,
    directory, and either the command tmux started it with or, for a plain
    shell window, the command typed into it. Only a window's first pane."""
    fmt = "#{session_name}|#{window_index}|#{window_name}|#{pane_index}|#{pane_current_path}|#{pane_pid}|#{pane_start_command}"
    try:
        out = _run(["tmux", "list-panes", "-a", "-F", fmt], timeout=15)
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    sessions: dict[str, list[dict]] = {}
    for line in out.splitlines():
        parts = line.split("|", 6)
        if len(parts) != 7 or parts[3] != "0":
            continue
        name, _, wname, _, path, pid, start = parts
        run = ""
        if not start:
            kid = next((p for p in procs if p["ppid"] == int(pid)), None)
            run = _short_cmd(kid["command"]) if kid else ""
        sessions.setdefault(name, []).append({"name": wname, "cwd": path, "start": start, "run": run})
    launchers = _tmux_launchers()
    return [dict({"name": n, "windows": ws}, **({"launcher": launchers[n]} if n in launchers else {}))
            for n, ws in sessions.items()]


def _tmux_sessions() -> list[str]:
    try:
        return _run(["tmux", "list-sessions", "-F", "#{session_name}"], timeout=15).split()
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _launchers_present(names: set[str]) -> set[str]:
    names = {n for n in names if re.fullmatch(r"[A-Za-z0-9_.-]+", n)}
    if not names:
        return set()
    out = subprocess.run(["zsh", "-ic", "whence -w " + " ".join(sorted(names))],
                         capture_output=True, text=True, timeout=60)
    return {line.split(":")[0] for line in out.stdout.splitlines() if line.endswith(": function")}


def _window_name(tabs: list[dict], earlier: list[dict]) -> str:
    in_workers = sum(1 for t in tabs if "/workers/" in (t.get("cwd") or "") + "/")
    base = "workers" if in_workers * 2 > len(tabs) else "owners"
    used = {w["name"] for w in earlier}
    name, n = base, 2
    while name in used:
        name, n = f"{base}-{n}", n + 1
    return name


def capture(path: Path | None) -> Path:
    procs = _processes()
    claude, copilot = _claude_sessions(), _copilot_locks()
    windows = []
    for i, (members, order) in enumerate(_group_tabs(_terminal_windows()), 1):
        tabs = []
        for r in members:
            tab = {"tty": r["tty"], "window_id": r["window_id"], "title": r["custom_title"]}
            tab.update(_describe_tab(r["tty"], procs, claude, copilot))
            tabs.append(tab)
        windows.append({"window": i, "name": _window_name(tabs, windows), "bounds": members[0]["bounds"],
                        "order": order, "tabs": tabs})
    present = _launchers_present({t.get("launcher", "") for w in windows for t in w["tabs"]})
    for w in windows:
        for t in w["tabs"]:
            if t.get("launcher") and t["launcher"] not in present:
                t["launcher"] = ""
    snap = {"captured_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "host": os.uname().nodename, "windows": windows, "tmux_sessions": _tmux_capture(procs)}
    STORE.mkdir(parents=True, exist_ok=True)
    if path is None:
        path = STORE / (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    path.write_text(json.dumps(snap, indent=2) + "\n")
    latest = STORE / "latest.json"
    if path.parent == STORE:
        tmp = STORE / ".latest.tmp"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(path.name)
        tmp.replace(latest)
    return path


# ── restore ──────────────────────────────────────────────────────────────────

def _current_tabs() -> list[dict]:
    """Every Terminal tab open now, described the way --capture describes it."""
    procs = _processes()
    claude, copilot = _claude_sessions(), _copilot_locks()
    return [dict(r, **_describe_tab(r["tty"], procs, claude, copilot)) for r in _terminal_windows()]


def _match_windows(windows: list[dict], current: list[dict]) -> list[list[int | None]]:
    """Per captured window, per tab, the Terminal window id of the open tab that
    is it, or None. Each open tab matches at most once, across all windows, and
    the surest evidence goes first so a loose match can never take a tab that
    another captured tab owns outright:

      1. an agent tab holds the same session id;
      2. the same window id and tty: a tab that has been open since the capture;
      3. a tab without a session id (a shell, an agent whose id was not found)
         matches an open tab of the same kind in the same cwd, preferring one
         running the same command, then an idle one, then the same tty. This is
         what finds a tab Terminal reopened after a reboot.
    """
    found = [[None] * len(w["tabs"]) for w in windows]
    taken: set[int] = set()
    every = [(wi, i, t) for wi, w in enumerate(windows) for i, t in enumerate(w["tabs"])]

    def claim(wi: int, i: int, row: dict) -> None:
        found[wi][i] = row["window_id"]
        taken.add(row["window_id"])

    for wi, i, t in every:
        if t.get("session_id"):
            row = next((r for r in current if r["window_id"] not in taken
                        and r["kind"] == t["kind"] and r.get("session_id") == t["session_id"]), None)
            if row:
                claim(wi, i, row)
    for wi, i, t in every:
        if not t.get("session_id") and found[wi][i] is None:
            row = next((r for r in current if r["window_id"] not in taken and r["kind"] == t["kind"]
                        and not r.get("session_id") and r["window_id"] == t.get("window_id")
                        and r["tty"] == t.get("tty")), None)
            if row:
                claim(wi, i, row)
    for wi, i, t in every:
        if t.get("session_id") or not t.get("cwd") or found[wi][i] is not None:
            continue
        hits = [r for r in current if r["window_id"] not in taken and r["kind"] == t["kind"]
                and not r.get("session_id") and r.get("cwd") == t["cwd"]]
        hits.sort(key=lambda r: (r.get("foreground") != t.get("foreground"), bool(r.get("foreground")),
                                 r["tty"] != t.get("tty")))
        if hits:
            claim(wi, i, hits[0])
    return found


def _reruns(tabs: list[dict], opened: list[int | None], current: list[dict]) -> frozenset[int]:
    """Captured shell tabs whose command stopped: the tab is open but idle (a
    reboot reopens shell tabs without what ran in them). Restore types the
    command into that tab instead of opening another."""
    by_id = {r["window_id"]: r for r in current}
    return frozenset(
        i for i, (t, wid) in enumerate(zip(tabs, opened))
        if wid is not None and t["kind"] == "shell" and _replayable(t.get("foreground", ""))
        and by_id[wid].get("foreground") != t.get("foreground"))


def _tab_command(tab: dict) -> str:
    q = shlex.quote
    cwd, sid, launcher = tab.get("cwd", ""), tab.get("session_id", ""), tab.get("launcher", "")
    kind = tab.get("kind")
    extra = "".join(" " + q(a) for a in tab.get("args") or [])
    if kind == "claude" and sid:
        tail = f"{extra} --resume {q(sid)}"
        return f"{launcher}{tail}" if launcher else f"cd {q(cwd)} && claude{tail}"
    if kind == "copilot":
        tail = extra + (f" --resume={q(sid)}" if sid else "")
        return f"{launcher}{tail}" if launcher else f"cd {q(cwd)} && copilot{tail}"
    if kind == "opencode":
        # -s pins the exact session; --continue (latest session in the project
        # directory) is the fallback when the id could not be found.
        tail = extra + (f" -s {q(sid)}" if sid else " --continue")
        return f"{launcher}{tail}" if launcher else f"cd {q(cwd)} && opencode {q(cwd)}{tail}"
    cmd = f"cd {q(cwd)}" if cwd else "cd"
    if _replayable(tab.get("foreground", "")):
        cmd += f" && {tab['foreground']}"
    return cmd


# Updated once, right before the first tab of that kind opens. A kind with no
# tab to open is never updated.
UPDATERS = {
    "claude": ["claude", "--update"],
    "copilot": ["brew", "upgrade", "copilot-cli"],
}
BREW_FALLBACK = "/opt/homebrew/bin/brew"


def _update_before(tab: dict, done: set[str], dry_run: bool) -> None:
    """Run the tab kind's updater the first time a tab of that kind is about to
    open. A failure is reported and the restore goes on: a stale binary still
    resumes its session."""
    kind = tab["kind"]
    if kind not in UPDATERS or kind in done:
        return
    done.add(kind)
    cmd = list(UPDATERS[kind])
    exe = shutil.which(cmd[0]) or (BREW_FALLBACK if cmd[0] == "brew" and Path(BREW_FALLBACK).exists() else "")
    print(c("   would run " if dry_run else "   running ", "dim") + c(" ".join(cmd), "dim")
          + c(f"  (before the first {kind} tab)", "dim"))
    if dry_run:
        return
    if not exe:
        print(c("   ✗ ", "red") + f"{cmd[0]} not found; {kind} tabs open without an update", file=sys.stderr)
        return
    try:
        tail = _run([exe] + cmd[1:], timeout=600).strip().splitlines()[-1:]
        print(c("   ✓ ", "green") + f"{kind} updated" + (c(f"  {tail[0][:100]}", "dim") if tail else ""))
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(c("   ✗ ", "red") + f"{kind} update failed, opening anyway: {exc}", file=sys.stderr)


def _run_in_tab(wid: int, cmd: str) -> None:
    """Type cmd into an already-open tab. Addressed by id, so the window is
    not fronted and does not move between desktops."""
    _osascript(f'tell application "Terminal" to do script {_as_str(cmd)} in selected tab of window id {wid}')


def _window_ids() -> set[int]:
    """Terminal sees every tab as a window, so a new tab shows up as a new id."""
    out = _osascript('tell application "Terminal" to get id of every window')
    return {int(x) for x in out.replace(",", " ").split()}


def _new_window(before: set[int]) -> int:
    """The id of the tab that appeared since `before`. Asked for by id, never by
    "front window": right after a new tab opens, Terminal can still report the
    old front window, and the next tab then lands in the wrong group."""
    for _ in range(20):
        new = _window_ids() - before
        if new:
            return new.pop()
        time.sleep(0.25)
    raise RuntimeError("Terminal did not open the new tab")


def _open_tab(cmd: str, after: int | None, bounds: list[int]) -> int:
    """Open cmd in a new tab right after tab `after`, or in a new window when
    None. Returns the new tab's window id, so the next tab can follow it."""
    before = _window_ids()
    if after is None:
        _osascript(f'tell application "Terminal"\nactivate\ndo script {_as_str(cmd)}\nend tell')
        wid = _new_window(before)
        _osascript(f'tell application "Terminal" to set bounds of window id {wid} to '
                   f'{{{", ".join(map(str, bounds))}}}')
        return wid
    # Fronting a tab selects it, and Cmd-T puts the new tab right after it.
    _osascript(f'tell application "Terminal"\nactivate\nset index of window id {after} to 1\nend tell')
    time.sleep(0.5)
    _osascript('tell application "Terminal" to activate\n'
               'tell application "System Events" to keystroke "t" using command down')
    wid = _new_window(before)
    _osascript(f'tell application "Terminal" to do script {_as_str(cmd)} in selected tab of window id {wid}')
    return wid


# ── presentation ─────────────────────────────────────────────────────────────
# One glyph and one color per tab state, shared by --list, --capture, --restore
# and --prune. Color carries the state; names stay uncolored.

_COLOR = False
_SGR = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
LABEL_W = 22


def _set_color(mode: str) -> None:
    global _COLOR
    if mode == "always":
        _COLOR = True
    elif mode == "never":
        _COLOR = False
    else:
        _COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def c(text: str, *styles: str) -> str:
    if not _COLOR or not styles:
        return text
    return f"\033[{';'.join(_SGR[x] for x in styles)}m{text}\033[0m"


def _home(path: str) -> str:
    home = str(HOME)
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _when(stamp: str) -> str:
    return stamp[:16].replace("T", " ") if stamp else "?"


def _name(w: dict) -> str:
    return w.get("name") or f"window-{w['window']}"


def _label(t: dict) -> str:
    raw = t.get("launcher") or t.get("title") or t["kind"]
    raw = re.sub(r"^\W+", "", raw)
    if raw in ("", "Terminal") and t["kind"] == "shell":
        raw = Path(t.get("cwd") or "~").name or "shell"
    return raw if len(raw) <= LABEL_W else raw[: LABEL_W - 1] + "…"


def _state(t: dict, is_open: bool, rerun: bool = False) -> tuple[str, str, str]:
    """(glyph, color, description) for one tab. ↻ resumes a prior agent session;
    ✦ starts something new: a shell, a re-run command, a Copilot with nothing typed yet."""
    sid = t.get("session_id") or ""
    fg = _short_cmd(t.get("foreground") or "")[:40]
    if is_open and rerun:
        return "✦", "yellow", f"open, idle · will run: {fg}"
    if is_open:
        return "●", "green", "open · restore skips it"
    if t["kind"] == "shell":
        where = _home(t.get("cwd") or str(HOME))
        if _replayable(t.get("foreground", "")):
            return "✦", "yellow", f"{where}  will run: {fg}"
        return "✦", "yellow", where
    if sid:
        return "↻", "cyan", f"resume {sid[:8]}"
    if t["kind"] == "opencode":  # no id found: restore passes --continue
        return "↻", "cyan", "continue the latest in its project"
    return "✦", "yellow", "start new · no conversation yet"


def _render_window(w: dict, opened: list[int | None], rerun: frozenset[int] = frozenset()) -> None:
    n = len(w["tabs"])
    note = {"earlier capture": " · tab order from an earlier capture",
            "guessed": " · tab order guessed: --capture again from its desktop"}.get(w.get("order", ""), "")
    print(c("╭─ ", "dim") + c(_name(w), "bold") + c(f" · {n} tab{'s' if n != 1 else ''}", "dim")
          + c(note, "yellow" if w.get("order") == "guessed" else "dim"))
    for i, (t, wid) in enumerate(zip(w["tabs"], opened)):
        glyph, color, desc = _state(t, wid is not None, i in rerun)
        kind = f"{t['kind']:<9}"
        print(c("│  ", "dim") + c(glyph, color) + " " + f"{_label(t):<{LABEL_W}}" + " " + c(kind, "dim")
              + c(desc, color if color != "green" else "dim"))
    print(c("╰─", "dim"))


def _render_tmux(saved: list[dict], note: str) -> None:
    """tmux sessions in a box like a window's, laid out in columns, with the
    same glyphs as tabs: running, or restore starts it new."""
    live = _tmux_sessions()
    state = {n: ("●", "green") for n in live}
    for sess in saved:
        state.setdefault(sess["name"], ("✦", "yellow"))
    names = list(state)
    if not names:
        return
    n = len(names)
    print(c("╭─ ", "dim") + c("tmux", "bold") + c(f" · {len(live)} running of {n} · {note}", "dim"))
    width = max(len(x) for x in names) + 4
    cols = max(1, (shutil.get_terminal_size((100, 24)).columns - 3) // width)
    rows = -(-n // cols)
    for r in range(rows):
        cells = [names[i] for i in range(r, n, rows)]
        print(c("│  ", "dim") + "".join(c(state[x][0] + " ", state[x][1]) + f"{x:<{width - 2}}" for x in cells).rstrip())
    print(c("╰─", "dim"))


def _legend() -> None:
    lines = [(("●", "green", "open or running"), ("↻", "cyan", "restore resumes its session"),
              ("✦", "yellow", "restore starts it new")),
             (("◆", "cyan", "latest snapshot"), ("◇", "dim", "older snapshot"))]
    print(c("╭─ ", "dim") + c("legend", "bold"))
    for line in lines:
        print(c("│  ", "dim") + "  ".join(c(g, col) + " " + c(text, "dim") for g, col, text in line))
    print(c("╰─", "dim"))


def _cheatsheet(names: list[str]) -> None:
    rows = [("terminal-sessions --capture", "save what is open now"),
            ("terminal-sessions --restore", "reopen everything saved"),
            ("terminal-sessions --restore --only " + "|".join(names or ["NAME"]), "restore just that part"),
            ("terminal-sessions --restore --dry-run", "preview, open nothing"),
            ("terminal-sessions --prune", "delete old snapshots (keeps 5)")]
    width = max(len(cmd) for cmd, _ in rows)
    print(c("╭─ ", "dim") + c("commands", "bold"))
    for cmd, what in rows:
        print(c("│  ", "dim") + c(f"{cmd:<{width}}", "bold") + "  " + c(what, "dim"))
    print(c("╰─", "dim"))


def _snapshots() -> list[Path]:
    return sorted((p for p in STORE.glob("*.json") if SNAPSHOT_NAME.fullmatch(p.name)), reverse=True)


def _latest() -> Path | None:
    link = STORE / "latest.json"
    return link.resolve() if link.exists() else None


def _tmux_saved(snap: dict) -> list[dict]:
    return snap.get("tmux_sessions") or []


def _tmux_cmds(sess: dict) -> list[list[str]]:
    """The tmux calls that rebuild one session exactly as it ran: each window
    with its name and directory, started with the command tmux originally ran,
    or for a shell window the command that was typed into it. Agent CLIs
    included, and as a new session: no resume flag is added."""
    name, cmds = sess["name"], []
    for i, w in enumerate(sess["windows"]):
        base = (["new-session", "-d", "-s", name, "-n", w["name"]] if i == 0
                else ["new-window", "-t", f"{name}:", "-n", w["name"]])
        if w.get("cwd"):
            base += ["-c", w["cwd"]]
        cmds.append(["tmux"] + base + (shlex.split(w["start"]) if w.get("start") else []))
        if not w.get("start") and w.get("run"):
            cmds.append(["tmux", "send-keys", "-t", f"{name}:{w['name']}", w["run"], "Enter"])
    return cmds


def _restore_tmux(snap: dict, dry_run: bool) -> None:
    live = set(_tmux_sessions())
    verb = "would start" if dry_run else "starting"
    for sess in _tmux_saved(snap):
        if sess["name"] in live:
            continue
        if sess.get("launcher"):
            print(c(f"   {verb} ", "dim") + c(f"tmux {sess['name']}", "bold")
                  + c(f"  (through {_short_cmd(sess['launcher'])})", "dim"))
            if not dry_run:
                done = subprocess.run(["zsh", "-lc", sess["launcher"]], capture_output=True, text=True, timeout=300)
                tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-4:])
                if done.returncode:
                    print(c("   ✗ ", "red") + f"{sess['name']}: launcher exited {done.returncode}\n{tail}", file=sys.stderr)
                else:
                    print(c("   ✓ ", "green") + c(tail.splitlines()[-1] if tail else "done", "dim"))
            continue
        n = len(sess["windows"])
        print(c(f"   {verb} ", "dim") + c(f"tmux {sess['name']}", "bold") + c(f"  ({n} window{'s' if n != 1 else ''})", "dim"))
        if dry_run:
            continue
        try:
            for cmd in _tmux_cmds(sess):
                _run(cmd)
        except RuntimeError as exc:
            print(c("   ✗ ", "red") + f"{sess['name']}: {exc}", file=sys.stderr)


def _insert_plan(opened: list[int | None]) -> dict[int | None, list[int]]:
    """Missing tab indexes grouped by the open tab each goes after, in
    captured order. That is the nearest earlier open tab, or for tabs ahead of
    every open tab the first open one, since Terminal cannot insert a tab in
    front of the others. Key None means no tab is open: a new window."""
    first = next((k for k, x in enumerate(opened) if x is not None), None)
    plan: dict[int | None, list[int]] = {}
    anchor = first
    for i, x in enumerate(opened):
        if x is not None:
            anchor = i
        else:
            plan.setdefault(anchor, []).append(i)
    return plan


def restore(path: Path, only: list[str], dry_run: bool) -> int:
    snap = json.loads(path.read_text())
    names = [_name(w) for w in snap["windows"]] + ["tmux"]
    unknown = [n for n in only if n not in names]
    if unknown:
        print(c("✗ ", "red") + f"unknown name(s): {', '.join(unknown)}  "
              + c(f"this snapshot has: {', '.join(names)}", "dim"), file=sys.stderr)
        return 2
    current = _current_tabs()
    matched = _match_windows(snap["windows"], current)
    verb = "would open" if dry_run else "opening"
    print(c("◆ ", "cyan") + c(f"{'preview of ' if dry_run else ''}restore", "bold")
          + c(f"  from {path.name} · captured {_when(snap.get('captured_at', ''))}", "dim"))
    if not only or "tmux" in only:
        _restore_tmux(snap, dry_run)
    updated: set[str] = set()
    for w, opened in zip(snap["windows"], matched):
        if only and _name(w) not in only:
            continue
        rerun = _reruns(w["tabs"], opened, current)
        _render_window(w, opened, rerun)
        for i in sorted(rerun):
            fg = w["tabs"][i]["foreground"]
            print(c(f"   {verb} ", "dim") + c(fg, "dim") + c("  (in its open tab)", "dim"))
            if not dry_run:
                _run_in_tab(opened[i], fg)
        if all(x is not None for x in opened):
            if not rerun:
                print(c("   nothing to open: every tab is already open", "dim"))
            continue
        for anchor, run in _insert_plan(opened).items():
            where = "new window" if anchor is None else "after " + _label(w["tabs"][anchor])
            for i in run:
                _update_before(w["tabs"][i], updated, dry_run)
                print(c(f"   {verb} ", "dim") + c(_tab_command(w["tabs"][i]), "dim") + c(f"  ({where})", "dim"))
            if dry_run:
                continue
            if anchor is None:
                # A new window: each Cmd-T appends, so open in captured order.
                after = None
                for i in run:
                    after = _open_tab(_tab_command(w["tabs"][i]), after, w["bounds"])
                    time.sleep(0.4)
                continue
            # Cmd-T lands right after the anchor only while the anchor is a tab
            # that was already open. Chained after a tab opened moments ago it
            # appends to the end instead. So open the run back to front, each
            # one right after the anchor.
            for i in reversed(run):
                _open_tab(_tab_command(w["tabs"][i]), opened[anchor], w["bounds"])
                time.sleep(0.4)
    return 0


def list_snapshots(path: Path) -> int:
    snaps, latest = _snapshots(), _latest()
    if not snaps:
        print(c("◇ ", "yellow") + f"no snapshots yet in {_home(str(STORE))}")
        _cheatsheet([])
        return 0
    n = len(snaps)
    print(c("╭─ ", "dim") + c("snapshots", "bold") + c(f" · {n} · {_home(str(STORE))}", "dim"))
    for s in snaps[:10]:
        try:
            when = _when(json.loads(s.read_text()).get("captured_at", ""))
        except (OSError, ValueError):
            when = "unreadable"
        is_latest = s.resolve() == latest
        print(c("│  ", "dim") + (c("◆", "cyan") if is_latest else c("◇", "dim")) + f" {when}  "
              + c(s.name, "dim") + (c("  latest", "cyan") if is_latest else ""))
    if n > 10:
        print(c("│  ", "dim") + c(f"… {n - 10} older", "dim"))
    print(c("╰─", "dim"))
    if not path.exists():
        print(c("✗ ", "red") + f"no snapshot at {path}")
        return 1
    snap = json.loads(path.read_text())
    current = _current_tabs()
    print()
    for w, opened in zip(snap["windows"], _match_windows(snap["windows"], current)):
        _render_window(w, opened, _reruns(w["tabs"], opened, current))
    _render_tmux(_tmux_saved(snap), "restore starts any that are not running")
    print()
    _legend()
    print()
    _cheatsheet([_name(w) for w in snap["windows"]] + ["tmux"])
    return 0


def prune(keep: int, dry_run: bool) -> int:
    if keep < 1:
        print(c("✗ ", "red") + "--keep must be at least 1", file=sys.stderr)
        return 2
    snaps, latest = _snapshots(), _latest()
    doomed = [p for p in snaps[keep:] if p.resolve() != latest]
    for p in doomed:
        if dry_run:
            print(c("  ✗ would remove ", "yellow") + p.name)
        else:
            p.unlink()
            print(c("  ✗ removed ", "red") + p.name)
    tail = "to remove" if dry_run else "removed"
    print(c("◆ ", "cyan") + f"{len(snaps) - len(doomed)} kept, {len(doomed)} {tail}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="show snapshots and windows (the default)")
    mode.add_argument("--capture", action="store_true", help="snapshot Terminal windows and tabs")
    mode.add_argument("--restore", action="store_true", help="rebuild windows and tabs from a snapshot")
    mode.add_argument("--prune", action="store_true", help="delete old snapshots, keeping the newest --keep")
    ap.add_argument("--file", type=Path, help="snapshot path (default: ~/.claude/terminal-sessions/latest.json)")
    ap.add_argument("--only", action="append", default=[], metavar="NAME",
                    help="restore only the named window, e.g. workers or owners (repeatable)")
    ap.add_argument("--keep", type=int, default=5, metavar="N", help="snapshots --prune keeps (default 5)")
    ap.add_argument("--dry-run", action="store_true", help="with --restore or --prune: show what would happen")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                    help="color output (auto: only on a terminal, off when NO_COLOR is set)")
    args = ap.parse_args()
    _set_color(args.color)
    print()  # set the output apart from the prompt line above it
    try:
        if args.prune:
            return prune(args.keep, args.dry_run)
        if args.capture:
            path = capture(args.file)
            snap = json.loads(path.read_text())
            print(c("✓ ", "green") + c("captured", "bold") + c(f"  {_home(str(path))}", "dim"))
            for w in snap["windows"]:
                _render_window(w, [0] * len(w["tabs"]))
            return 0
        path = args.file or STORE / "latest.json"
        if not args.restore:
            if args.only or args.dry_run:
                print(c("✗ ", "red") + "--only goes with --restore; --dry-run with --restore or --prune",
                      file=sys.stderr)
                return 2
            return list_snapshots(path)
        if not path.exists():
            print(c("✗ ", "red") + f"no snapshot at {path}; run --capture first", file=sys.stderr)
            return 1
        return restore(path, args.only, args.dry_run)
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print(c("✗ ", "red") + f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
