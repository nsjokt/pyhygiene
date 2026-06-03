"""Tests for the v0.2 additions: cache awareness + uv-run automation detection."""
from pathlib import Path

from pyhygiene import audit as A
from pyhygiene import plan as P


def test_find_caches_detects_and_classifies(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "HOME", tmp_path)
    (tmp_path / ".cache" / "pip").mkdir(parents=True)
    (tmp_path / ".cache" / "huggingface").mkdir(parents=True)
    caches = {c["tool"]: c for c in A.find_caches()}
    assert "pip" in caches and "huggingface" in caches
    assert "expensive" in caches["huggingface"]["refill"].lower()   # model cache flagged
    assert "cheap" in caches["pip"]["refill"].lower()


def test_runner_venv_refs_resolves_uv_run(tmp_path):
    proj = tmp_path / "market-tool"
    (proj / ".venv" / "bin").mkdir(parents=True)
    (proj / ".venv" / "bin" / "python").write_text("")
    cmd = f"cd {proj} && uv run python -m app.daily 2>&1"
    refs = A._runner_venv_refs(cmd)
    assert str(proj / ".venv" / "bin" / "python") in refs
    # a plain command with no runner yields nothing
    assert A._runner_venv_refs(f"cd {proj} && ls") == []


def test_wrapper_script_automation_resolves_via_workingdir(tmp_path):
    # launchd runs a wrapper .sh in a WorkingDirectory; the `uv run` lives inside
    # the script, not the plist. Detection must follow both.
    proj = tmp_path / "market-tool"
    (proj / ".venv" / "bin").mkdir(parents=True)
    (proj / ".venv" / "bin" / "python").write_text("")
    wrapper = proj / "scripts" / "run.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text('#!/bin/bash\ncd "$PROJECT_ROOT"\nuv run python -m app\n')
    plist = (f"<key>WorkingDirectory</key><string>{proj}</string>"
             f"<string>/bin/bash</string><string>{wrapper}</string>")
    refs = A._runner_venv_refs(A._expand_plist(plist))
    assert str(proj / ".venv" / "bin" / "python") in refs


def test_cache_candidates_ranked_with_risk(tmp_path):
    # cheap cache → low risk; model cache → medium risk (gated from blanket clean)
    report = {"roots": [], "interpreters": [], "venvs": [], "project_markers": [],
              "automation": {}, "user_site_packages": [],
              "caches": [
                  {"tool": "uv", "path": str(tmp_path / "uv"), "size": "4G",
                   "refill": "cheap (re-downloads wheels)"},
                  {"tool": "huggingface", "path": str(tmp_path / "hf"), "size": "13G",
                   "refill": "EXPENSIVE — re-downloads models (can be many GB)"},
              ]}
    (tmp_path / "uv").mkdir()
    (tmp_path / "hf").mkdir()
    plan = P.build_plan(report)
    by_tool = {c.get("tool"): c for c in plan["candidates"] if c["category"] == "cache"}
    assert by_tool["uv"]["risk"] == "low"
    assert by_tool["huggingface"]["risk"] == "medium"
