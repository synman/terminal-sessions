# Operator's guide

How `terminal-sessions` decides what to capture and what to restore. Read this before you rely on it for a real reboot.

## Concepts

| Term | Meaning |
|---|---|
| Snapshot | One JSON file, `~/.claude/terminal-sessions/<UTC stamp>.json`. `latest.json` is a symlink to the newest. |
| Window | A group of tabs that share one Terminal frame. Terminal's AppleScript shows every tab as its own window, so the tool groups tabs by identical frame, then splits groups that share a frame by what the tab bar lists. |
| Tab kind | `claude`, `copilot`, `opencode`, or `shell`. Found from the processes on the tab's tty. |
| Launcher | A zsh function that starts an agent the way you normally start it. Used when it exists, else the bare binary. |
| Recipe | How one tmux session is rebuilt: per window a name, a directory, and a command. |

## Capture

```sh
terminal-sessions --capture
```

Capture reads, and launches nothing. For each visible Terminal tab it records the tty, window id, title, and what runs on the tab:

- **Claude Code.** The session id comes from `~/.claude/sessions/<pid>.json`.
- **Copilot CLI.** The id comes from `~/.copilot/session-state/*/inuse.<pid>.lock`. A Copilot started with `--resume` writes no lock, so the id comes from its arguments. `COPILOT_HOME` moves the base directory. A session with no content yet has no id, and restore starts a fresh one.
- **OpenCode.** The id comes from `-s` or `--session` in its arguments. Failing that, it is the session in the tab's project directory updated last since the process started, read from OpenCode's SQLite database. With no id found, restore passes `--continue`.
- **Shell.** The tab's directory, plus the foreground command if one is running. The tab that runs the capture is never recorded as running anything.

Options the session was started with are carried by an allowlist, per tool. See [Reference](reference.md#options-carried-across-a-restore). Anything else is not replayed, and the snapshot lists it as `args_not_replayed` so the gap is visible.

Real arguments are read from the kernel, so a multi-word value such as a prompt stays one argument. If that read fails, the tool falls back to splitting the `ps` text.

### Window names

A window is named `workers` when more than half its tabs sit under a `workers/` directory. Otherwise it is `owners`. A repeated name gets `-2`, `-3`. These names are what `--only` takes. To rename one, edit its `"name"` in the snapshot. See [SOP: Snapshot care](sop-snapshot-care.md).

### Tab order

Terminal's AppleScript cannot report tab order. The tool reads the tab bar through System Events, which sees only the **current desktop** (Space).

| `order` in the snapshot | Meaning |
|---|---|
| `tab bar` | Read from the tab bar just now |
| `earlier capture` | The window is on another desktop. Order was copied from the last five snapshots that read it from a bar, matched by directory and title. |
| `guessed` | No bar and no earlier capture. Order follows window creation, which is only an approximation of the tab bar. `--list` warns about it on the window's header line. |

The earlier-capture match uses each tab's directory and title. If the titles have changed since, fewer tabs match, and the window falls back to `guessed`.

With windows on two desktops, run `--capture` once from each desktop. The second run reuses the first run's order for the other window.

### tmux

Capture records every tmux session on the default server, whether or not Terminal shows it. Per session, per window: name, directory, and either the command tmux started it with or, for a plain shell window, the command typed into it. Only a window's first pane is kept. A session with a registered launcher also stores that command. See [SOP: tmux launcher](sop-tmux-launcher.md).

## List

```sh
terminal-sessions                   # or --list
terminal-sessions --list --file ~/.claude/terminal-sessions/20261004T175722Z.json
```

Shows up to ten snapshots, newest first, then each window of the chosen snapshot, a tmux box, a legend, and a command cheat sheet.

| Glyph | Meaning |
|---|---|
| `●` green | The tab is open, or the tmux session is running. Restore skips it. |
| `↻` cyan | Restore resumes an agent session |
| `✦` yellow | Restore starts something new: a shell, a re-run command, a Copilot with no conversation yet |
| `◆` / `◇` | Latest snapshot / an older one |

A shell tab that is open but idle shows `✦ open, idle · will run: <command>`. That is the state after a reboot: Terminal reopens the tab, but not what ran in it.

## Restore

```sh
terminal-sessions --restore --dry-run     # always preview first
terminal-sessions --restore
terminal-sessions --restore --only owners --only tmux
```

An unknown `--only` name is refused with exit code `2`, and the names in the snapshot are listed.

### What restore skips

A captured tab counts as open when:

1. An agent tab: some open tab of the same kind holds the same session id.
2. Any tab with no id: the same window id and tty as at capture, so a tab that never closed.
3. A shell, or an agent with no id: an open tab of the same kind in the same directory, preferring one running the same command, then an idle one, then the same tty. This finds the tab Terminal reopened after a reboot.

Each open tab matches at most once, across all windows. The surest evidence is checked first, so a loose match never takes a tab that another captured tab owns outright.

### After a reboot

Terminal reopens its windows as idle shell tabs. Restore treats the two tab types differently:

- **Shell tabs** are matched to those idle tabs by directory. The captured command is **typed into the existing tab**, not into a new one. `--dry-run` prints it as `would open <command>  (in its open tab)`.
- **Agent tabs** match only on a session id, and a reopened shell holds none. Each agent gets a **new** tab, and the idle tab it replaces stays open. Close those idle tabs by hand after the restore.

### Where new tabs go

- If **no** captured tab of a window is open, restore opens one new window at the captured frame, then the tabs in order.
- If **some** are open, each missing tab goes right after the nearest earlier captured tab that is open. A missing tab with no open tab before it goes after the first open one, because Terminal cannot insert at the front.
- Cmd-T puts a tab after the selected one only when that one was already open. So a run of missing tabs is opened back to front, each after the same anchor.

### What each tab runs

| Kind | Command typed into the new tab |
|---|---|
| Claude Code | `<launcher> <opts> --resume <id>`, else `cd <cwd> && claude <opts> --resume <id>` |
| Copilot CLI | `<launcher> <opts> --resume=<id>`, else `cd <cwd> && copilot <opts> --resume=<id>`. With no id, no `--resume`. |
| OpenCode | `<launcher> <opts> -s <id>`, else `cd <cwd> && opencode <cwd> <opts> -s <id>`. With no id, `--continue`. |
| Shell | `cd <cwd>`, then the foreground command if it is replayable |

Capture keeps a launcher name only if `zsh -ic "whence -w <name>"` reports it as a **function**. An alias or a script on `PATH` does not count, and that tab is saved without a launcher, so restore uses the bare binary.

Where do launcher names come from?

- Claude Code: the `name` field of `~/.claude/sessions/<pid>.json`, the session's name (for example one set with `--name`). If a zsh function has that name, restore calls it. Name your launcher function after your sessions, or ignore this and get the bare binary.
- OpenCode: the `ISAAC_OPENCODE_SESSION_NAME` or `ISAAC_OPENCODE_WORKER_NAME` environment variable of the process. These come from the author's own OpenCode launcher setup. If you do not set them, OpenCode tabs restore with the bare `opencode`.
- Copilot: `copilot-<scope>`, found by an optional `scope_detect` module. This repository does not ship one, so Copilot tabs restore with the bare binary. See [Reference](reference.md#optional-scope-lookup).

A shell command is retyped as `ps` shows it, so an argument that held a space loses its quotes. Two things are never re-run: an agent binary, because its launcher resumes it, and a command of 1000 characters or more, because half a command is not what was running.

### Tool updates

Right before the first tab of a kind opens, restore updates that tool, once. Later tabs of the same kind do not repeat it:

| A tab of this kind will open | Restore runs first |
|---|---|
| Claude Code | `claude --update` |
| Copilot CLI | `brew upgrade copilot-cli` |

A kind with no tab to open is not updated. That covers tabs that are already open and windows left out by `--only`. OpenCode and tmux windows are never updated. A failed update prints an error and the restore goes on, since the old binary still resumes its session. `--dry-run` lists the updates and runs none. Restore runs them itself, not in a tab, and waits up to 10 minutes for each.

### tmux restore

For each saved session that is not running:

- With a stored launcher: runs it in a login shell (`zsh -lc`), with a 300 second limit, and prints the last lines of its output. A non-zero exit prints to stderr and is reported, and the restore of other sessions continues.
- Without one: `tmux new-session -d` for the first window and `new-window` for each other, each started with its original command. A shell window gets its command typed in with `send-keys`.

A running session is left alone.

## Permissions

| Need | Why | If missing |
|---|---|---|
| Automation, for the app you run the tool from | `osascript` drives Terminal and System Events | macOS prompts the first time. A denial surfaces as `error: osascript failed (...)`. |
| Accessibility, for the app you run the tool from | Restore sends Cmd-T through System Events. Capture reads the tab bar the same way. | Capture prints `note: tab order unavailable` and uses creation order. Restore fails when it adds a tab. |

"The app you run the tool from" is normally Terminal itself. Grant Accessibility in System Settings, Privacy & Security, Accessibility.

## Desktops (Spaces)

Fronting a window to read its tab bar would move it onto the current desktop instead of switching to it. So capture never does that, and a window on another desktop keeps its earlier order. Restore does front a window when it adds a tab to it. The snapshot records no desktop, so expect a restored window on the current desktop. After a restore, check where each window landed and drag it back if needed.

## Privacy

A snapshot is plain JSON in your home directory, created with your default umask. It holds working directories, session ids, window titles, the machine's node name, and foreground commands exactly as `ps` shows them. A token passed as a command-line argument will be in it. The tool sets no tighter permissions, so consider `chmod 700 ~/.claude/terminal-sessions`. Do not commit, share, or sync the snapshot directory. See [SOP: Snapshot care](sop-snapshot-care.md) for editing or removing entries.

## Limits

- Terminal.app only. No iTerm2, no other emulator.
- Scrollback, shell variables, and running program state are not saved.
- tmux: first pane of each window only, and a rebuilt session is new.
- Windows and tabs are found through AppleScript. A hidden window is skipped.

## Source and upkeep

Described from `terminal-sessions.py` functions: `capture`, `_describe_tab`, `_group_tabs`, `_split_by_bars`, `_order_from_earlier`, `_match_windows`, `_insert_plan`, `_tab_command`, `_update_before`, `_open_tab`, `_restore_tmux`, `_tmux_cmds`, `_launchers_present`, `_window_name`, `_state`, `prune`.

Update this guide when any of them changes behavior, when the `CARRY` or `NOT_CARRIED` tables change, or when the glyph legend changes. Check it with `--list` and `--restore --dry-run` against a real snapshot.
