"""Prevention guardrails — the "don't let it get messy again" layer.

These are deliberately OS/shell-level (a shell export, a PEP 668 marker) rather
than tied to any one editor or agent, so they protect every process that runs
pip — Claude, Cursor, a human, a Makefile — not just one tool.

`install` is idempotent: it detects what's already in place and only adds what's
missing, so it's safe to run repeatedly.
"""
from __future__ import annotations

import os
from pathlib import Path
from shutil import which

HOME = Path.home()

SENTINEL = "# ── Python 환경 가드레일 (pyhygiene) ──"
MARKER_TEXT = (
    "[externally-managed]\n"
    "Error=Direct install into this interpreter is disabled. Use a venv: "
    "uv venv .venv && source .venv/bin/activate   |   one-off: gpip install <pkg>\n"
)


def detect_shell_rc() -> Path:
    shell = os.environ.get("SHELL", "")
    if "bash" in shell:
        return HOME / ".bashrc"
    return HOME / ".zshrc"  # zsh is macOS default


def _hook_shell() -> str:
    return "bash" if "bash" in os.environ.get("SHELL", "") else "zsh"


def _block() -> str:
    direnv = (f'eval "$(direnv hook {_hook_shell()})"' if which("direnv")
              else "# (direnv not installed — run `brew install direnv` for auto .venv)")
    return (
        f"\n{SENTINEL}\n"
        "# 가상환경 밖에서의 pip install / --user 를 전면 차단\n"
        "export PIP_REQUIRE_VIRTUALENV=true\n"
        'gpip() { PIP_REQUIRE_VIRTUALENV=false command pip "$@"; }\n'
        f"{direnv}\n"
        "# ── /가드레일 ──\n"
    )


def _pyenv_versions(root: Path | None = None) -> Path:
    return (root or (HOME / ".pyenv")) / "versions"


def status(*, pyenv_root: Path | None = None) -> list[dict]:
    checks: list[dict] = []
    env = os.environ.get("PIP_REQUIRE_VIRTUALENV", "")
    checks.append({"name": "PIP_REQUIRE_VIRTUALENV",
                   "ok": env.lower() in ("1", "true", "yes"),
                   "detail": env or "(unset in this process)"})
    pv = _pyenv_versions(pyenv_root)
    if pv.is_dir():
        for v in sorted(pv.iterdir()):
            has = any((lib / "EXTERNALLY-MANAGED").exists() for lib in v.glob("lib/python*"))
            checks.append({"name": f"pyenv {v.name} PEP668 marker", "ok": has,
                           "detail": "present" if has else "missing"})
    checks.append({"name": "direnv", "ok": which("direnv") is not None,
                   "detail": "installed" if which("direnv") else "not installed (optional)"})
    return checks


def install(*, apply: bool = True, rc_path: Path | None = None,
            pyenv_root: Path | None = None) -> dict:
    rc = rc_path or detect_shell_rc()
    actions: list[dict] = []

    # 1) shell rc guardrail block
    shell = os.environ.get("SHELL", "")
    if "fish" in shell and rc_path is None:
        # the block is POSIX (bash/zsh) syntax; writing it into a fish config
        # would silently never load. Tell the user instead of corrupting it.
        actions.append({"kind": "shell-rc",
                        "target": str(HOME / ".config/fish/config.fish"),
                        "status": "unsupported-shell (fish): add "
                                  "'set -gx PIP_REQUIRE_VIRTUALENV true' manually"})
    else:
        try:
            rc_text = rc.read_text(errors="surrogateescape") if rc.exists() else ""
            if SENTINEL in rc_text:
                actions.append({"kind": "shell-rc", "target": str(rc), "status": "already-present"})
            elif apply:
                with rc.open("a", encoding="utf-8", errors="surrogateescape") as f:
                    if rc_text and not rc_text.endswith("\n"):
                        f.write("\n")
                    f.write(_block())
                actions.append({"kind": "shell-rc", "target": str(rc), "status": "added"})
            else:
                actions.append({"kind": "shell-rc", "target": str(rc), "status": "would-add"})
        except OSError as e:  # unreadable/unwritable rc shouldn't crash install
            actions.append({"kind": "shell-rc", "target": str(rc), "status": f"failed: {e}"})

    # 2) PEP 668 markers on every pyenv interpreter
    pv = _pyenv_versions(pyenv_root)
    if pv.is_dir():
        for v in sorted(pv.iterdir()):
            for lib in v.glob("lib/python*"):
                marker = lib / "EXTERNALLY-MANAGED"
                if marker.exists():
                    actions.append({"kind": "pep668", "target": str(marker), "status": "already-present"})
                elif apply:
                    try:
                        marker.write_text(MARKER_TEXT)
                        actions.append({"kind": "pep668", "target": str(marker), "status": "added"})
                    except OSError as e:  # read-only interpreter dir → record, don't crash
                        actions.append({"kind": "pep668", "target": str(marker), "status": f"failed: {e}"})
                else:
                    actions.append({"kind": "pep668", "target": str(marker), "status": "would-add"})

    # 3) direnv advisory (we don't auto-install a brew package)
    if not which("direnv"):
        actions.append({"kind": "direnv", "target": "-",
                        "status": "advise: brew install direnv (optional, for auto .venv)"})

    return {"apply": apply, "rc": str(rc), "actions": actions}


def render_status(checks: list[dict]) -> str:
    L = ["=== guard status ==="]
    for c in checks:
        L.append(f"  [{'OK' if c['ok'] else '  '}] {c['name']}: {c['detail']}")
    return "\n".join(L)


def render_install(result: dict) -> str:
    verb = "would change" if not result["apply"] else "applied"
    L = [f"=== guard install ({verb}) ==="]
    for a in result["actions"]:
        L.append(f"  [{a['status']}] {a['kind']}  {a['target'].replace(str(HOME), '~')}")
    if not result["apply"]:
        L.append("\n  Re-run without --dry-run to apply. (Open a new shell afterward to load it.)")
    else:
        L.append("\n  Done. Open a new shell (or `source` your rc) to load the env guardrail.")
    return "\n".join(L)
