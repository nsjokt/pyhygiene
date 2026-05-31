# pyhygiene

Diagnose and **safely** clean up messy Python environments — multiple
interpreters (pyenv, Homebrew, python.org, system, conda), scattered
virtualenvs, orphaned `pip install --user` packages, redundant duplicate
versions.

What makes it safe (and different from a one-off `rm -rf` script):

- **Automation-aware.** Cross-checks `cron` and `launchd` *before* suggesting
  any deletion, so it never proposes removing the interpreter your nightly job
  actually runs.
- **Backup-first.** Captures a reinstallable package manifest before anything
  is removed.
- **Never auto-sudo.** Privileged deletions (e.g. a python.org framework under
  `/Library`) are handed off as a script you run yourself — a password is never
  taken from or used in an automated command.

## Install / run

Zero dependencies (stdlib only), so it runs anywhere:

```bash
uvx pyhygiene audit            # run without installing
# or
uv venv .venv && source .venv/bin/activate
uv pip install -e .            # editable dev install
pyhygiene audit
```

## Commands

| Command | Status | What it does |
|---|---|---|
| `pyhygiene audit [roots…] [--json]` | ✅ | Read-only diagnosis. `--json` for tooling/agents. |
| `pyhygiene guard status` | ✅ | Check whether the prevention guardrails are in place. |
| `pyhygiene plan` | 🚧 | Risk-ranked cleanup plan from an audit. |
| `pyhygiene clean` | 🚧 | Backup-first, automation-aware cleanup. |
| `pyhygiene guard install` | 🚧 | Install prevention guardrails (PEP 668 marker, etc.). |

The MVP intentionally ships **only read-only commands**. Mutating commands
arrive only with their safety guarantees attached.

## Design

See [`../mac_clear/DESIGN-python-hygiene.md`](../mac_clear/DESIGN-python-hygiene.md)
for the full architecture: a tool-agnostic core (this CLI + shell guardrails)
plus thin per-platform adapters (a Claude Code skill lives at
`~/.claude/skills/python-audit`, with Cursor / aider / `AGENTS.md` adapters to
follow).
