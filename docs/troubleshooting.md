# Troubleshooting

Find the line you saw, then follow the fix. A restore is safe to repeat: it skips tabs it finds open.

## Messages

| You see | Cause | Fix |
|---|---|---|
| `note: tab order unavailable (...); using window creation order` | System Events could not read the tab bar. Usually Accessibility is not granted, or the windows are on another desktop. | Grant Accessibility to the app you run the tool from (normally Terminal). Run `--capture` from the desktop that holds the window. |
| `tab order guessed: --capture again from its desktop` (on a window's header) | No tab bar was readable and no earlier capture covered this window | Switch to that window's desktop and capture again |
| `error: osascript failed (...)` | macOS refused an AppleScript call. Usually a missing Automation or Accessibility grant. | System Settings, Privacy & Security. Allow the app you run the tool from to control Terminal and System Events. |
| `error: Terminal did not open the new tab` | A new tab did not appear within five seconds | Make sure Terminal is running and not at a modal prompt. Rerun restore. |
| `no snapshot at ...; run --capture first` | No file at `latest.json` or at the `--file` path | Capture, or point `--file` at a real snapshot |
| `unknown name(s): ...  this snapshot has: ...` | `--only` names a window the snapshot does not have | Use a name from the list it prints. Window names live in the snapshot. |
| `--only goes with --restore; --dry-run with --restore or --prune` | A flag used with the wrong mode | Add `--restore`, or drop the flag |
| `error: ... tmux-launchers.json is unreadable` or `must map a session name to a command string` | The launcher file is not a JSON object of strings | Fix the file. See [SOP: tmux launcher](sop-tmux-launcher.md). |
| `✗ <session>: launcher exited N` | A tmux launcher command failed | Run its command by hand and fix it |

## Surprising results

**Restore ran an update I did not expect.** Restore runs `claude --update` or `brew upgrade copilot-cli` once, right before the first tab of that kind opens. Use `--dry-run` to see it first, or `--only` to leave a window out. See [Tool updates](operators-guide.md#tool-updates).

**Two windows were merged into one.** Terminal windows that share the same frame are one group by bounds. The tool splits them by what the tab bar lists. If a window sits on another desktop, its bar is unreadable and it can stay merged. Capture again from each desktop. Delete the bad snapshot. See [SOP: Snapshot care](sop-snapshot-care.md).

**Windows ended up on one desktop after a restore.** Adding a tab to an open window fronts that window, and Terminal moves a fronted window onto the current desktop. Restore the windows from the desktop where you want them, one at a time with `--only`.

**A tab shows `✦ ... start new · no conversation yet` for an agent you expected to resume.** Its capture had no session id. Copilot with nothing typed yet has none. Check with `grep -n '"session_id": ""' ~/.claude/terminal-sessions/latest.json`.

**An agent started with a different model or flag.** Only an allowlist of options is carried. The rest are in `args_not_replayed`. See [Reference](reference.md#options-carried-across-a-restore).

**A tab restored as the bare binary, not through my launcher.** Capture keeps a launcher only when it is a zsh function that an interactive zsh sees. An alias or a script on `PATH` does not count. Define it as a function in your `~/.zshrc`, then capture again.

**Extra idle shell tabs are left after a restore.** Terminal reopened them, and the agent tabs opened as new tabs beside them. Close the idle ones. See [After a reboot](operators-guide.md#after-a-reboot).

**A shell command I did not expect ran.** Restore retypes the foreground command of a shell tab. Empty its `"foreground"` in the snapshot, or leave that window out with `--only`.

**A shell command lost its quotes.** Commands are retyped as `ps` shows them, so an argument that held a space is flattened. Edit the snapshot's `"foreground"` to the right form.

**`--list` says a tab is open that I closed.** A shell tab counts as open when any open shell sits in the same directory. Close that other tab, or change the tab's `cwd` in the snapshot.

**Restore did nothing.** Every tab was already open. The output says `nothing to open: every tab is already open`.

## Gather evidence for a bug report

Collect these, and remove anything private first:

1. `terminal-sessions --list --color never`
2. The snapshot, with `cwd`, `foreground`, and `title` values replaced by placeholders.
3. `python3 --version` and `sw_vers`.
4. Which desktops the windows were on.

## Source and upkeep

Messages come from `terminal-sessions.py`: `_tab_bars`, `_new_window`, `_tmux_launchers`, `_restore_tmux`, `restore`, `main`. Update the table when a message changes. To confirm one, grep the source for the exact text in the first column.
