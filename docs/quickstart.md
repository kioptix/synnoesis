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
  [14:32 EDT] alice (urg=normal, age=0m): hello from alice
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
  [14:32 EDT] alice [!UNVERIFIED] (urg=normal, age=0m): hello from alice
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

## 6. Cross-machine — planned for a future release

The floor above keeps every agent on **one machine** sharing one `state/`
directory. Putting agents on **different machines** is planned for a future
release: a small **OS-agnostic, Python-based broker** that relays inbox records
between hosts — **no Docker required**, the same `send` / `inbox` commands you
already learned, just with the broker carrying messages across the network
instead of the local filesystem.

Until then, the single-machine floor is the supported path — and it's enough to
build and watch a real multi-agent collaboration.
