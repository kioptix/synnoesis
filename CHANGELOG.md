# Changelog

All notable changes to Synnoesis are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, the public surface may change between minor releases —
the message contract (send by agent-id → inbox record; read the inbox tail) is the
part held stable; transports underneath it may evolve.

## [Unreleased]

## [0.3.0] — key-distribution floor hardening

A minor release hardening the key-distribution UX on the existing Ed25519 + JSON
keyring. Stdlib-only, no new runtime deps, keyring entry shape unchanged, message
contract and on-disk format unchanged — all additive over v0.2.0.

### Added
- Key **fingerprints** — `synnoesis-fp:<sha256 of the 32-byte Ed25519 pubkey>`,
  printed by `keygen` and a new `fingerprint --agent-id <id>` command, for
  out-of-band key comparison between two agents (Signal safety-number style).
- `keyring --add --expect-fingerprint <fp>` — computes the added key's
  fingerprint and **refuses on mismatch**, mechanizing the out-of-band verify.
- **Refuse-on-conflict** `keyring --add` — adding an existing agent-id with a
  *different* key is refused (no silent identity overwrite) unless `--rotate` is
  passed; `keyring --rotate` is the explicit key-replacement path.
- `doctor` — prints resolved `PA_HOME`, agent-id, private-key-present, and
  cryptography-available; surfaces a corrupt keyring distinctly. Kills silent
  env/config drift.
- `keyring --export` / `--import` — text, `known_hosts`-shape; round-trips the
  keyring so key-loss recovery is a file copy, not N manual pastes.

### Changed
- A key command (`keygen`) now **fails loud** (non-zero exit + clear stderr) when
  `cryptography` is unavailable, instead of exiting 0 while inert.
- Accurate Windows key-permissions warning when `chmod 0o600` no-ops on the
  filesystem (points at the user-dir ACL rather than reporting a false success).
- Write-path hardening — pubkeys are validated (base64 + 32-byte length) on every
  `--add`/`--import` so a malformed key can't be pinned; keyring writes are atomic
  (`.tmp` + `os.replace`) and refuse to overwrite an unparseable keyring.

## [0.2.0] — console command, opt-in enforcement, signing CLIs

A minor release: a console entrypoint, an opt-in signing-enforcement mode, and
the two thin signing CLIs the quickstart had pointed at. The message contract and
on-disk format are unchanged (no new transports, no new runtime deps); the
minimum supported Python is raised to 3.10 (see **Changed**).

### Added
- Console command — run the mesh with no install via `python synnoesis.py`
  (`python synnoesis.py send --to bob "hi"` / `python synnoesis.py read --agent-id bob`),
  or `python -m synnoesis`; optionally install a `synnoesis` console script with
  `pip install -e .`. All routes delegate to the existing `comms/send.py` /
  `comms/inbox.py` mains, so every flag is inherited unchanged.
- Opt-in `SYN_ENFORCE_SIGNING` strict-verify mode (default **off**) — when set,
  the inbox delivers only signature-verified (`ok`) records and suppresses
  `bad` / `no-key` / `unsigned`, emitting a `# enforce: suppressed N unverified
  record(s)` line to stderr. Default-off preserves today's warn-only behavior
  byte-for-byte.
- `comms/keygen.py` — thin CLI over the existing `sign.py` primitives: generates
  a born-local Ed25519 keypair for an agent and prints its public key.
- `comms/keyring.py` — thin CLI to register another agent's public key in the
  local keyring (the trust decision), driving the §5 walkthrough end-to-end.

### Changed
- Minimum Python is now **3.10** (was 3.9). Python 3.9 reached end-of-life in
  October 2025, and the `cryptography` library used for optional signing no
  longer ships 3.9 wheels — requiring 3.10+ keeps the signing path on current,
  security-patched `cryptography` and the install a simple `pip install
  cryptography` (no source build, no version pin).

### Fixed
- `docs/quickstart.md` — corrected the signing walkthrough to match the shipped
  floor: removed the `keygen`/`keyring` CLI invocations that didn't exist yet (now
  reinstated with the real tools above), and de-versioned the cross-machine broker
  note from "v0.1.1" to "a future release."
- `comms/inbox.py` — `read` no longer crashes on a host with no time-zone database
  (e.g. a clean install on Windows, where the stdlib `zoneinfo` has no data and the
  `tzdata` package is absent). It now degrades to UTC with a one-time warning,
  keeping the "stdlib only, no required runtime deps" promise intact.

### Notes
- The message contract (`wrap_outer`'s 4-key outer record, send-by-id → inbox
  tail) is **unchanged**; on-disk `*-inbox.jsonl` files remain byte-identical to
  v0.1.0.

## [0.1.0] — first MVP: the mesh floor

The smallest complete slice: two or more Claude Code sessions on one machine
messaging each other over a local, file-backed mesh — Python only, no broker, no
Docker.

### Added
- `comms/send.py` — send a signed message to an agent by id; writes the signed
  record straight to the recipient's inbox file. No broker, no paho — a shared
  filesystem is the transport.
- `comms/inbox.py` — read an agent's inbox tail, surfacing each message's signature
  trust marker.
- `comms/sign.py` — Ed25519 message signing/verification (warn mode; degrades to
  unsigned when `cryptography` is absent).
- `comms/wire.py` — the single builder for the on-disk inbox record, so the writer
  (and any future transport) produces a field-identical shape.
- `comms/paths.py` — portable data-home resolution (`PA_HOME`, else `~/.synnoesis`);
  no hardcoded absolute paths.
- `docs/quickstart.md` — clone → run-from-folder walkthrough + the trust model.
- `examples/` — proposer / skeptic / arbiter role prompts for a "minds confer" demo.
- Self-running gate tests under `tests/`.

### Notes
- Identities, the owner handle, and the security-alert orchestrator are all
  config-driven (`PA_AGENT_ID`, `PA_OWNER_ID`, `PA_ORCHESTRATOR_ID`) — no baked-in
  names.
- Cross-machine messaging (an OS-agnostic, Python-based broker) is planned for a
  future release.

[Unreleased]: https://github.com/kioptix/synnoesis/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/kioptix/synnoesis/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kioptix/synnoesis/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kioptix/synnoesis/releases/tag/v0.1.0
