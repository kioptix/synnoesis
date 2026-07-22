# Synnoesis — repo instructions (AGENTS.md — AI coding sessions read this first)

This is **Groupe Kioptix Inc.'s official public OSS repo** (`github.com/kioptix/synnoesis`,
Apache-2.0, Groupe Kioptix Inc.). Treat it as production + public.

## 🔴 MANDATORY — read `RULES-OF-ENGAGEMENT.md` and follow it. Non-negotiable:

1. **ROE-1 — Review-before-commit.** NEVER `git commit` or `git push` autonomously.
   Prepare changes in the working tree, then surface them for **the maintainer's
   review**; they read + understand every change before it is committed. Commit only
   on their explicit, per-change go. This is their system to understand and own.
2. **ROE-2 — Versioning is SemVer + annotated tags + CHANGELOG**, all four version
   sources (tag / pyproject / `synnoesis/__init__.py` / CHANGELOG) kept in sync —
   enforced by `tests/test_version_consistency.py`, which `tools/release-gate.py`
   runs first. First MVP release = `v0.1.0`. No tagging un-reviewed code.

## Standard
Secrets never committed (hardened `.gitignore` + `gitleaks` before every push);
clean atomic conventional commits; `main` stays releasable (feature branches +
review + green `pytest -m "not live"`); match existing conventions (Apache-2.0,
Groupe Kioptix Inc., synnoesis.dev). v1 ships the local-mesh floor — see
`docs/quickstart.md` and `CHANGELOG.md`.

When in doubt: prepare, don't commit. Ask the maintainer.
