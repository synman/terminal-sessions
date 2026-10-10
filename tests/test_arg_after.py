"""A bare session flag must not read the next option as a session id.

`copilot --resume --yolo` opens the session picker. Read as an id, `--yolo`
became `--resume=--yolo` on restore and the tab failed to start.
"""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "terminal_sessions", Path(__file__).resolve().parent.parent / "terminal-sessions.py")
ts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ts)


def test_a_bare_flag_followed_by_an_option_has_no_value():
    assert ts._arg_after("copilot --resume --yolo", ("--resume",)) == ""
    assert ts._arg_after("opencode -s --model x", ("-s", "--session")) == ""


def test_a_bare_flag_at_the_end_has_no_value():
    assert ts._arg_after("copilot --yolo --resume", ("--resume",)) == ""


def test_a_real_value_is_still_read_in_both_forms():
    assert ts._arg_after("copilot --resume abc-123 --yolo", ("--resume",)) == "abc-123"
    assert ts._arg_after("copilot --resume=abc-123 --yolo", ("--resume",)) == "abc-123"
    assert ts._arg_after("opencode -s ses_9 --model x", ("-s", "--session")) == "ses_9"
