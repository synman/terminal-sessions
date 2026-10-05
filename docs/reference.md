# Reference

Lookup tables for `terminal-sessions`. For how the tool decides things, see the [Operator's guide](operators-guide.md).

## Files and environment

| Path or variable | Role |
|---|---|
| `~/.claude/terminal-sessions/<YYYYMMDD>T<HHMMSS>Z.json` | A snapshot. The stamp is UTC. Only files with this exact name shape are ever listed or pruned. |
| `~/.claude/terminal-sessions/latest.json` | Symlink to the newest snapshot. Updated by `--capture` only when the snapshot lands in this directory. |
| `~/.claude/terminal-sessions/tmux-launchers.json` | Optional, hand-written: `{"<tmux session>": "<command>"}`. Read at capture time. |
| `~/.claude/sessions/<pid>.json` | Read-only. Claude Code's record of a live session. |
| `~/.copilot/session-state/*/` | Read-only. Copilot CLI session state. `COPILOT_HOME` replaces `~/.copilot`. |
| `~/.local/share/opencode/opencode-local.db`, `opencode.db` | Read-only. OpenCode's session database, opened with `mode=ro`. |
| `ISAAC_OPENCODE_SESSION_NAME`, `ISAAC_OPENCODE_WORKER_NAME` | Read from an OpenCode process's environment to name its launcher. |
| `NO_COLOR`, `TERM=dumb` | Turn off color under `--color auto`. |

## Snapshot format

```json
{
  "captured_at": "2026-10-04T17:57:22-04:00",
  "host": "my-mac.local",
  "windows": [
    {
      "window": 1,
      "name": "owners",
      "bounds": [100, 80, 1300, 800],
      "order": "tab bar",
      "tabs": [
        {"tty": "ttys003", "window_id": 4021, "title": "build", "kind": "shell",
         "cwd": "/Users/me/src/app", "foreground": "make watch"},
        {"tty": "ttys007", "window_id": 4025, "title": "agent", "kind": "claude",
         "session_id": "0b7c…", "launcher": "", "cwd": "/Users/me/src/app",
         "args": ["--model", "opus"], "args_not_replayed": ["--debug"]}
      ]
    }
  ],
  "tmux_sessions": [
    {"name": "svc", "launcher": "~/bin/start-svc.sh",
     "windows": [{"name": "main", "cwd": "/Users/me/svc", "start": "", "run": "tail -f log"}]}
  ]
}
```

### Top level

| Key | Meaning |
|---|---|
| `captured_at` | Local time with offset |
| `host` | The machine's node name |
| `windows` | Terminal tab groups |
| `tmux_sessions` | tmux recipes. Empty when `tmux` is missing or nothing runs. |

### Window

| Key | Meaning |
|---|---|
| `window` | 1-based position, ordered by the lowest window id in the group |
| `name` | `owners`, `workers`, or either with `-2`, `-3`. Edit to rename. `--only` matches it. |
| `bounds` | `[left, top, right, bottom]`. Restore applies it to a new window. |
| `order` | `tab bar`, `earlier capture`, or `guessed` |
| `tabs` | In tab order |

### Tab

| Key | Kinds | Meaning |
|---|---|---|
| `tty`, `window_id`, `title` | all | Where the tab was. Used for matching and for the label shown. |
| `kind` | all | `claude`, `copilot`, `opencode`, `shell` |
| `cwd` | all | Working directory |
| `session_id` | agents | The session to resume. May be empty. |
| `session_id_method` | opencode | `argv` or `db-latest-updated` |
| `launcher` | agents | Zsh function name, or `""` |
| `args` | agents | Options replayed on restore |
| `args_not_replayed` | agents | Flags seen and left out, so the gap shows |
| `foreground` | shell | The command running when captured, cut at 1000 characters. Empty if idle. |

### tmux session

| Key | Meaning |
|---|---|
| `name` | Session name |
| `launcher` | Optional command from `tmux-launchers.json`. Restore runs it instead of rebuilding. |
| `windows[].name`, `.cwd` | Window name and directory |
| `windows[].start` | The command tmux started the window with. Empty for a plain shell window. |
| `windows[].run` | For a plain shell window, the command typed into it |

## Options carried across a restore

A flag with a value is carried as `flag value` or `flag=value`.

| Tool | Carried | Never replayed |
|---|---|---|
| Claude Code | `--model`, `--effort`, `--advisor`, `--permission-mode`, `--agent`, `--fallback-model` (with value); `--dangerously-skip-permissions`, `--verbose` (no value) | `--dangerously-load-development-channels`, `--name`, `-n`, `--session-id`, `--resume`, `-r`, `--continue`, `-c`, `--fork-session` |
| Copilot CLI | `--model`, `--agent`, `-i`, `--interactive` (with value); `--yolo`, `--allow-all`, `--allow-all-tools`, `--allow-all-paths`, `--autopilot` | `--additional-mcp-config`, `--experimental`, `--no-experimental`, `--prompt`, `-p`, `--resume`, `--continue`, `--available-tools`, `--excluded-tools` |
| OpenCode | `--model`, `-m`, `--agent`, `--variant` | `-s`, `--session`, `-c`, `--continue`, `--cwd`, `--conditions` |

Any other flag is dropped and recorded under `args_not_replayed`. The table is an allowlist on purpose: a prompt or an unknown flag replayed blindly would be wrong.

## Optional scope lookup

The script tries `from scope_detect import detect_scope` from its own directory. Without that module, every Copilot tab is restored with the bare `copilot` binary, and nothing else changes. To supply one, provide `detect_scope(cwd, registry_path)` returning a tuple whose first item is a scope name. A Copilot tab in a directory with scope `work` then restores through a `copilot-work` zsh function, if one exists. `registry_path` is a `.workspace-paths` file one directory above the script, passed through untouched.

## Exit codes

| Code | Cause |
|---|---|
| `0` | Success |
| `1` | A capture or restore error, a missing snapshot, an unreadable `tmux-launchers.json`, an `osascript` failure |
| `2` | Usage error: `--only` without `--restore`, `--dry-run` without `--restore` or `--prune`, `--keep` below 1, an unknown `--only` name, or an argparse error |

## Source and upkeep

Anchored to `terminal-sessions.py`: `CARRY`, `NOT_CARRIED`, `capture` (the snapshot dict), `_describe_tab`, `_tmux_capture`, `_tmux_launchers`, `main` (exit codes). Update the matching table when any of them changes. A quick check: capture, then compare a snapshot's keys with the tables above.
