# Changelog

All notable changes to Synnoesis are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, the public surface may change between minor releases —
the message contract (send by agent-id → inbox record; read the inbox tail) is the
part held stable; transports underneath it may evolve.

## [Unreleased]

## [0.5.0] — the agent runner, presence & durable delivery

### Added

- **`synnoesis agent` — the agent runner.** A config-driven daemon that attaches a
  model to the mesh: subscribe → verify locally → authorize → model turn → signed
  reply. Until now Synnoesis moved messages *between* agents but never ran one; the
  roles in `examples/` are now executable rather than illustrative. Any
  OpenAI-compatible endpoint (Ollama, OpenRouter, vLLM, …) via `base_url`; no new
  dependencies.
- **Durable delivery.** `listen` now connects with a stable client-id and a persistent
  broker session, so qos1 messages published while a listener is DOWN are queued and
  delivered on reconnect. Previously they were simply lost. On connect the listener
  reports whether the broker **resumed** a session (a backlog is arriving) or opened a
  **new** one (nothing was held) — the two are otherwise indistinguishable.
- **Presence.** Listeners publish a retained, record-signed state document to
  `agent/<id>/state` — `online` on connect, `offline` on clean shutdown, and an
  `offline via lwt` Last Will that the *broker* publishes if the agent dies
  ungracefully.
- **`synnoesis who`** — list agents' presence with age and signature status.
- `send` prints a best-effort note when the recipient looks offline (never fails or
  delays the send), and `doctor` gained a presence section that reads this agent's own
  retained record back from the broker.

### Security

- **The agent runner has no tools.** The model turn is text-in → text-out. No
  filesystem, no shell, no network egress beyond the configured model endpoint. This
  is a structural choice, not a filter: injection *detection* is a semantic
  classifier, and a semantic classifier cannot carry a security boundary. An injected
  agent can be made to **say** anything and can **do** nothing.
- 🔴 **A signed agent reply is AUTHENTIC and UNVETTED.** The runner signs its output,
  so untrusted content that arrives leaves *authenticated under the agent's key* — the
  daemon is harmless itself but is a **signature-laundering step**, and an injection
  can chain through it to a downstream peer that *does* have tools. Replies carry an
  explicit banner saying so. Consumers must not read `verify=ok` as "safe to act on".
- **Authorization is separate from verification.** `respond_to` defaults to **empty**:
  the agent replies to nobody until configured. Keyring membership answers *who is
  this*; `respond_to` answers *will I act on it*. Conflating them would let anyone
  whose key you ever enrolled drive your agent. An unlisted sender gets a loud,
  instructive refusal in the log — and no reply.
- **Signature enforcement is forced on** in the agent path regardless of
  `SYN_ENFORCE_SIGNING`, and startup fails loudly if `cryptography` is absent.
- **Loop containment:** a per-process rolling reply budget (default 20 / 600s, logged
  at startup so it is tuned from observed trips rather than guessed). Exhaustion is
  announced once per window — a silent budget trip would be indistinguishable from a
  dead agent. Bounds what this process emits, not the mesh as a whole; a signed hop
  counter would bound the chain but requires a wire-contract change, so it is deferred.
- **No secrets in config:** `api_key_env` names an *environment variable*, never a key,
  and an unset variable is fatal rather than silently unauthenticated.
- **Presence is record-signed from day one** (`synnoesis/presence/v1` domain tag). An
  unsigned presence channel lets anyone who can reach the broker declare anyone else
  online or offline — worse than no presence, because it is confident and wrong. A
  record whose signature fails is displayed **as a forgery**, never folded into
  "offline" and never counted as online.
- **A validly-signed record on the wrong topic is rejected.** The signature covers the
  record's own `agent_id`; publishing your genuinely-signed record onto *another*
  agent's state topic is a real spoof that signature-checking alone does not catch, so
  topic and signed identity must agree.
- **Presence is never freshness-gated, deliberately.** A Last Will is signed at connect
  time but published at death time, so it legitimately arrives hours "stale" — a
  freshness check would drop exactly the record that reports an agent died. Staleness
  is displayed, not enforced, and `who` says so next to the answer.
- **Honest staleness in the output:** retained ≠ alive, and ages are cross-clock and
  therefore approximate. `who` prints both caveats alongside the results rather than
  burying them in docs — the display is informational and must never be reused as a
  liveness gate.
- A persistent session with an empty client-id is **refused**: the broker would queue
  messages under an identity nothing reconnects as.

## [0.4.1] — honest packaging metadata

A documentation and metadata release. **No code changes** — the library behaves
identically to 0.4.0. It exists because PyPI releases are immutable: the project
description shown on the package page is baked into the published artifact, and
0.4.0's said only that the project was early development, with no description of
what it actually does.

### Changed

- **README rewritten.** Leads with what Synnoesis is (a signed message mesh for
  agents), what works today versus what does not, a runnable 60-second example,
  and the security model — including what it deliberately does *not* do (signing
  is not encryption; the replay bound is a bound, not a proof; there is no
  authorization layer). The previous README said only "🚧 Early development."
- Package `description` and keywords now describe the mesh rather than a
  personal-assistant system, matching the README and the shipped surface.
- `Development Status` classifier `2 - Pre-Alpha` → `3 - Alpha`.

### Removed

- **The npm placeholder (`package.json`, `index.js`).** A five-line module
  exporting `{}` that carried the same version number as the real Python package,
  claiming a parity that did not exist. It was never published. If a JavaScript
  package ships in future it will be added back with an implementation behind it,
  and re-added to the version-consistency gate in the same change.

### Fixed

- **The release gate now runs the version-consistency check.** That check already
  existed (`tests/test_version_consistency.py`) but nothing invoked it at release
  time, so a build whose metadata files disagreed would have passed the gate. It
  now runs first, before any artifact is built, and a *missing* check fails the
  gate rather than being silently skipped.

## [0.4.0] — cross-machine transport (MQTT)

The file-transport floor (same machine, shared filesystem) has always been the
default; this release adds the **ceiling**: an MQTT broker so two machines can
exchange the SAME signed messages. The design bet from v0.1.0 pays off — the broker
is a *second writer* under the frozen contract, so ``read`` is unchanged and a
broker-delivered record is field-identical to a file record by construction.

The floor is untouched: with ``SYN_BROKER`` unset, behavior is byte-for-byte
identical to v0.3.0, and the MQTT client (``paho-mqtt``) is an **optional** extra —
a single-machine install stays dependency-free.

### Fixed

- **Packaging: the built artifacts did not contain the implementation.**
  `packages = ["synnoesis"]` shipped three files; the entire `comms/` floor (11
  modules) was absent from both the wheel and the sdist. A `pip install synnoesis`
  produced a CLI where every real subcommand raised `ImportError` — `--help` still
  worked, which is why it went unnoticed. `comms/` is now mapped into the package
  namespace via `package-dir`, and `cli.py` resolves the installed layout first.

  This is a **latent defect that predates this release**, not a v0.4.0 regression:
  `v0.3.0`'s `pyproject.toml` is identical. It never reached a user — only the
  `0.0.1` name-reservation placeholder was ever published.

### Added
- ``synnoesis listen`` — the cross-machine RECEIVER bridge. Subscribes
  ``agent/<me>/inbox`` on the broker, **re-verifies each message against THIS
  machine's keyring** (never trusting the broker or a sender's self-reported
  verdict), and appends the same on-disk inbox record ``read`` already understands.
  It is the trust boundary the ``inbox.py`` security invariant calls for.
- ``send`` MQTT path — publishes the signed inner envelope **verbatim** to the
  broker when ``SYN_BROKER`` is set. ``--local`` forces the file transport;
  ``--via {auto,file,mqtt}`` selects explicitly (``mqtt`` errors rather than
  silently falling back). Every broker send **announces its transport**, so an
  auto-selected network egress is never silent.
- Opt-in dependency extras: ``signing`` (``cryptography``), ``mqtt`` (``paho-mqtt>=2.0``
  **+ ``cryptography``** — cross-machine bundles signing, since verifying a message
  received over an untrusted broker is the whole point), and ``all``. The bare install
  stays dependency-free.
- ``doctor`` now reports broker config, transport-security posture, reachability,
  auth, and paho availability.
- New environment surface (all ``SYN_*``): ``SYN_BROKER``, ``SYN_BROKER_TLS_CA``,
  ``SYN_BROKER_USER``, ``SYN_BROKER_PASSFILE``, ``SYN_ALLOW_PLAINTEXT``,
  ``SYN_MAX_AGE_SEC``.

### Security
- **Fail-closed off-box:** a non-loopback broker without TLS is **refused** unless
  ``SYN_ALLOW_PLAINTEXT=1`` is explicitly set — and even then it **warns on every
  connect**. TLS (``SYN_BROKER_TLS_CA``) verifies the broker certificate; there is
  deliberately no skip-verify option.
- **Replay defense:** a bounded seen-nonce cache drops qos1 duplicates and in-window
  replays; a signed-``_at`` freshness check (``SYN_MAX_AGE_SEC``, default 300s; an
  attacker cannot forge a fresh timestamp) drops replays beyond the window. The bound
  is honest — "no replay older than N", not "replay-proof".
- **No secrets in argv:** the broker password is read from a file
  (``SYN_BROKER_PASSFILE``), never a command-line flag.
- **Signing is not encryption (stated plainly):** messages are signed (authenticity +
  integrity) and TLS protects them in transit, but they are **not end-to-end
  encrypted — the broker sees message content.** End-to-end confidentiality is a
  non-goal of this release (the signature tag registry reserves
  ``synnoesis/v2/encmsg`` for a future encrypted envelope).

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

[Unreleased]: https://github.com/kioptix/synnoesis/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/kioptix/synnoesis/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/kioptix/synnoesis/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/kioptix/synnoesis/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/kioptix/synnoesis/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kioptix/synnoesis/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kioptix/synnoesis/releases/tag/v0.1.0
