# Changelog

All notable changes to Synnoesis are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, the public surface may change between minor releases —
the message contract (send by agent-id → inbox record; read the inbox tail) is the
part held stable; transports underneath it may evolve.

## [Unreleased]

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

[Unreleased]: https://github.com/kioptix/synnoesis/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kioptix/synnoesis/releases/tag/v0.1.0
