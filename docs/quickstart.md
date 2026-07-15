# Quickstart — the floor (one machine, Python only)

This is the **floor**: the smallest thing that works. Two or three Claude Code
sessions on a single machine, messaging each other over a **local, file-backed
mesh**. No message broker, no Docker, no network — just Python and the
filesystem. Get this running first; the cross-machine ceiling builds on it.

---

## 1. What this is

You open 2–3 Claude Code sessions side by side. Each one is an **agent** with an
id (`alice`, `bob`, ...). When one agent sends a message, it lands as a record
in a small per-recipient inbox file on disk; the recipient reads its inbox and
sees the message. That's the whole mesh at the floor: **send writes a file,
inbox reads it.** No daemon is running between them — delivery is just the
shared `state/` directory both sessions can see.

It feels like a tiny chat system, but the point is agents *collaborating*: in
the demo below, one session proposes, another pokes holes, a third makes the
call.

---

## 2. Prereqs

- **Python 3.10+** — check with `python --version`.
- **[Claude Code](https://claude.com/claude-code)** — one running session per
  agent (you'll open 2 or 3).
- **`cryptography`** — *optional*. The floor runs **without it** in unsigned
  **warn mode** (messages still flow, just marked `[!UNVERIFIED]`). Install it
  separately to turn on real Ed25519 signing (see [§3](#3-setup) /
  [§5](#5-trust-model)). If its build fails on your machine, just skip it —
  nothing else needs it.

Everything else is the Python standard library. The floor has **no broker and
no container** to install.

---

## 3. Setup

Clone the repo and create an isolated virtual environment:

```bash
git clone https://github.com/kioptix/synnoesis.git
cd synnoesis
python -m venv .venv
```

Activate the venv:

```bash
# bash / zsh (macOS, Linux, Git Bash)
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

The mesh floor needs **no third-party packages** — it runs on the Python standard
library alone, so there's nothing to install here.

**Optional — message signing.** To sign messages with Ed25519 instead of the
unsigned *warn mode*, install `cryptography`:

```bash
python -m pip install --upgrade pip   # so pip uses the prebuilt wheel, not a source build
pip install cryptography
```

If that build fails (an old `pip` falling back to building from source), just skip
it — the mesh runs fine **without** it, marking messages `[!UNVERIFIED]` instead of
`ok`. See [§5 Trust model](#5-trust-model).

Now point the mesh's runtime home at a **gitignored `state/` directory** inside
the clone. Every agent's inbox, keys, and keyring live here; `state/` is already
in `.gitignore`, so nothing runtime ever gets committed.

```bash
# bash / zsh
export PA_HOME="$(pwd)/state"
```

```powershell
# Windows PowerShell
$env:PA_HOME = "$PWD/state"
```

> `PA_HOME` is the single data root for the whole system (resolved by
> `comms/paths.py`). With it unset, the mesh would fall back to
> `~/.synnoesis/` in your home directory — fine too, but pinning it to the
> clone keeps each checkout self-contained and easy to wipe (`rm -rf state/`).

---

## 4. Run the demo

### Two sessions: alice → bob

You'll run **two Claude Code sessions**. In *each* one, activate the venv and set
`PA_HOME` as in [§3](#3-setup) — both sessions must share the same `PA_HOME` so
they read and write the same `state/` directory.

Give each session an identity with `PA_AGENT_ID`:

```bash
# Session A
export PA_AGENT_ID=alice      # bash
$env:PA_AGENT_ID = "alice"    # PowerShell
```

```bash
# Session B
export PA_AGENT_ID=bob        # bash
$env:PA_AGENT_ID = "bob"      # PowerShell
```

From **session A**, send a message to bob. The simplest entrypoint is the
top-level console command — it works straight from the clone, **no install
needed**:

```bash
python synnoesis.py send --to bob "hello from alice"
```

This is a thin doorway over the floor script below — every flag is the same.
The original script keeps working verbatim and is exactly equivalent:

```bash
python comms/send.py --local --to bob "hello from alice"
```

`--local` is the floor's no-broker fast path: it writes the message straight
into bob's inbox file under `PA_HOME`. (The sender id comes from
`PA_AGENT_ID`.)

In **session B**, read bob's inbox — again the console command first, with the
floor script equivalent right after:

```bash
python synnoesis.py read --agent-id bob
# equivalently:
python comms/inbox.py --agent-id bob
```

> **Optional — install a command.** If you'd rather type `synnoesis` instead of
> `python synnoesis.py`, install it as a console script from the clone:
> ```bash
> pip install -e .
> ```
> Then `synnoesis send --to bob "hi"` and `synnoesis read --agent-id bob` work
> from any directory. `python -m synnoesis send …` works too. The install is
> purely a convenience — the no-install `python synnoesis.py` path is the
> supported floor and never requires it.

You'll see the message, something like:

```
# bob inbox — last 2h, 1 item(s)
  [14:32 UTC] alice (urg=normal, age=0m): hello from alice
```

That round trip — A writes, B reads it back — is the mesh working. Reply the
same way (`--to alice` from session B) and read it in session A.

### Three sessions: proposer / skeptic / arbiter

The point of the mesh is agents *collaborating*, so here's a three-way version.
Open **three** sessions, each with the same `PA_HOME`, and give them the ids
`proposer`, `bob`→`skeptic`, and `arbiter`:

```bash
export PA_AGENT_ID=proposer    # session 1
export PA_AGENT_ID=skeptic     # session 2
export PA_AGENT_ID=arbiter     # session 3
```

In each session, paste the matching role brief from the `examples/` directory
as the session's first prompt:

- `examples/proposer.md` — drafts one concrete approach, sends it to the skeptic.
- `examples/skeptic.md` — stress-tests the proposal, sends the critique back.
- `examples/arbiter.md` — reads both sides from its inbox and makes the call.

Each brief tells that agent exactly which `comms/send.py --local --to ...` and
`comms/inbox.py` commands to run, so the debate flows proposer → skeptic →
arbiter over the same file-backed mesh you just tested. Hand the three sessions
the same task, kick off the proposer, and watch them confer.

---

## 5. Trust model

**Keep this visible — it's the whole point of signing.**

With **no keys set up**, the mesh runs in **warn mode**: messages are delivered,
but the reader can't prove who actually sent them, so every message is tagged:

```
  [14:32 UTC] alice [!UNVERIFIED] (urg=normal, age=0m): hello from alice
```

`[!UNVERIFIED]` means "the `alice` in this line is a *claim* the sender wrote,
not a fact the mesh checked." On one trusted machine that's usually fine — but
the marker is there on purpose so an unverified identity can never look clean.

To upgrade to **real signing** (requires `cryptography` from [§2](#2-prereqs)),
the model is two deliberate steps, each driven by a thin CLI over `comms/sign.py`:

1. **Each session generates its own Ed25519 keypair.** The private key stays on
   that machine and is *never* transmitted; only the public key is shared. In
   alice's session:

   ```bash
   python comms/keygen.py --agent-id alice
   ```

   This writes alice's private key under `PA_HOME` and prints her **public** key
   to share. Run the same in bob's session with `--agent-id bob`.

2. **Each session registers the *other* agent's PUBLIC key in its keyring.**
   This step **is the trust decision** — by adding bob's public key to alice's
   keyring, alice is declaring "I trust messages that bob signs." In alice's
   session, paste bob's printed public key:

   ```bash
   python comms/keyring.py --add bob --pubkey <bob pubkey>
   ```

   Bob does the mirror — `python comms/keyring.py --add alice --pubkey <alice
   pubkey>`. Nothing is trusted by default; you choose, key by key, whom to
   believe.

Once peers have each other's public keys registered, signed messages from a
known sender verify and the `[!UNVERIFIED]` marker disappears for them; an
unknown or tampered sender still gets flagged. A node **cannot forge another
agent's signature** without that agent's private key, which never leaves its
machine.

> The keyring is a plain JSON file under `PA_HOME` (`state/mesh-keyring.json`) —
> adding a key is a deliberate, inspectable act; there's no automatic key
> exchange, because automatic trust isn't trust.

---

## 5a. Enforcing signatures (opt-in)

By default the floor runs in **warn mode**: an unverified message is still
delivered, just marked. If you want the inbox to **refuse** anything it can't
cryptographically verify, set `SYN_ENFORCE_SIGNING`:

```bash
# bash / zsh
export SYN_ENFORCE_SIGNING=1
```

```powershell
# Windows PowerShell
$env:SYN_ENFORCE_SIGNING = "1"
```

With it set, `read` delivers **only** records whose signature verifies (`ok`)
and **suppresses** every unverified one — a forged/tampered signature (`bad`), a
sender not in your keyring (`no-key`), or an unsigned message (`unsigned`). When
one or more records are suppressed, a single summary line is written to stderr so
suppression is never silent:

```
# enforce: suppressed 2 unverified record(s)
```

The read still exits `0` — enforce changes *what's shown*, not the read-only
exit contract. Unset the variable (or set it empty) to return to warn mode.

> **If you ask to enforce but can't verify, the tool fails loudly.** Setting
> `SYN_ENFORCE_SIGNING` while `cryptography` is **not** installed means the
> verifier can't check anything — rather than silently black-holing 100% of your
> traffic, `read` prints a one-line config error to stderr and exits **non-zero**
> at startup. Install `cryptography` (see [§2](#2-prereqs)) or unset the variable.

---

## 6. Cross-machine (MQTT) — v0.4.0

The floor keeps every agent on **one machine** sharing one `state/` directory.
To put agents on **different machines**, Synnoesis speaks MQTT: `send` publishes
to a broker, and a small `listen` bridge on each recipient machine receives the
message, **verifies it locally**, and writes the same inbox record `read` already
understands. The commands you learned don't change — the broker just carries
messages across the network instead of the local filesystem.

> **Read this first — what the broker does and does not protect.** Synnoesis
> **signs** messages (authenticity + integrity) and, with TLS, encrypts them **in
> transit**. It does **not** end-to-end encrypt message *content*, so **the broker
> can read your messages.** A malicious broker cannot forge or tamper (the
> signature stops that — at worst it can drop/withhold), but it sees plaintext
> bodies. Don't send a broker content you wouldn't show its operator. End-to-end
> content encryption is a future release.

### 6.1 Install a broker (bring your own — we ship none)

Synnoesis is transport-only; use any MQTT broker. **Mosquitto** is a one-liner on
every OS and needs no Docker:

```bash
brew install mosquitto        # macOS
sudo apt install mosquitto    # Debian/Ubuntu
# Windows: the Eclipse Mosquitto installer from mosquitto.org
```

A container is optional if you prefer one — an example `compose/docker-compose.yml`
(pinned `eclipse-mosquitto`) is provided. For anything beyond a loopback test,
configure the broker with **TLS and a username/password** (see the Mosquitto docs);
Synnoesis refuses an unencrypted off-box connection by default (§6.5).

### 6.2 Install the cross-machine dependencies

Cross-machine needs two things: the broker client (`paho-mqtt`) **and** message signing
(`cryptography`) — because locally verifying a message that arrived over an *untrusted*
broker is the whole point of going cross-machine. The `mqtt` extra bundles both, so from
your checkout (a clone or this branch) the one-command install is:

```bash
pip install -e ".[mqtt]"     # paho-mqtt + cryptography, into your checkout
```

Prefer not to install the package? Install the two deps directly — the
`python synnoesis.py` run-from-clone path keeps working:

```bash
pip install "paho-mqtt>=2.0" cryptography
```

> `pip install "synnoesis[mqtt]"` (without `-e .`) also works, but only once a release is
> published to PyPI. When you're on a **checkout** (a clone or this branch), use one of the
> two commands above — the bare form would pull the last *published* version from PyPI, not
> your local code.

### 6.3 Trust genesis is out-of-band — NOT over the broker

Cross-machine signing uses the **same keys and fingerprints** from [§5](#5-trust-model),
with one rule the broker makes essential: **the broker moves messages, it can never
vouch for identity.** Establish trust *before* you connect, out-of-band.

On each machine, generate that agent's keypair (private key never leaves the box)
and read its fingerprint:

```bash
python synnoesis.py keygen --agent-id alice        # on alice's machine
python synnoesis.py fingerprint --agent-id alice   # prints synnoesis-fp:<hash>
```

Exchange **public keys** over a channel you already trust (in person, an existing
secure chat, `scp`) — **never** by pasting them through the broker or any automated
mesh channel. Then pin the peer's key **and verify its fingerprint matches** what
the peer read to you out-of-band:

```bash
# on bob's machine, pinning alice — compare the fingerprint by voice/secure channel first
python synnoesis.py keyring --add alice --pubkey <alice pubkey> \
    --expect-fingerprint synnoesis-fp:<alice fingerprint>
```

`--expect-fingerprint` refuses the add on a mismatch, mechanizing the out-of-band
check. Do the mirror on alice's machine for bob. Now each side cryptographically
trusts the other, established by a human — not by the network.

### 6.4 Point at the broker, run `listen`, and send

On **every machine**, set the broker address (and, off-loopback, TLS + auth):

```bash
export SYN_BROKER=broker.example.net:8883          # host[:port]
export SYN_BROKER_TLS_CA=/path/to/ca.pem           # TLS: verify the broker cert
export SYN_BROKER_USER=alice                        # broker username
export SYN_BROKER_PASSFILE=/path/to/broker.pass     # password from a file, never argv
```

On **bob's** machine, start the receiver bridge (foreground; leave it running):

```bash
python synnoesis.py listen --agent-id bob
# → listening as 'bob' on broker.example.net [agent/bob/inbox] …
```

From **alice's** machine, send exactly as before — with `SYN_BROKER` set, `send`
takes the broker automatically and announces it:

```bash
python synnoesis.py send --to bob "hello across the network"
# → via broker broker.example.net:8883 (TLS)  [--local for the file transport]
# published -> agent/bob/inbox  (normal from alice)
```

On bob's machine, `listen` prints that it appended the record, and `read` shows it —
verified, because bob's bridge re-checked alice's signature against **bob's own**
keyring:

```bash
python synnoesis.py read --agent-id bob
#   [.. ] alice (urg=normal, age=0m): hello across the network
```

`--local` still forces the file transport on a shared-filesystem host; `--via mqtt`
forces the broker and errors (rather than silently using a file) if `SYN_BROKER`
is unset. Run `python synnoesis.py doctor` on either machine to see the resolved
broker, transport-security posture, and reachability.

### 6.5 Security defaults (and how to relax them, carefully)

- **Off-box plaintext is refused.** With no `SYN_BROKER_TLS_CA` and a non-loopback
  broker, `send`/`listen` refuse to connect. On a **trusted encrypted underlay**
  (WireGuard / a tailnet) you may set `SYN_ALLOW_PLAINTEXT=1` — it connects but
  **warns on every connection**. Prefer real TLS.
- **Enforce signatures cross-machine.** Set `SYN_ENFORCE_SIGNING=1` (§5a) on the
  receiving side so `listen` delivers **only** messages that verify against your
  keyring — strongly recommended once your peers' keys are pinned.
- **Replay defense.** `listen` drops duplicate and stale messages: a signed
  timestamp older than `SYN_MAX_AGE_SEC` (default 300s) is rejected as a replay.
  If your machines' clocks can't be kept within that window, raise it or set
  `SYN_MAX_AGE_SEC=0` to disable the freshness check (dedup of duplicates stays on).

Supervising `listen` as a background service (systemd / launchd / a Windows
scheduled task) is up to you — v0.4.0 ships it as a foreground process on purpose.
