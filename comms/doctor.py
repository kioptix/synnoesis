#!/usr/bin/env python3
"""doctor.py — print Synnoesis's resolved config so silent drift is visible.

The #1 "the mesh is broken" footgun is ``PA_HOME`` / ``PA_AGENT_ID`` drifting
between shells: a fresh terminal that never re-exported them falls back to
``~/.synnoesis`` (a DIFFERENT keyring), so keys look missing and every peer
reads as ``no-key`` with no error pointing at the cause. This prints exactly what
the tools resolved — one command a confused user runs first to see where their
keys/keyring actually live and whether signing is even possible.

Pure stdlib; reports ``cryptography`` availability rather than requiring it.

Usage:
  python comms/doctor.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Local mesh libs live beside this script (sys.path.insert + bare import).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402  — portable data-home resolver
import sign   # noqa: E402  — key dir / keyring / crypto availability
import mqtt as mq  # noqa: E402  — broker config + security posture (v0.4.0)


def _probe(host: str, port: int, timeout: float) -> str:
    """Best-effort TCP reachability of the broker (connect only, no data). Never
    raises; a diagnostic must not hang or crash on an unreachable broker."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "yes (TCP connect ok)"
    except Exception as e:  # noqa: BLE001
        return f"no ({type(e).__name__})"


def _broker_section() -> None:
    """Print the cross-machine transport diagnostics (v0.4.0). Uses only the pure
    config/security helpers plus a short, non-fatal reachability probe."""
    cfg = mq.resolve_broker()
    if cfg is None:
        print("  broker       : (unset) — file transport only; "
              "set SYN_BROKER=host[:port] for cross-machine")
        return
    where = ("TLS" if cfg.use_tls else
             "loopback plaintext" if cfg.is_loopback else "PLAINTEXT")
    print(f"  broker       : {cfg.host}:{cfg.port}  ({where})")
    try:
        mq.check_transport_security(cfg, warn=lambda _m: None)
        posture = ("TLS-encrypted in transit" if cfg.use_tls else
                   "loopback (ok)" if cfg.is_loopback else
                   "plaintext ALLOWED (SYN_ALLOW_PLAINTEXT) — warns every connect")
    except mq.SynBrokerError:
        posture = ("REFUSED — off-box plaintext without TLS; set SYN_BROKER_TLS_CA "
                   "or SYN_ALLOW_PLAINTEXT")
    print(f"  transport sec: {posture}")
    print("  confidential.: NOT end-to-end encrypted — the broker sees message "
          "content (Synnoesis signs, it does not encrypt)")
    if cfg.user:
        print(f"  broker auth  : user={cfg.user}, passfile={cfg.passfile or '(none)'}")
    else:
        print("  broker auth  : (none)")
    try:
        mq.require_paho()
        print("  paho-mqtt    : available")
    except mq.SynBrokerError:
        print("  paho-mqtt    : MISSING — `pip install synnoesis[mqtt]` to use the broker")
    print(f"  reachable    : {_probe(cfg.host, cfg.port, 2.0)}")


def _presence_section(agent: str) -> None:
    """S5: this agent's OWN retained presence, as other agents currently see it.

    Reads the record rather than reporting what we believe we published — the whole
    failure mode presence has is a stale retained record that everyone else is still
    being served. Asking the broker is the only answer that means anything.
    """
    cfg = mq.resolve_broker()
    if cfg is None:
        return
    print("  --- presence ---")
    print("  durable recv : stable client-id + persistent session (messages sent "
          "while `listen` is down are queued by the broker)")
    print("  NOTE         : the durability window IS the freshness window — a "
          "message queued longer than SYN_MAX_AGE_SEC is dropped as stale on "
          "delivery. Raise it deliberately; it widens replay tolerance equally.")
    if not agent:
        print("  own state    : (no PA_AGENT_ID — cannot look up own presence)")
        return
    try:
        import presence  # noqa: PLC0415
        entry = presence.peek(cfg, agent, timeout=1.5)
    except Exception as e:  # noqa: BLE001 — a diagnostic must never crash
        print(f"  own state    : lookup failed ({e.__class__.__name__})")
        return
    if entry is None:
        print("  own state    : none retained — this agent has not published "
              "presence (run `synnoesis listen`)")
        return
    state = {True: "online", False: "offline"}.get(entry.get("online"), "unknown")
    print(f"  own state    : {state} via {entry.get('via') or '?'}, last said "
          f"{presence.age_str(entry.get('_at', ''))}  [verify="
          f"{entry.get('status')}]")
    if entry.get("status") == "bad":
        print("  ⚠ WARNING    : your own retained presence record FAILS signature "
              "verification — someone else may be publishing presence as you.")


def main(argv=None) -> int:
    home = paths.pa_home()
    from_env = bool((os.environ.get("PA_HOME") or "").strip())
    agent = (os.environ.get("PA_AGENT_ID") or "").strip()
    keys_dir = sign._mesh_keys_dir()
    keyring_path = keys_dir.parent / "mesh-keyring.json"
    try:
        n = len(sign.load_keyring_strict().get("agents") or {})
        keyring_state = f"{n} agent{'' if n == 1 else 's'}"
    except ValueError:
        keyring_state = "UNREADABLE/CORRUPT — back up and inspect this file"

    src = ("from env PA_HOME" if from_env
           else "default (~/.synnoesis); set PA_HOME to pin")
    print("Synnoesis doctor")
    print(f"  PA_HOME      : {home}  ({src})")
    print(f"  agent id     : {agent or '(unset — set PA_AGENT_ID)'}")
    if agent:
        priv = keys_dir / f"{agent}.key"
        print(f"  private key  : {'present' if priv.is_file() else 'ABSENT'}  "
              f"({priv})")
    print(f"  keyring      : {keyring_path}  ({keyring_state})")
    if sign.CRYPTO_AVAILABLE:
        print("  cryptography : available (signing + verification enabled)")
    else:
        print("  cryptography : MISSING — signing/verification disabled "
              "(warn-mode only)")
    enforce = (os.environ.get("SYN_ENFORCE_SIGNING") or "").strip() not in ("", "0")
    print(f"  enforce sign : {'ON (deliver ok-verified only)' if enforce else 'off (warn-mode)'}")
    _broker_section()
    _presence_section(agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
