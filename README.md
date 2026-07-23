# Synnoesis

> **syn** (together) + **noēsis** (thinking) — *"minds thinking together."*

A **signed message mesh for AI agents**. Give each agent an Ed25519 identity, and
they can send each other messages — on one machine over the filesystem, or across
machines over an MQTT broker — with every message verified locally by the receiver
against its own keyring.

The broker moves bytes. It never vouches for identity.

Built and published by **[Groupe Kioptix Inc.](https://synnoesis.dev)**

---

## What works today (v0.4.x)

| | |
|---|---|
| `send` / `read` | Signed messages between agents on one machine. **Zero dependencies** — the file floor is standard library only. |
| `listen` | Cross-machine delivery over an MQTT broker, re-verified against the receiving machine's keyring. |
| `keygen` / `keyring` / `fingerprint` | Ed25519 identity: generate, exchange, pin, verify. |
| `doctor` | Diagnostics — config, broker reachability, transport-security posture. |

**Not yet:** there is no agent runner. Synnoesis moves messages *between* agents; it
does not run them. The roles in [`examples/`](examples/) are prompt files you paste
into your own agent sessions, not executable agents. A config-driven runner that
attaches a model to the mesh is the next release — see [Roadmap](#roadmap).

The message contract — *send by agent-id → inbox record; read the inbox tail* — is
the stable part. Transports underneath it may still change while the version is `0.x`.

## Install

```bash
pip install synnoesis                # file floor, no dependencies
pip install "synnoesis[signing]"     # + Ed25519 signing
pip install "synnoesis[mqtt]"        # + cross-machine transport (includes signing)
```

Python 3.10+. The bare install stays dependency-free on purpose: a single-machine
mesh should never fail to build.

> ⚠️ **Check your Python first — a too-old one fails confusingly, not loudly.** On
> Python < 3.10, `pip install synnoesis` may not error at all: it can quietly resolve
> to the ancient `0.0.1` name-reservation placeholder (the only release permitting
> 3.9), which installs "successfully" and does nothing. macOS in particular still
> ships `python3` as 3.9.x on older systems, so build the venv with an explicit
> `python3.12 -m venv .venv` and confirm with `python -V` once it's activated.

## 60 seconds: two agents, one machine

Open two shells. Give each an identity:

```bash
export PA_AGENT_ID=alice        # shell 1   ($env:PA_AGENT_ID = "alice" on PowerShell)
export PA_AGENT_ID=bob          # shell 2
```

Alice sends:

```bash
synnoesis send --local --to bob "hello from alice"
```

Bob reads:

```bash
synnoesis read --agent-id bob
```

That's the whole contract. No broker, no daemon, no config file.

The message arrives marked `[!UNSIGNED]` — you haven't generated any keys yet, and
Synnoesis says so rather than pretending. Run `synnoesis keygen` on each side and
exchange public keys (`synnoesis keyring --add`) to get `verify=ok`; the
[quickstart](docs/quickstart.md) walks through it.

## Across machines

Point both machines at a broker and run the receiver:

```bash
export SYN_BROKER=broker.example.net:8883
export SYN_BROKER_TLS_CA=/path/to/ca.pem
synnoesis listen
```

`send` now publishes to the broker instead of the local floor, and **announces which
transport it used** — an auto-selected network egress is never silent. `--local`
forces the file path; `--via {auto,file,mqtt}` selects explicitly, and `mqtt` errors
rather than quietly falling back.

Full walkthrough, including key exchange: [`docs/quickstart.md`](docs/quickstart.md).

## Security model

The design assumption is that **the broker is hostile** — it is infrastructure you
may not control, sitting between agents that trust each other.

| Property | How |
|---|---|
| **Authenticity** | Ed25519 signatures over `_from`, `_to`, `_at`, `_urgency`, `body`, `_nonce`. |
| **Local verification** | The receiver verifies against *its own* keyring. A sender's self-reported verdict and the broker's word are both ignored. |
| **Enforcement** | `SYN_ENFORCE_SIGNING=1` drops anything that isn't `ok` — and fails loudly at startup if `cryptography` is missing, rather than silently rejecting all traffic. |
| **Replay defense** | Bounded seen-nonce cache plus a signed-timestamp freshness window (`SYN_MAX_AGE_SEC`, default 300s). An attacker cannot forge a fresh `_at` — it is inside the signature. |
| **Fail-closed transport** | A non-loopback broker without TLS is **refused** unless `SYN_ALLOW_PLAINTEXT=1` is set explicitly — and even then it warns on every connect. There is deliberately no skip-verify option. |
| **No secrets in argv** | The broker password is read from a file (`SYN_BROKER_PASSFILE`), never a flag. |
| **Keys stay home** | Private keys are generated on the machine that uses them and are never transmitted. Only public keys are exchanged. |

### What it deliberately does not do

- **Signing is not encryption.** Messages are authenticated, not confidential. A
  broker operator can read every body. Use TLS for transport confidentiality, and
  don't put secrets in message bodies.
- **The replay bound is honest.** It is "no replay older than `SYN_MAX_AGE_SEC`", not
  "replay-proof." Widening the window widens replay tolerance by exactly the same
  amount — that tradeoff is yours to set deliberately.
- **No authorization layer.** A valid signature proves *who sent it*, not that they
  were allowed to ask for it. What an agent does with a verified message is the
  agent's problem.

## Roadmap

| Version | Theme |
|---|---|
| **0.5.0** | Agent runner (attach a model to the mesh) · presence + last-will · durable delivery across listener restarts |
| 0.6.0 | Hardening: per-agent singleton lock, bad-signature quarantine, reconnect watchdog |
| 0.7.0 | Signed control channel · liveness that distinguishes *wedged* from *online* |
| 1.0.0 | Broadcast topics · signed keyring distribution · encrypted envelope |

## Usage & compliance

Synnoesis is an orchestration layer. It routes to model providers — Anthropic's
Claude and others — directly or through gateways like OpenRouter, **always using
your own credentials**. It bundles no API keys and grants no model access of its own.

- **Personal / single-user (Anthropic):** a Claude subscription via the official
  Claude CLI is fine.
- **Multi-user / commercial:** use your own **API key** (Anthropic's Commercial
  Terms, or a commercial gateway such as OpenRouter) — *not* a personal
  subscription. Routing other users through personal subscription credentials
  violates Anthropic's terms.
- **You're responsible for the terms of every provider and gateway you use** — e.g.
  [Anthropic](https://www.anthropic.com/legal),
  [OpenRouter](https://openrouter.ai/terms). Provider terms flow *through* a gateway:
  using Claude via OpenRouter still binds you to Anthropic's terms.
- **Export & eligibility:** model access is subject to each provider's geographic
  availability and to applicable export-control and sanctions law (U.S. export
  controls restricted some frontier models for foreign nationals in 2026; gateways
  like OpenRouter likewise forbid circumventing geo-restrictions). Ensuring you and
  your users are eligible is your responsibility. Synnoesis is model-agnostic and
  guarantees no specific model's availability.
- **Data path:** prompts and outputs routed through a gateway pass through that
  gateway and the downstream provider, each with its own retention and privacy terms.
  Keep gateway prompt-logging off for sensitive data.

**Bottom line:** use Synnoesis commercially all you like — just bring your own keys,
so you stay compliant and protected.

## License

Released under the **Apache License 2.0** — free to use, modify, and distribute,
**including commercially**. See [`LICENSE`](LICENSE).

## Trademarks

"Kioptix", "Synnoesis", and associated logos are trademarks of Groupe Kioptix Inc.
and are **not** licensed under Apache 2.0 (see §6). Use the software freely; please
don't use the names or logos to brand your own product or imply endorsement.
