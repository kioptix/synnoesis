# Changelog

All notable changes to Synnoesis are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, the public surface may change between minor releases —
the message contract (send by agent-id → inbox record; read the inbox tail) is the
part held stable; transports underneath it may evolve.

## [Unreleased]

## [0.2.0] — console command, opt-in enforcement, signing CLIs

A non-breaking minor release: a console entrypoint, an opt-in signing-enforcement
mode, and the two thin signing CLIs the quickstart had pointed at. No new
transports, no new runtime deps, no change to the frozen on-disk contract.

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

[Unreleased]: https://github.com/kioptix/synnoesis/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kioptix/synnoesis/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kioptix/synnoesis/releases/tag/v0.1.0
