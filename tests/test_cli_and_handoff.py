"""Regression tests for the code-review fixes: handoff-script safety, the CLI
wiring layer, and guard.install resilience."""
from __future__ import annotations

from pathlib import Path

import pytest

import pyhygiene
from pyhygiene import cli
from pyhygiene import clean as C
from pyhygiene import guard as G


def _report(**kw):
    base = {"roots": [], "interpreters": [], "venvs": [], "project_markers": [],
            "automation": {}, "user_site_packages": [], "caches": []}
    base.update(kw)
    return base


def _interp(kind, version, path):
    return {"kind": kind, "label": version, "version": version,
            "path": str(path), "size": "100M", "protected": False}


# ── handoff script never passes the target through shell parsing ──────────────
def test_handoff_does_not_shell_interpolate_the_target(tmp_path):
    # A path with shell metacharacters must never end up executable in the script.
    evil = f"/Library/Frameworks/E$(touch {tmp_path / 'PWNED'}).framework/Versions/3.13"
    item = {"id": 1, "category": "redundant_interpreter", "path": evil,
            "owner": "root", "action": {"type": "handoff", "target": evil}}
    script = C.write_handoff(tmp_path, item)
    body = script.read_text()
    assert "$(touch" not in body                 # the payload is NOT in the script
    assert 'rm -rf -- "$target"' in body         # deletes a variable, not a literal
    assert "$(" not in script.name               # filename is slugified
    # the raw path lives in a sidecar data file, read at runtime via `read`
    sidecars = list(tmp_path.glob("*.target"))
    assert sidecars and evil in sidecars[0].read_text()
    assert "IFS= read -r target <" in body


def test_handoff_symlink_cleanup_is_specific_and_broken_only(tmp_path):
    # Removing python.org "3.9" must not match unrelated symlinks (e.g. Homebrew
    # python@3.9) just because they share the version number.
    item = {"id": 1, "category": "redundant_interpreter",
            "path": "/Library/Frameworks/Python.framework/Versions/3.9",
            "owner": "root",
            "action": {"type": "handoff",
                       "target": "/Library/Frameworks/Python.framework/Versions/3.9"}}
    body = C.write_handoff(tmp_path, item).read_text()
    assert "! -e " in body                        # only NOW-BROKEN links
    assert 'frag="${target#/}"' in body           # matches the full path fragment
    assert '*"3.9"*' not in body                  # never the bare basename


# ── CLI layer: the gates that map flags onto the safety logic ─────────────────
def test_cli_clean_without_apply_never_mutates(tmp_path, monkeypatch, capsys):
    site = tmp_path / ".local/lib/python3.11"
    (site / "site-packages").mkdir(parents=True)
    report = _report(user_site_packages=[{"path": str(site), "size": "1G"}])
    monkeypatch.setattr(cli.audit_mod, "audit", lambda roots=None: report)

    rc = cli.main(["clean"])  # no --apply

    assert rc == 0
    assert site.exists()                          # dry-run deleted nothing
    assert "DRY-RUN" in capsys.readouterr().out


def test_cli_include_interpreters_actually_selects_them(tmp_path, monkeypatch, capsys):
    a = tmp_path / "pyenv/3.10.0"; (a / "bin").mkdir(parents=True)
    b = tmp_path / "brew/3.10.0"; (b / "bin").mkdir(parents=True)
    report = _report(interpreters=[_interp("pyenv", "3.10.0", a),
                                   _interp("homebrew", "3.10.0", b)])
    monkeypatch.setattr(cli.audit_mod, "audit", lambda roots=None: report)

    cli.main(["clean", "--include-interpreters"])  # blanket dry-run

    # regression: the flag used to lift the refusal but select nothing → no-op
    assert "redundant_interpreter" in capsys.readouterr().out


def test_cli_version_flag_prints_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert pyhygiene.__version__ in capsys.readouterr().out


# ── guard.install records failures instead of crashing mid-mutation ───────────
def test_guard_install_survives_readonly_interpreter_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "HOME", tmp_path)
    rc = tmp_path / ".zshrc"
    rc.write_text("# existing\n")
    libdir = tmp_path / ".pyenv/versions/3.11.9/lib/python3.11"
    libdir.mkdir(parents=True)
    libdir.chmod(0o500)  # read-only → marker write must fail gracefully
    try:
        res = G.install(apply=True, rc_path=rc, pyenv_root=tmp_path / ".pyenv")
    finally:
        libdir.chmod(0o700)  # restore so pytest can clean up
    assert any(a["kind"] == "pep668" and a["status"].startswith("failed")
               for a in res["actions"])
    # the shell-rc block still got written despite the later marker failure
    assert G.SENTINEL in rc.read_text()
