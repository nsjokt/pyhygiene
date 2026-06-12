"""End-to-end scenario suite — simulated 'other people's messy machines'.

Each scenario builds a synthetic machine inventory (a `report`) plus real
fixture directories under a temp path, then drives the REAL decision pipeline
(build_plan → default_selection → clean.execute). Nothing touches the host:
every deletion is an rmtree on a tmp fixture, and destructive system commands
(brew/pyenv uninstall) are never executed — the scenarios assert they're gated.

These double as regression guards: every bug we found in development has a
scenario here so a future change can't silently reintroduce it. See SCENARIOS.md
for the plain-language catalog.
"""
from __future__ import annotations  # PEP 604 `X | Y` annotations on Python 3.9

from pathlib import Path

import pytest

from pyhygiene import clean as C
from pyhygiene import plan as P
from pyhygiene import audit as A


# ── fixture builders ────────────────────────────────────────────────────────
def _venv(path: Path, base: str, version: str = "3.11.9") -> dict:
    (path / "bin").mkdir(parents=True, exist_ok=True)
    (path / "bin" / "python").write_text("")
    (path / "pyvenv.cfg").write_text(f"home = {base}\nversion = {version}\n")
    return {"path": str(path), "version": version, "base": base, "size": "100M"}


def _interp(kind: str, label: str, version: str, path: Path | str, protected=False) -> dict:
    return {"kind": kind, "label": label, "version": version,
            "path": str(path), "size": "100M", "protected": protected}


def _site(path: Path, *dist_infos: str) -> dict:
    sp = path / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    for di in dist_infos:
        (sp / f"{di}.dist-info").mkdir()
    return {"path": str(path), "size": "1G"}


def _cache(path: Path, tool: str, refill: str) -> dict:
    path.mkdir(parents=True, exist_ok=True)
    return {"tool": tool, "path": str(path), "size": "5G", "refill": refill}


def _machine(tmp: Path, **kw) -> dict:
    base = {"roots": [str(tmp)], "interpreters": [], "venvs": [], "project_markers": [],
            "automation": {}, "user_site_packages": [], "caches": []}
    base.update(kw)
    return base


def _cats(plan):
    return {(c["category"], c["path"]) for c in plan["candidates"]}


CHEAP = "cheap (re-downloads wheels)"
PRICEY = "EXPENSIVE — re-downloads models (can be many GB)"


# ── Scenario 1: the ML researcher (huge caches + a cron-driven training venv) ─
def test_scenario_ml_researcher(tmp_path):
    pyenv = tmp_path / "pyenv" / "3.11.9"
    pyenv_bin = pyenv / "bin"
    pyenv_bin.mkdir(parents=True)
    train = _venv(tmp_path / "trainer" / ".venv", str(pyenv_bin))
    report = _machine(
        tmp_path,
        interpreters=[_interp("pyenv", "3.11.9", "3.11.9", pyenv)],
        venvs=[train],
        automation={"crontab": [str(Path(train["path"]) / "bin" / "python")]},
        caches=[_cache(tmp_path / ".cache/huggingface", "huggingface", PRICEY),
                _cache(tmp_path / ".cache/torch", "torch", "expensive — model weights"),
                _cache(tmp_path / ".cache/uv", "uv", CHEAP),
                _cache(tmp_path / "Library/Caches/pip", "pip", CHEAP)],
    )
    plan = P.build_plan(report)
    # the training venv (and its interpreter) must NOT be deletion candidates
    assert ("broken_venv", train["path"]) not in _cats(plan)
    assert not any(c["category"] == "redundant_interpreter" for c in plan["candidates"])
    # caches are surfaced; model caches are medium-risk, wheel caches low
    risk = {c["tool"]: c["risk"] for c in plan["candidates"] if c["category"] == "cache"}
    assert risk["huggingface"] == "medium" and risk["torch"] == "medium"
    assert risk["uv"] == "low" and risk["pip"] == "low"
    # a blanket cleanup takes only the cheap caches — never the model caches
    blanket = clean_targets(plan, C.default_selection(plan))
    assert {"uv", "pip"} == {c["tool"] for c in blanket if c["category"] == "cache"}


def clean_targets(plan, ids):
    return [c for c in plan["candidates"] if c["id"] in ids]


# ── Scenario 2: accidental --user installs (regression: empty backup manifest) ─
def test_scenario_accidental_user_installs(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HOME", tmp_path)  # backups land in the sandbox
    system = _interp("system", "os", "3.9.6", "/usr/bin/python3", protected=True)
    user = _site(tmp_path / ".local/lib/python3.11", "numpy-1.26.4", "requests-2.32.5")
    report = _machine(tmp_path, interpreters=[system], user_site_packages=[user])
    plan = P.build_plan(report)
    assert ("user_packages", user["path"]) in _cats(plan)
    # the OS interpreter is never a candidate
    assert not any(c["path"] == "/usr/bin/python3" for c in plan["candidates"])
    # apply: it's removed AND the reinstallable manifest is non-empty
    res = C.execute(plan, C.default_selection(plan), report, apply=True)
    assert not Path(user["path"]).exists()
    manifest = (Path(res["backup"]) / "python3.11_packages.txt").read_text()
    assert "numpy==1.26.4" in manifest and "requests==2.32.5" in manifest


# ── Scenario 3: duplicate 3.11 (pyenv + homebrew); only pyenv is used ─────────
def test_scenario_duplicate_interpreters(tmp_path):
    pyenv = tmp_path / "pyenv/3.11.9"
    (pyenv / "bin").mkdir(parents=True)
    brew = tmp_path / "homebrew/3.11.9"
    (brew / "bin").mkdir(parents=True)
    v = _venv(tmp_path / "proj/.venv", str(pyenv / "bin"))
    report = _machine(
        tmp_path,
        interpreters=[_interp("pyenv", "3.11.9", "3.11.9", pyenv),
                      _interp("homebrew", "python@3.11", "3.11.9", brew)],
        venvs=[v],
    )
    plan = P.build_plan(report)
    cats = _cats(plan)
    # homebrew copy is redundant (no venv built from it, a sibling 3.11 exists)
    assert ("redundant_interpreter", str(brew)) in cats
    # the pyenv copy IS used by a venv → never a candidate
    assert ("redundant_interpreter", str(pyenv)) not in cats
    # blanket clean must NOT remove an interpreter
    assert plan_candidate(plan, str(brew))["id"] not in C.default_selection(plan)
    # and even if selected, execute refuses without --include-interpreters
    res = C.execute(plan, {plan_candidate(plan, str(brew))["id"]}, report, apply=True)
    assert Path(brew).exists() and res["refused"]


def plan_candidate(plan, path):
    return next(c for c in plan["candidates"] if c["path"] == path)


# ── Scenario 4: launchd-driven bot (protect venv AND its base interpreter) ────
def test_scenario_launchd_bot(tmp_path):
    pyenv = tmp_path / "pyenv/3.11.12"
    pyenv_bin = pyenv / "bin"
    pyenv_bin.mkdir(parents=True)
    bot = _venv(tmp_path / "trading-bot/venv", str(pyenv_bin), "3.11.12")
    report = _machine(
        tmp_path,
        interpreters=[_interp("pyenv", "3.11.12", "3.11.12", pyenv)],
        venvs=[bot],
        automation={str(tmp_path / "LaunchAgents"): [str(Path(bot["path"]) / "bin" / "python")]},
    )
    plan = P.build_plan(report)
    prot = plan["protected"]["paths"]
    assert any(bot["path"] in p for p in prot)         # the venv
    assert any(str(pyenv_bin) in p for p in prot)      # its base interpreter
    assert plan["candidates"] == [] or all(
        bot["path"] not in c["path"] for c in plan["candidates"])


# ── Scenario 5: uv-run wrapper script (resolve venv through WorkingDirectory) ─
def test_scenario_uv_run_wrapper(tmp_path):
    proj = tmp_path / "market-tool"
    (proj / ".venv" / "bin").mkdir(parents=True)
    (proj / ".venv" / "bin" / "python").write_text("")
    wrapper = proj / "scripts" / "run_daily.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text('cd "$PROJECT_ROOT"\nuv run python -m lib.snapshot\n')
    plist = (f"<key>WorkingDirectory</key><string>{proj}</string>"
             f"<string>/bin/bash</string><string>{wrapper}</string>")
    # resolution (what audit.automation_refs does per plist) must find the venv
    resolved = A._runner_venv_refs(A._expand_plist(plist))
    assert str(proj / ".venv" / "bin" / "python") in resolved
    # and once resolved, the venv is protected
    report = _machine(
        tmp_path,
        venvs=[{"path": str(proj / ".venv"), "version": "3.14", "base": "x", "size": "1G"}],
        automation={"launchd": resolved},
    )
    plan = P.build_plan(report)
    assert any(str(proj / ".venv") in p for p in plan["protected"]["paths"])


# ── Scenario 6: a broken venv (its base interpreter is gone) ──────────────────
def test_scenario_broken_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HOME", tmp_path)
    dead = _venv(tmp_path / "old/.venv", "/nonexistent/interpreter/bin")
    report = _machine(tmp_path, venvs=[dead])
    plan = P.build_plan(report)
    assert ("broken_venv", dead["path"]) in _cats(plan)
    C.execute(plan, C.default_selection(plan), report, apply=True)
    assert not Path(dead["path"]).exists()


# ── Scenario 7: root-owned python.org framework + a pasted password ───────────
def test_scenario_root_framework_is_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HOME", tmp_path)
    # owner is derived from a /Library path; two 3.13s make it 'redundant'
    fw = "/Library/Frameworks/Python.framework/Versions/3.13"
    report = _machine(tmp_path, interpreters=[
        _interp("python.org", "3.13", "3.13.7", fw),
        _interp("python.org", "3.13b", "3.13.1", "/Library/Frameworks/PythonT.framework/Versions/3.13"),
    ])
    plan = P.build_plan(report)
    cand = plan_candidate(plan, fw)
    assert cand["owner"] == "root" and cand["action"]["type"] == "handoff"
    # apply with explicit opt-in: a sudo script is WRITTEN, the path is NOT deleted,
    # and no password is ever involved (the CLI has no password parameter at all).
    res = C.execute(plan, {cand["id"]}, report, apply=True, include_interpreters=True)
    r0 = next(r for r in res["results"] if r["id"] == cand["id"])
    assert r0["status"] == "handoff" and Path(r0["script"]).exists()


# ── Scenario 8: a clean machine (nothing to do) ───────────────────────────────
def test_scenario_clean_machine(tmp_path):
    pyenv = tmp_path / "pyenv/3.12.0"
    (pyenv / "bin").mkdir(parents=True)
    v = _venv(tmp_path / "proj/.venv", str(pyenv / "bin"), "3.12.0")
    report = _machine(tmp_path, interpreters=[_interp("pyenv", "3.12.0", "3.12.0", pyenv)],
                      venvs=[v])
    plan = P.build_plan(report)
    assert plan["candidates"] == []
    assert plan["total_reclaim_kb"] == 0


# ── Scenario 9: stale plan vs a live machine (re-validation must save it) ─────
def test_scenario_stale_plan_revalidates(tmp_path):
    live = tmp_path / "live/venv"
    (live / "bin").mkdir(parents=True)
    (live / "bin" / "python").write_text("")
    stale = {"candidates": [{"id": 1, "category": "broken_venv", "path": str(live),
                             "owner": "user", "risk": "low", "reason": "r",
                             "size_kb": 1, "size_h": "1K",
                             "action": {"type": "rmtree", "target": str(live)}}],
             "total_reclaim_h": "1K", "total_reclaim_kb": 1}
    fresh = _machine(tmp_path,
                     venvs=[{"path": str(live), "version": "3.11", "base": "x", "size": "1M"}],
                     automation={"crontab": [str(live / "bin" / "python")]})
    res = C.execute(stale, {1}, fresh, apply=True)
    assert Path(live).exists() and any(r["id"] == 1 for r in res["refused"])


# ── Scenario 10: blanket clean safety on a mixed machine ──────────────────────
def test_scenario_blanket_clean_is_conservative(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HOME", tmp_path)
    user = _site(tmp_path / ".local/lib/python3.11", "tqdm-4.0.0")
    pip = _cache(tmp_path / "Library/Caches/pip", "pip", CHEAP)
    hf = _cache(tmp_path / ".cache/huggingface", "huggingface", PRICEY)
    pyenv = tmp_path / "pyenv/3.10.0"
    (pyenv / "bin").mkdir(parents=True)
    brew = tmp_path / "homebrew/3.10.0"
    (brew / "bin").mkdir(parents=True)
    report = _machine(
        tmp_path,
        interpreters=[_interp("pyenv", "3.10.0", "3.10.0", pyenv),
                      _interp("homebrew", "python@3.10", "3.10.0", brew)],
        user_site_packages=[user], caches=[pip, hf],
    )
    plan = P.build_plan(report)
    C.execute(plan, C.default_selection(plan), report, apply=True)
    # removed: --user packages + cheap pip cache
    assert not Path(user["path"]).exists()
    assert not Path(pip["path"]).exists()
    # SURVIVED: the expensive model cache and the redundant interpreter
    assert Path(hf["path"]).exists()
    assert Path(brew).exists()
