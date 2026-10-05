# SOP: tmux launcher

Make a tmux session restart through its own script, instead of being rebuilt window by window.

## Scope

tmux sessions on the default tmux server of one Mac. Not for ordinary sessions. The plain recipe rebuilds those exactly as they ran.

## When this runs

A session needs more than its windows to come back right: environment variables, cleanup of old processes, a pane layout or size set by a script. The plain recipe replays none of that. Register a launcher when the session has its own start script.

Do not register one for a session that a bare `tmux new-session` rebuilds correctly.

## Source of truth

`_tmux_launchers`, `_tmux_capture`, `_restore_tmux` in `terminal-sessions.py`. The file is `~/.claude/terminal-sessions/tmux-launchers.json`.

## Preconditions

1. The start script runs from a login zsh shell with no arguments you must supply by hand. Restore runs it as `zsh -lc "<command>"`.
2. The script is safe to run when the session is not running. Restore runs it only for sessions that are not running.
3. The script finishes within 300 seconds and returns to the shell. Restore waits that long and then gives up. A script that ends in `tmux attach` never returns, so it must not.
4. You know the session's exact tmux name: `tmux list-sessions -F '#{session_name}'`.

## Procedure

1. Write the file. The key is the tmux session name, the value is the command:

   ```json
   {
     "svc": "~/bin/start-svc.sh",
     "work": "cd ~/src/work && ./up.sh"
   }
   ```

   Use a full path or a command on the login shell's `PATH`.
2. Check it is valid: `python3 -m json.tool ~/.claude/terminal-sessions/tmux-launchers.json > /dev/null`. Expect no output.
3. Capture:

   ```sh
   terminal-sessions --capture
   ```

   Capture copies each matching command into the snapshot as that session's `"launcher"`. A missing file means no launchers. A file that is not a JSON object of strings stops the capture with `error: ... must map a session name to a command string`.
4. Preview. A session that is running is skipped silently, so stop it first (`tmux kill-session -t svc`):

   ```sh
   terminal-sessions --restore --only tmux --dry-run
   ```

   Expect `would start tmux svc  (through start-svc.sh)`. A command that is not a bare path prints whole, for example `(through cd ~/src/work && ./up.sh)`.

## Verification

Stop the session (`tmux kill-session -t svc`), then run `terminal-sessions --restore --only tmux`. Expect `starting tmux svc  (through start-svc.sh)`, then a green `✓` and the last line of the script's output. Run `tmux ls`. The session is back. Prove the environment is the script's, with a variable the script sets, for example:

```sh
tmux show-environment -g | grep MY_VAR
tmux capture-pane -p -t svc | tail -5
```

Expect the variable and the script's expected output. The first command shows the tmux server's global environment, so it fits a script that sets it there. For a variable set inside a pane, run `echo $MY_VAR` in that pane.

## Failure branches

| Symptom | Do |
|---|---|
| `✗ svc: launcher exited N`, with output | Fix the script. The last four lines of its output are shown. Other sessions still restore. |
| The session came back as plain windows | The snapshot was captured before the launcher was registered. Capture again. |
| `error: ... unreadable` | `tmux-launchers.json` is not valid JSON. Fix it. Capture refuses to fall back to the plain recipe, because that would restart the session without what the script does. |

## Done definition

A stopped session restarts through its script under `--restore --only tmux`, and `tmux ls` shows it.

## Upkeep

Update this SOP when the launcher file shape, the 300 second limit, or the shell used to run it changes. Those live in `_tmux_launchers` and `_restore_tmux`.
