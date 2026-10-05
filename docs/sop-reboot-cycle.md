# SOP: Reboot cycle

Capture your Terminal.app windows before a reboot, and restore them after.

## Scope

One Mac, one user, Terminal.app. Covers Claude Code, Copilot CLI, OpenCode, shell, and tmux tabs.

## When this runs

- Before any planned reboot, logout, or Terminal quit.
- Again after a reboot, once you are logged in and Terminal is open.
- Do not run restore while you are actively working in Terminal. It moves windows and types into tabs.

## Source of truth

`terminal-sessions.py` (`capture`, `restore`). Snapshots in `~/.claude/terminal-sessions/`. Concepts in the [Operator's guide](operators-guide.md).

## Preconditions

1. `terminal-sessions --help` prints usage.
2. Terminal has Accessibility permission. See [Permissions](operators-guide.md#permissions).
3. Windows on more than one desktop? Plan to run capture once from each.
4. You have decided the snapshot is safe to keep. It holds commands and session ids. See [Privacy](operators-guide.md#privacy).

## Procedure: before the reboot

1. Quit nothing yet. Run:

   ```sh
   terminal-sessions --capture
   ```

   Expect `✓ captured  ~/.claude/terminal-sessions/<stamp>.json`, then a box per window with one line per tab.
2. Read each box. A window the tool could not order carries a yellow note, `tab order guessed`. Then check that the agent tabs you want resumed have an id. This should print nothing for them:

   ```sh
   grep -n '"session_id": ""' ~/.claude/terminal-sessions/latest.json
   ```

   A match is an agent with no id. Restore starts a fresh Copilot there, or passes `--continue` to OpenCode. A Copilot with nothing typed yet legitimately has none. The check relies on the tool writing its JSON with two-space indentation.
3. If a window is on another desktop, switch to that desktop and run `--capture` again. Expect that window's note to read `tab order from an earlier capture` or the order to be read fresh.
4. Run `terminal-sessions --list`. Expect every tab marked `●`, because everything is still open.
5. Reboot.

## Procedure: after the reboot

1. Log in. Let Terminal reopen its windows if it does. Do not start your agents by hand.
2. Preview:

   ```sh
   terminal-sessions --restore --dry-run
   ```

   Expect `would open ...` lines. Read each command. Edit the snapshot, or use `--only`, to leave anything out. See [SOP: Snapshot care](sop-snapshot-care.md).
3. Restore. Hands off the keyboard until it prints the last window:

   ```sh
   terminal-sessions --restore
   ```

4. Close the idle shell tabs Terminal reopened beside your restored agent tabs. See [After a reboot](operators-guide.md#after-a-reboot).
5. Restore one part at a time if you prefer (run this in place of step 3):

   ```sh
   terminal-sessions --restore --only tmux
   terminal-sessions --restore --only owners
   ```

## Verification

Run `terminal-sessions --list`. Done means:

- every tab is `●`, except any you chose to leave out;
- each agent tab's session shows the conversation you expect, not an empty one;
- each shell tab is in its directory with its command running;
- `tmux ls` shows each saved session.

## Failure branches

| Symptom | Do |
|---|---|
| `error: osascript failed` | Grant Automation or Accessibility, then rerun. A rerun skips tabs already open. |
| A tab opened in the wrong window | Stop. Close the stray tab. Rerun `--list` to see what is considered open. See [Troubleshooting](troubleshooting.md). |
| An agent started with an empty conversation | Its capture had no session id. Resume it by hand from that tool's own session picker. |
| A shell command ran that you did not want | Press Ctrl-C in that tab. Remove the command from the snapshot's `foreground` before the next restore. |

Restore is safe to repeat. It never reopens a tab it finds open.

## Done definition

`--list` shows no `✦` or `↻` for anything you wanted back, and you have spot-checked one agent tab's history.

## Upkeep

Update this SOP when `restore` changes how it opens tabs, when the output lines change, or when a new tab kind is added. Re-run the procedure against a spare snapshot with `--dry-run` to confirm the expected lines.
