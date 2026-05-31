"""Tests for guard install — the key property is idempotency (safe to re-run)."""
from pyhygiene import guard as G


def test_install_adds_missing_then_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "HOME", tmp_path)
    rc = tmp_path / ".zshrc"
    rc.write_text("# existing user config\n")
    pyenv = tmp_path / ".pyenv"
    (pyenv / "versions" / "3.11.9" / "lib" / "python3.11").mkdir(parents=True)

    # First run adds both the shell block and the PEP 668 marker.
    r1 = G.install(apply=True, rc_path=rc, pyenv_root=pyenv)
    s1 = {(a["kind"], a["status"]) for a in r1["actions"]}
    assert ("shell-rc", "added") in s1
    assert ("pep668", "added") in s1
    assert G.SENTINEL in rc.read_text()
    assert (pyenv / "versions/3.11.9/lib/python3.11/EXTERNALLY-MANAGED").exists()

    # Second run changes nothing — everything is already present.
    r2 = G.install(apply=True, rc_path=rc, pyenv_root=pyenv)
    assert all(a["status"] == "already-present"
               for a in r2["actions"] if a["kind"] in ("shell-rc", "pep668"))
    # crucially, the block is not duplicated
    assert rc.read_text().count(G.SENTINEL) == 1


def test_install_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "HOME", tmp_path)
    rc = tmp_path / ".zshrc"
    rc.write_text("x\n")
    pyenv = tmp_path / ".pyenv"
    (pyenv / "versions" / "3.12.0" / "lib" / "python3.12").mkdir(parents=True)

    r = G.install(apply=False, rc_path=rc, pyenv_root=pyenv)
    assert G.SENTINEL not in rc.read_text()
    assert not (pyenv / "versions/3.12.0/lib/python3.12/EXTERNALLY-MANAGED").exists()
    assert any(a["status"] == "would-add" for a in r["actions"])
