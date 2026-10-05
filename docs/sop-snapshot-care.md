# SOP: Snapshot care

Prune old snapshots, edit one before a restore, and recover from a capture that went wrong.

## Scope

The snapshot directory `~/.claude/terminal-sessions/` on one Mac. Three tasks, each with its own procedure below.

## When this runs

| Task | Trigger |
|---|---|
| Prune | The directory has grown, or you want old command history gone |
| Edit | A restore preview shows something you do not want, or you want a window renamed |
| Recover | A capture merged windows, lost tabs, or recorded the wrong order |

## Source of truth

`prune`, `_snapshots`, `_latest`, `restore` in `terminal-sessions.py`. File layout in the [Reference](reference.md#snapshot-format).

## Preconditions

- Snapshots are plain JSON, so any editor works. Copy the stamped file before you edit it, not `latest.json`, which is only a symlink.
- `terminal-sessions --list` shows which file is latest.

## Procedure: prune

1. Preview:

   ```sh
   terminal-sessions --prune --keep 5 --dry-run
   ```

   Expect `✗ would remove <stamp>.json` lines and a count, `N kept, M to remove`.
2. Run it:

   ```sh
   terminal-sessions --prune --keep 5
   ```

   Only files named `<8 digits>T<6 digits>Z.json` are touched. The snapshot `latest.json` points at always stays, even outside the newest N.
3. `--keep` must be 1 or more. Zero is refused with exit code `2`.

## Procedure: edit a snapshot

1. Find the file: `terminal-sessions --list` marks the latest with `◆`. Copy it first:

   ```sh
   cp ~/.claude/terminal-sessions/<stamp>.json ~/.claude/terminal-sessions/<stamp>.json.bak
   ```

   A `.bak` name does not match the snapshot pattern, so it is never listed or pruned. Delete it yourself when done.
2. Edit the stamped file. Common edits:
   - Rename a window: change its `"name"`. That is the name `--only` takes.
   - Skip a tab: delete its object from `"tabs"`.
   - Stop a shell command being re-run: set that tab's `"foreground"` to `""`.
   - Skip a tmux session: delete it from `"tmux_sessions"`.
3. Check the result is valid JSON. Expect no output:

   ```sh
   python3 -m json.tool ~/.claude/terminal-sessions/<stamp>.json > /dev/null
   ```

4. Preview the file you edited. Without `--file`, restore reads `latest.json`, which may be a different snapshot:

   ```sh
   terminal-sessions --restore --dry-run --file ~/.claude/terminal-sessions/<stamp>.json
   ```

   Expect only what you kept. Restore for real with the same `--file`.

## Procedure: recover from a bad capture

A capture is a read of the screen. It is safe to discard.

1. Look at what went wrong: `terminal-sessions --list --file ~/.claude/terminal-sessions/<stamp>.json`.
2. Delete the bad file:

   ```sh
   rm ~/.claude/terminal-sessions/<stamp>.json
   ```

   If it was the latest, `latest.json` now dangles. Point it at the previous good one:

   ```sh
   ln -sfn <good-stamp>.json ~/.claude/terminal-sessions/latest.json
   ```

   Or skip the link and capture again. A new capture rewrites `latest.json`.
3. Capture again, from each desktop that holds a window. Windows that share one frame are split by what the tab bar lists, so a second capture from the right desktop usually fixes a merge.
4. Verify with `--list`. Every window should show the tabs you expect and, for any window not on the current desktop, a note naming its order source.

## Verification

- After prune: `terminal-sessions --list` shows the expected count in its header and still marks a latest.
- After edit or recover: `--restore --dry-run`, with `--file` if you edited a snapshot that is not the latest, lists exactly the tabs and commands you intend.

## Failure branches

| Symptom | Do |
|---|---|
| `--list` says `no snapshot at ...` | `latest.json` dangles. Relink as above, or capture. |
| `error: ... unreadable` or a JSON error | The file is not valid JSON. Restore your `.bak` or the previous snapshot. |
| `--only` is refused | The name is not in the snapshot. The message lists the names that are. |

## Done definition

The snapshot you will restore from is the one you checked with `--restore --dry-run` (same `--file`), and no `.bak` files are left behind.

## Upkeep

Update this SOP when the snapshot filename pattern, the `prune` rules, or the snapshot keys change.
