# terminal-sessions

Save your macOS Terminal.app windows, tabs, and agent sessions before a reboot, and rebuild them afterward.

One Python file. Standard library only. No daemon, no install step beyond a symlink.

## What it saves

`--capture` writes a snapshot of every open Terminal window and tab:

| Tab runs | Restore does |
|---|---|
| Claude Code | resumes the same session id |
| GitHub Copilot CLI | resumes the same session id |
| OpenCode | resumes the same session id, or continues the latest one in its project |
| A plain shell | `cd` to its directory, then retypes the command that was running (`ssh`, `make`, `tail`, and so on) |
| tmux sessions | rebuilds each session that is not running, as a new session |

It also saves which tabs were grouped in one window, the order of the tabs, and each window's frame.

Nothing is launched by `--capture`. `--restore` opens only what the snapshot lists, and skips any tab that is already open, so a second restore does not double-launch.

## Requirements

- macOS with Terminal.app (the stock Terminal, not iTerm2 or another emulator)
- Python 3.9 or newer. The macOS system `python3` works.
- `zsh`. Restore checks for launcher functions through `zsh -ic`.
- Accessibility and Automation permission for the app you run it from (normally Terminal), so restore can send Cmd-T and drive Terminal. See [Operator's guide](docs/operators-guide.md#permissions).
- `tmux` is optional. Without it, no tmux sessions are captured.

## Install

```sh
git clone https://github.com/synman/terminal-sessions.git
cd terminal-sessions
mkdir -p ~/bin
ln -sf "$PWD/terminal-sessions.py" ~/bin/terminal-sessions   # ~/bin must be on your PATH
terminal-sessions --help
```

The script is executable and starts with a `python3` shebang, so the link runs it directly. You can also run it in place: `python3 terminal-sessions.py --help`.

## Quick start

```sh
terminal-sessions --capture             # save what is open now
terminal-sessions                       # same as --list: snapshots, windows, what restore would do
terminal-sessions --restore --dry-run   # preview, open nothing
terminal-sessions --restore             # reopen everything saved
```

## Commands

| Command | What it does |
|---|---|
| `--list` (default) | Show snapshots, then each window of the chosen snapshot and what restore would do to each tab |
| `--capture` | Save a snapshot to `~/.claude/terminal-sessions/<UTC stamp>.json` and point `latest.json` at it |
| `--restore` | Rebuild windows and tabs from a snapshot |
| `--restore --only NAME` | Restore just one window, or `tmux`. Repeat the flag for several. |
| `--restore --dry-run` | Print the plan and open nothing |
| `--prune [--keep N]` | Delete old snapshots, keeping the newest N (default 5) and the one `latest.json` points at. `--keep` applies only with `--prune`. |
| `--file PATH` | The snapshot to read for `--list` and `--restore` (default `latest.json`), or to write for `--capture`. `latest.json` is repointed only when the file is inside the snapshot directory. |
| `--color auto\|always\|never` | Color output. `auto` colors only a terminal, and never when `NO_COLOR` is set. |

Exit codes: `0` success, `1` capture or restore error, `2` usage error.

## Know before you restore

- **Restore runs commands.** A shell tab's foreground command is typed back into the tab. Read `--dry-run` first.
- **A snapshot is sensitive.** It holds directories, window titles, session ids, and commands as `ps` shows them. A secret passed on a command line is in there. Keep snapshots private and out of version control.
- **Restore updates tools.** Right before the first Claude Code tab it runs `claude --update`, and before the first Copilot CLI tab `brew upgrade copilot-cli`. A kind with no tab to open is not updated.
- **Restore moves windows.** Adding a tab to an open window brings that window to the front on the current desktop. Do not type while restore runs.
- **Tmux sessions come back new.** No history, no running state, no resume flag.

## Documentation

| Document | Read it to |
|---|---|
| [Operator's guide](docs/operators-guide.md) | Understand how capture and restore decide what to do |
| [Reference](docs/reference.md) | Look up the snapshot format, carried options, and files |
| [SOP: Reboot cycle](docs/sop-reboot-cycle.md) | Capture before a reboot and restore after it |
| [SOP: Snapshot care](docs/sop-snapshot-care.md) | Prune snapshots, rename a window, recover from a bad capture |
| [SOP: tmux launcher](docs/sop-tmux-launcher.md) | Make a tmux session restart through its own script |
| [Troubleshooting](docs/troubleshooting.md) | Fix a failure or a surprising result |

## Contributing

The tool is `terminal-sessions.py`. Its docstring is the help text, so change both together. Each document names the functions it describes at the bottom. When you change one of those functions, update that document in the same commit. Check a change with `python3 terminal-sessions.py --help` and `--restore --dry-run` against a real snapshot.

## License

MIT. See [LICENSE](LICENSE).
