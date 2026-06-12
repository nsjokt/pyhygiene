# pyhygiene

Diagnose and **safely** clean up messy Python environments — multiple
interpreters (pyenv, Homebrew, python.org, system), scattered virtualenvs,
orphaned `pip install --user` packages, redundant duplicate versions, and
multi-GB package caches.

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

| Command | What it does |
|---|---|
| `pyhygiene audit [roots…] [--json]` | Read-only diagnosis: interpreters, venvs, project mapping, caches, and a `cron`/`launchd` cross-check. |
| `pyhygiene plan [roots…] [--json]` | Risk-ranked cleanup plan with reclaimable size. Read-only. |
| `pyhygiene clean [roots…]` | Cleanup. **Dry-run by default** — pass `--apply` to execute (a backup manifest is written first). |
| `pyhygiene guard status` | Check whether the prevention guardrails are installed. |
| `pyhygiene guard install` | Install the prevention guardrails (PEP 668 marker, etc.) — idempotent. |

`clean` is a dry-run unless you pass `--apply`. A blanket run only removes
low-risk items (orphaned `--user` packages, broken venvs, cheap caches);
redundant interpreters and expensive model caches require explicit selection
(`--include-interpreters`, `--only cache`, or `--id`), and root-owned removals
are handed off as a script you run yourself.

## Design

A **tool-agnostic core** (this CLI + OS/shell guardrails) plus thin
**per-platform adapters**. A Claude Code skill (`python-audit`) wraps the CLI;
Cursor / aider / `AGENTS.md` adapters can follow. The safety logic —
automation cross-check, backup-first, never auto-sudo — lives in the CLI, so
every adapter inherits it rather than re-implementing it.
