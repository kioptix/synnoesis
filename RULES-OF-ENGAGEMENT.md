# Synnoesis — Rules of Engagement

_The operating rules for this repository. Established by haken (project owner),
2026-06-24. The 🔴 MANDATORY rules are non-negotiable and override convenience,
speed, or any contributor/agent's default behavior._

---

## 🔴 MANDATORY — NON-NEGOTIABLE

### ROE-1 — Review-before-commit. Nothing is committed until haken has reviewed and understands it.

No code, config, or doc enters a Synnoesis **commit** until **haken has
personally read it and understands it.** This is two things at once: a quality
gate, and haken's explicit goal — *to fully understand his own system* (he is
publishing it; he must own every line).

**Workflow (always):**
1. Contributor/agent **PREPARES** the change in the working tree (uncommitted).
2. haken **REVIEWS** it — walkthrough-style, chunked (one subsystem / coherent
   unit per sitting), with the *why* explained, not just the *what*.
3. **ONLY THEN** is it committed — by/with haken, on his explicit go.

**Applies to ALL code** — copied from the upstream personal-assistant tree,
AI-generated, or hand-modified. There is no "I'll just commit this quick."

**For AI coding agents / assistants:** NEVER run `git commit` or `git push` on
this repo autonomously. Stage and surface changes for haken's review; commit only
on his explicit, per-change authorization. Preparing files in the working tree for
review is fine and encouraged; committing them is not yours to do.

### ROE-2 — Professional version tagging (Semantic Versioning).

- **SemVer** (https://semver.org): `MAJOR.MINOR.PATCH`. `0.x` = pre-1.0 (public
  API may still change).
- Baseline: the scaffold is `0.0.1`. **First MVP release = `v0.1.0`** (first
  coherent, installable feature set).
- **Every release** gets: an **annotated tag** (`git tag -a vX.Y.Z -m "…"`) **+ a
  `CHANGELOG.md` entry** (Keep a Changelog format: Added / Changed / Fixed /
  Removed).
- The four version sources must **always agree**: the git tag, `pyproject.toml`
  `version`, `package.json` `version`, and the CHANGELOG heading.
- **No tagging un-reviewed code** — ROE-1 happens first.

---

## 🟢 PROFESSIONAL STANDARD — the bar for this repo

_(haken's first published OSS project — do it right, consistently.)_

- **Secrets NEVER committed.** Hardened `.gitignore` + a `gitleaks` / secret scan
  before **every** push. Protect commit-1's clean provenance — a leaked credential
  means rotate + history-surgery, and you can't un-publish.
- **Clean, atomic, meaningful commits.** Conventional-style messages
  (`feat:`, `fix:`, `docs:`, `chore:`, `test:` …), one logical change per commit,
  no `wip` / `asdf` noise. Every commit stands on its own and is reviewable.
- **`main` stays releasable.** Real work on feature branches; merge to `main` only
  when reviewed (ROE-1) and tests are green. Tag releases from `main`.
- **Tests gate releases.** `pytest -m "not live"` green before any merge or tag.
- **Everything documented.** README, QUICKSTART (the minimal *and* the opt-in mesh
  install paths), CHANGELOG, CONTRIBUTING, LICENSE (Apache-2.0 ✓).
- **Consistency.** Match the conventions already set: Apache-2.0, author
  *Groupe Kioptix Inc.*, homepage *synnoesis.dev*, and the README's Usage &
  Compliance framing. Don't introduce divergent styles or relicense pieces.

---

## 🟠 DESIGN PRINCIPLES (architectural rules — haken-direct 2026-06-24)

### DP-1 — No absolute paths, except container-internal.
The shipped code must contain **no machine-specific absolute paths** (no
`C:/claude-home`, `/c/claude-home`, `C:/Users/haken`, hardcoded interpreter paths).
All paths are **env/config-driven or derived relative to the code** (e.g. a single
`PA_HOME`/root-resolver). Absolute paths are permitted **only inside a container**,
where the container controls them (e.g. a fixed `/app` + `/data` layout). A path
that only works on the author's machine is a release blocker.

### DP-2 — Provider-neutral naming.
Synnoesis is **multi-provider** (Claude *and* others). No baked-in product/provider
names in structural identifiers — the core agent is not named after a single
provider, and no module / config-key / agent-id / package hardcodes "claude" or a
specific model. Names are generic or config-driven. Cosmetic mentions (docs, logs)
get cleaned to a professional, provider-neutral public face.

### DP-3 — Consolidate toward fewer moving parts.
Prefer fewer processes/services a user must run + supervise (service consolidation,
containerized deploy) over the current 1-process-per-MCP sprawl — weighed against
refactor cost. The bar: a stranger should not have to hand-start a dozen daemons.

## Scope reference

v0.1.0 ships the **mesh floor**: two or more local sessions message each other over
a file-backed mesh (Python only, no broker, no Docker) — see `docs/quickstart.md`.
Acceptance criterion: *a stranger can clone, follow the quickstart, and watch one
session send a message another receives — in a few minutes, all local.* The
cross-machine broker, and the broader assistant (scheduler, data services, a chat
surface), are deferred to later releases.

## Why these exist

haken is publishing his first open-source project and wants it done well. ROE-1 is
also a learning commitment — he reviews everything so he fully owns and understands
the system he's putting his name (Groupe Kioptix Inc.) on. The assisting agent's
job is to be a steady, professional OSS guide: keep it consistent, keep it
reviewable, and **never rush a commit.**
