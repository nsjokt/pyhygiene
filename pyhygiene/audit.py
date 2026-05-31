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
# Runner-style invocations (uv run / poetry run / …) don't name a venv path
# directly, so we resolve the venv from the command's working directory.
_RUNNER_RE = re.compile(r"\b(?:uv|poetry|pipenv|pdm|hatch|rye)\s+run\b")
_DIR_RE = re.compile(r"""(?:cd|--directory|--project)\s+["']?(/[^\s"';&|<>]+)""")

# Known package/tool caches. These are regenerable, but "refill cost" differs:
# wheel caches re-download in seconds; model caches re-download many GB.
_CACHE_SPECS = [
    ("pip", "pip", "cheap (re-downloads wheels)"),
    ("uv", "uv", "cheap (re-downloads wheels)"),
    ("poetry", "pypoetry", "cheap"),
    ("huggingface", "huggingface", "EXPENSIVE — re-downloads models (can be many GB)"),
    ("torch", "torch", "expensive — re-downloads model weights"),
]


def _runner_venv_refs(text: str) -> list[str]:
    """If a command uses `uv run`/`poetry run`/…, resolve the venv it would use
    from a `cd <dir>` / `--directory <dir>` in the same command."""
    if not _RUNNER_RE.search(text):
        return []
    refs: list[str] = []
    for d in _DIR_RE.findall(text):
        for sub in (".venv", "venv"):
            cand = Path(d) / sub / "bin" / "python"
            if cand.exists():
                refs.append(str(cand))
    return refs


# launchd often runs a wrapper script in a WorkingDirectory rather than a literal
# venv path. To see through that, expand a plist with its WorkingDirectory (as a
# synthetic `cd`) and the contents of any wrapper scripts it invokes.
_WORKDIR_RE = re.compile(r"<key>WorkingDirectory</key>\s*<string>([^<]+)</string>")
_SCRIPT_RE = re.compile(r"<string>(/[^<>\s]+\.(?:sh|bash|zsh))</string>")


def _expand_plist(text: str) -> str:
    extra: list[str] = []
    for wd in _WORKDIR_RE.findall(text):
        extra.append(f"cd {wd}")                 # so runner detection has a dir
    for sp in _SCRIPT_RE.findall(text):
        p = Path(sp)
        if p.is_file():
            try:
                extra.append(p.read_text(errors="ignore"))
            except Exception:
                pass
    return text + "\n" + "\n".join(extra)


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
        hits: set[str] = set()
        for line in cron.splitlines():          # per-line so cd/runner stay paired
            hits |= set(_REF_RE.findall(line)) | set(_runner_venv_refs(line))
        if hits:
            refs["crontab"] = sorted(hits)
    for d in (HOME / "Library/LaunchAgents", Path("/Library/LaunchAgents"),
              Path("/Library/LaunchDaemons")):
        if not d.is_dir():
            continue
        hits = set()
        for plist in d.glob("*.plist"):
            try:
                text = _expand_plist(plist.read_text(errors="ignore"))
            except Exception:
                continue
            hits |= set(_REF_RE.findall(text)) | set(_runner_venv_refs(text))
        # drop obvious non-interpreters like a bare "/bin/python" that never exists
        hits = {h for h in hits if Path(h).exists() or "venv" in h}
        if hits:
            refs[str(d)] = sorted(hits)
    return refs


def find_caches() -> list[dict]:
    """Regenerable package/tool caches — often the biggest safe disk win."""
    roots = [HOME / ".cache", HOME / "Library" / "Caches"]
    out: list[dict] = []
    seen: set[str] = set()
    for tool, name, refill in _CACHE_SPECS:
        for r in roots:
            p = r / name
            if p.is_dir() and str(p) not in seen:
                seen.add(str(p))
                out.append({"tool": tool, "path": str(p),
                            "size": dir_size_h(p), "refill": refill})
    return out


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
        "caches": find_caches(),
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

    L.append("\n=== [6] Caches (regenerable — often the biggest safe win) ===")
    if not report.get("caches"):
        L.append("  (none)")
    for c in report.get("caches", []):
        L.append(f"  {c['tool']:<12}{c['size']:<8}{c['path'].replace(str(HOME), '~')}")
        L.append(f"        refill: {c['refill']}")

    return "\n".join(L)
