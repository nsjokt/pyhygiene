"""Read-only audit of the Python environment.

Enumerates interpreters, virtualenvs, project markers, scheduled-automation
references, and orphaned ``--user`` site-packages. Nothing here mutates the
system — every function only reads. The result is a plain dict so it can be
rendered as text or emitted as JSON for downstream ``plan``/``clean`` steps.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

HOME = Path.home()

# Directories that are huge and never interesting to descend into.
_PRUNE = {"Library", "Caches", ".Trash", "node_modules", ".git", "__pycache__"}
# Reference shape we look for in cron/launchd: a path to a python interpreter
# or into a venv's bin/. Used to mark things as in-use (PROTECTED).
_REF_RE = re.compile(r"/[^\s<>\"']*(?:python[0-9.]*|/\.?venv/bin/[A-Za-z0-9._-]+)")


def _run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return out.stdout
    except Exception:
        return ""


def dir_size_h(path: str | Path) -> str:
    """Human-readable size via ``du`` (fast, POSIX). Empty string on failure."""
    out = _run(["du", "-sh", str(path)])
    return out.split("\t")[0].strip() if out else ""


def _version_of(python_bin: Path) -> str:
    out = _run([str(python_bin), "--version"])
    parts = out.strip().split()
    return parts[1] if len(parts) >= 2 else "?"


def default_roots() -> list[Path]:
    names = ["Desktop", "Documents", "projects", "code", "dev", "src", "work",
             "repos", "git"]
    roots = [HOME / n for n in names if (HOME / n).is_dir()]
    cwd = Path.cwd()
    if cwd not in roots:
        roots.append(cwd)
    return roots


def find_interpreters() -> list[dict]:
    found: list[dict] = []
    # pyenv
    pyenv_versions = HOME / ".pyenv" / "versions"
    if pyenv_versions.is_dir():
        for v in sorted(pyenv_versions.iterdir()):
            if (v / "bin" / "python").exists():
                found.append({"kind": "pyenv", "label": v.name,
                              "version": _version_of(v / "bin" / "python"),
                              "path": str(v), "size": dir_size_h(v),
                              "protected": False})
    # homebrew
    for cellar in ("/opt/homebrew/Cellar", "/usr/local/Cellar"):
        c = Path(cellar)
        if not c.is_dir():
            continue
        for formula in sorted(c.glob("python@*")):
            for ver in sorted(formula.iterdir()):
                found.append({"kind": "homebrew", "label": formula.name,
                              "version": ver.name, "path": str(ver),
                              "size": dir_size_h(ver), "protected": False})
    # python.org
    pyorg = Path("/Library/Frameworks/Python.framework/Versions")
    if pyorg.is_dir():
        for v in sorted(pyorg.iterdir()):
            if v.name == "Current":
                continue
            found.append({"kind": "python.org", "label": v.name,
                          "version": v.name, "path": str(v),
                          "size": dir_size_h(v), "protected": False})
    # system (OS-managed, protected by default)
    if Path("/usr/bin/python3").exists():
        found.append({"kind": "system", "label": "os",
                      "version": _version_of(Path("/usr/bin/python3")),
                      "path": "/usr/bin/python3", "size": "(OS)",
                      "protected": True})
    return found


def _walk_prune(root: Path, extra_prune: set[str] = frozenset()):
    """os.walk that prunes heavy dirs in-place for speed."""
    prune = _PRUNE | set(extra_prune)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in prune]
        yield dirpath, dirnames, filenames


def find_venvs(roots: list[Path]) -> list[dict]:
    venvs: list[dict] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in _walk_prune(root):
            if "pyvenv.cfg" not in files:
                continue
            vdir = Path(dirpath)
            if str(vdir) in seen:
                continue
            seen.add(str(vdir))
            cfg = {}
            for line in (vdir / "pyvenv.cfg").read_text(errors="ignore").splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    cfg[k.strip()] = v.strip()
            venvs.append({
                "path": str(vdir),
                "version": cfg.get("version") or cfg.get("version_info", "?"),
                "base": cfg.get("home", "?"),
                "size": dir_size_h(vdir),
            })
    return sorted(venvs, key=lambda x: x["path"])


def find_project_markers(roots: list[Path], limit: int = 60) -> list[str]:
    markers = {".python-version", "pyproject.toml"}
    out: set[str] = set()
    # exclude venv internals / installed packages so we see the PROJECT's markers
    extra = {".venv", "venv", "site-packages"}
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in _walk_prune(root, extra):
            for f in files:
                if f in markers or (f.startswith("requirements") and f.endswith(".txt")):
                    out.add(str(Path(dirpath) / f))
    return sorted(out)[:limit]


def automation_refs() -> dict[str, list[str]]:
    """Interpreter/venv paths referenced by cron and launchd. PROTECTED."""
    refs: dict[str, list[str]] = {}
    cron = _run(["crontab", "-l"])
    if cron:
        hits = sorted(set(_REF_RE.findall(cron)))
        if hits:
            refs["crontab"] = hits
    for d in (HOME / "Library/LaunchAgents", Path("/Library/LaunchAgents"),
              Path("/Library/LaunchDaemons")):
        if not d.is_dir():
            continue
        hits: set[str] = set()
        for plist in d.glob("*.plist"):
            try:
                hits |= set(_REF_RE.findall(plist.read_text(errors="ignore")))
            except Exception:
                pass
        # drop obvious non-interpreters like a bare "/bin/python" that never exists
        hits = {h for h in hits if Path(h).exists() or "venv" in h}
        if hits:
            refs[str(d)] = sorted(hits)
    return refs


def user_site_packages() -> list[dict]:
    out: list[dict] = []
    for pat in ("Library/Python/*/lib", ".local/lib/python*"):
        for p in HOME.glob(pat):
            if p.is_dir():
                out.append({"path": str(p), "size": dir_size_h(p)})
    return out


def audit(roots: list[Path] | None = None) -> dict:
    roots = roots or default_roots()
    return {
        "roots": [str(r) for r in roots],
        "interpreters": find_interpreters(),
        "venvs": find_venvs(roots),
        "project_markers": find_project_markers(roots),
        "automation": automation_refs(),
        "user_site_packages": user_site_packages(),
    }


def render_text(report: dict) -> str:
    L: list[str] = []
    L.append("################ Python Hygiene Audit ################")
    L.append(f"scan roots: {', '.join(report['roots'])}")

    L.append("\n=== [1] Interpreters (kind | version | size | path) ===")
    for it in report["interpreters"]:
        prot = "  [protected]" if it["protected"] else ""
        L.append(f"  {it['kind']:<11}{it['version']:<10}{it['size']:<8}{it['path']}{prot}")

    L.append("\n=== [2] Virtualenvs (built from which interpreter) ===")
    if not report["venvs"]:
        L.append("  (none found)")
    for v in report["venvs"]:
        L.append(f"  {v['path']}  ({v['size']})")
        L.append(f"      python {v['version']}  ←  {v['base']}")

    L.append("\n=== [3] Project markers ===")
    for m in report["project_markers"]:
        L.append(f"  {m.replace(str(HOME), '~')}")

    L.append("\n=== [4] Automation cross-check — IN USE (PROTECTED) ===")
    if not report["automation"]:
        L.append("  (no python references in cron/launchd)")
    for src, hits in report["automation"].items():
        L.append(f"  [{src}]")
        for h in hits:
            L.append(f"    {h}")

    L.append("\n=== [5] --user site-packages (cleanup candidates) ===")
    if not report["user_site_packages"]:
        L.append("  (none)")
    for u in report["user_site_packages"]:
        L.append(f"  {u['path'].replace(str(HOME), '~')}   {u['size']}")

    return "\n".join(L)
