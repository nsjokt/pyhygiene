"""Smoke tests for the read-only audit. These must never mutate the system."""
from pathlib import Path

from pyhygiene import audit


def test_audit_returns_expected_shape(tmp_path: Path):
    # A fake project root with a marker and a fake venv.
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / ".python-version").write_text("3.11.12\n")
    venv = tmp_path / "proj" / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text(
        "home = /opt/homebrew/opt/python@3.14/bin\nversion = 3.14.3\n"
    )

    report = audit.audit([tmp_path])

    assert set(report) == {
        "roots", "interpreters", "venvs", "project_markers",
        "automation", "user_site_packages",
    }
    # the fake venv is discovered and its base interpreter parsed
    assert any(v["version"] == "3.14.3" for v in report["venvs"])
    # the project marker is found, venv-internal files are not project markers
    assert any(m.endswith(".python-version") for m in report["project_markers"])


def test_render_text_is_stringy():
    report = audit.audit([])
    out = audit.render_text(report)
    assert "Python Hygiene Audit" in out
    assert "[4] Automation cross-check" in out
