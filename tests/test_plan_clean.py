"""Safety tests for plan + clean. These prove the guarantees that make the
product trustworthy, all on temp dirs — never touching real data."""
from pathlib import Path

from pyhygiene import clean as C
from pyhygiene import plan as P


def make_report(**kw):
    base = {"roots": [], "interpreters": [], "venvs": [], "project_markers": [],
            "automation": {}, "user_site_packages": []}
    base.update(kw)
    return base


def _user_pkg_candidate(path):
    return {"id": 1, "category": "user_packages", "path": str(path), "owner": "user",
            "risk": "medium", "reason": "r", "size_kb": 1, "size_h": "1K",
            "action": {"type": "rmtree", "target": str(path)}}


def _plan(cands):
    return {"candidates": cands, "total_reclaim_h": "1K", "total_reclaim_kb": 1}


# ---- protected set ----------------------------------------------------------
def test_protected_includes_automation_venv_and_its_base(tmp_path):
    venv = tmp_path / "proj" / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("")
    base = tmp_path / ".pyenv" / "versions" / "3.11.12" / "bin"
    base.mkdir(parents=True)
    report = make_report(
        venvs=[{"path": str(venv), "version": "3.11.12", "base": str(base), "size": "1M"}],
        automation={"crontab": [str(venv / "bin" / "python")]},
    )
    prot = P.compute_protected(report)
    assert P.is_protected(str(venv), prot)          # the venv itself
    assert P.is_protected(str(base.parent), prot)   # its interpreter version dir


def test_broken_venv_is_candidate_but_protected_one_is_not(tmp_path):
    broken = tmp_path / "old" / ".venv"
    broken.mkdir(parents=True)
    live = tmp_path / "live" / "venv"
    (live / "bin").mkdir(parents=True)
    (live / "bin" / "python").write_text("")
    report = make_report(
        venvs=[
            {"path": str(broken), "version": "3.10", "base": "/nonexistent/bin", "size": "1M"},
            {"path": str(live), "version": "3.11", "base": "/nonexistent/bin", "size": "1M"},
        ],
        automation={"crontab": [str(live / "bin" / "python")]},
    )
    cats = {(c["category"], c["path"]) for c in P.build_plan(report)["candidates"]}
    assert ("broken_venv", str(broken)) in cats
    assert ("broken_venv", str(live)) not in cats  # automation names it → protected


# ---- clean: never deletes without apply ------------------------------------
def test_dry_run_does_not_delete(tmp_path):
    d = tmp_path / "victim"
    d.mkdir()
    res = C.execute(_plan([_user_pkg_candidate(d)]), {1}, make_report(), apply=False)
    assert res["dry_run"] and d.exists()


def test_apply_deletes_and_writes_backup_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HOME", tmp_path)  # keep backups out of real HOME
    d = tmp_path / "victim"
    # Real layout: dist-info lives in a NESTED site-packages/, not at the top
    # level. (Regression: a top-level-only glob produced an empty manifest.)
    (d / "site-packages" / "requests-2.0.dist-info").mkdir(parents=True)
    (d / "site-packages" / "urllib3-2.1.0.dist-info").mkdir(parents=True)
    res = C.execute(_plan([_user_pkg_candidate(d)]), {1}, make_report(), apply=True)
    assert res["applied"] and not d.exists()
    bk = Path(res["backup"])
    assert (bk / "REMOVED.tsv").exists()
    manifest = (bk / "victim_packages.txt").read_text()
    assert "requests==2.0" in manifest      # captured from nested site-packages
    assert "urllib3==2.1.0" in manifest      # reinstallable list, pre-delete


def test_root_target_is_handoff_never_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HOME", tmp_path)
    target = tmp_path / "Frameworks" / "Python.framework"
    target.mkdir(parents=True)
    cand = {"id": 1, "category": "redundant_interpreter", "path": str(target),
            "owner": "root", "risk": "medium", "reason": "r", "size_kb": 1,
            "size_h": "17M", "action": {"type": "handoff", "target": str(target)}}
    res = C.execute(_plan([cand]), {1}, make_report(), apply=True, include_interpreters=True)
    r0 = res["results"][0]
    assert r0["status"] == "handoff" and target.exists()  # NOT auto-deleted
    assert Path(r0["script"]).exists()


def test_apply_refuses_item_that_became_protected(tmp_path):
    live = tmp_path / "live" / "venv"
    (live / "bin").mkdir(parents=True)
    (live / "bin" / "python").write_text("")
    stale_plan = _plan([{"id": 1, "category": "broken_venv", "path": str(live),
                         "owner": "user", "risk": "low", "reason": "r", "size_kb": 1,
                         "size_h": "1K", "action": {"type": "rmtree", "target": str(live)}}])
    fresh = make_report(
        venvs=[{"path": str(live), "version": "3.11", "base": "/x/bin", "size": "1M"}],
        automation={"crontab": [str(live / "bin" / "python")]},
    )
    res = C.execute(stale_plan, {1}, fresh, apply=True)
    assert live.exists()  # re-validation saved it
    assert any(x["id"] == 1 for x in res["refused"])


def test_interpreter_removal_requires_optin(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HOME", tmp_path)
    d = tmp_path / "py311"
    d.mkdir()
    cand = {"id": 1, "category": "redundant_interpreter", "path": str(d), "owner": "user",
            "risk": "medium", "reason": "r", "size_kb": 1, "size_h": "1M",
            "action": {"type": "command", "command": ["true"]}}
    res = C.execute(_plan([cand]), {1}, make_report(), apply=True, include_interpreters=False)
    assert d.exists() and any(x["id"] == 1 for x in res["refused"])
