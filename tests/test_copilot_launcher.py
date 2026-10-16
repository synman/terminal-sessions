"""A Copilot tab must restore through its copilot-<scope> launcher.

The launcher sets the session's name for the tools around it; the bare
binary does not. After the script moved out of the isaac clone, the scope
lookup looked beside the script, failed to import in silence, and every
Copilot tab was saved and restored bare.
"""
import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "terminal-sessions.py"


def _load():
    spec = importlib.util.spec_from_file_location("terminal_sessions", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ts = _load()


def _fake_isaac(root: Path) -> Path:
    (root / "services").mkdir(parents=True)
    (root / "services" / "scope_detect.py").write_text(
        "def detect_scope(cwd, ws):\n    return ('isaac' if cwd.endswith('isaac') else 'workspace', '', '', True)\n")
    return root


def test_isaac_root_comes_from_the_env(tmp_path, monkeypatch):
    root = _fake_isaac(tmp_path / "isaac")
    monkeypatch.setenv("ISAAC_ROOT", str(root))
    monkeypatch.setattr(ts, "HOME", tmp_path / "home")
    assert ts._isaac_root() == root


def test_isaac_root_comes_from_the_rules_link(tmp_path, monkeypatch):
    root = _fake_isaac(tmp_path / "isaac")
    (root / "rules").mkdir()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "rules").symlink_to(root / "rules")
    monkeypatch.delenv("ISAAC_ROOT", raising=False)
    monkeypatch.setattr(ts, "HOME", home)
    assert ts._isaac_root() == root.resolve()


def test_no_isaac_clone_is_none_not_the_script_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("ISAAC_ROOT", raising=False)
    monkeypatch.setattr(ts, "HOME", tmp_path / "home")
    assert ts._isaac_root() is None


def test_a_missing_lookup_is_said_once(monkeypatch, capsys):
    monkeypatch.setattr(ts, "detect_scope", None)
    monkeypatch.setattr(ts, "_scope_miss_told", False)
    assert ts._scope("/a") == "" and ts._scope("/b") == ""
    assert capsys.readouterr().err.count("bare copilot binary") == 1


def test_the_live_clone_names_the_isaac_launcher():
    """On a node with isaac installed, the isaac root itself scopes to isaac."""
    if ts.ISAAC_ROOT is None:
        pytest.skip("no isaac clone on this node")
    assert ts.detect_scope is not None
    assert ts._scope(str(ts.ISAAC_ROOT)) == "isaac"


def _snapshot(tmp_path):
    tabs = [
        {"tty": "t1", "window_id": 1, "title": "x", "kind": "copilot", "session_id": "abc",
         "launcher": "", "cwd": "/w/isaac", "args": ["--yolo"]},
        {"tty": "t2", "window_id": 2, "title": "y", "kind": "copilot", "session_id": "def",
         "launcher": "", "cwd": "/w/other", "args": []},
        {"tty": "t3", "window_id": 3, "title": "z", "kind": "copilot", "session_id": "ghi",
         "launcher": "copilot-kept", "cwd": "/w/isaac", "args": []},
    ]
    snap = {"captured_at": "2026-10-10T00:44:25-04:00", "host": "h",
            "windows": [{"window": 1, "name": "owners", "bounds": [0, 0, 1, 1], "order": "front", "tabs": tabs}],
            "tmux_sessions": []}
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(snap))
    return path


def _stub_scope(monkeypatch, present):
    monkeypatch.setattr(ts, "_scope", lambda cwd: "isaac" if cwd.endswith("isaac") else "other")
    monkeypatch.setattr(ts, "_launchers_present", lambda names: set(names) & present)


def test_restore_fills_a_bare_copilot_tab_from_its_directory(tmp_path, monkeypatch, capsys):
    """Through restore() itself, so an old snapshot saved bare is repaired on the way out."""
    _stub_scope(monkeypatch, {"copilot-isaac"})
    monkeypatch.setattr(ts, "_current_tabs", lambda: [])
    monkeypatch.setattr(ts, "_match_windows", lambda ws, cur: [[None] * len(w["tabs"]) for w in ws])
    assert ts.restore(_snapshot(tmp_path), ["owners"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert "copilot-isaac --yolo --resume=abc" in out
    # No copilot-other function exists, so that tab keeps the bare binary.
    assert "cd /w/other && copilot --resume=def" in out
    # A launcher the snapshot already names is left alone.
    assert "copilot-kept --resume=ghi" in out
