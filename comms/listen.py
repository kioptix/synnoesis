#!/usr/bin/env python3
"""listen.py — the cross-machine RECEIVER bridge for the Synnoesis mesh (v0.4.0).

The other half of the MQTT transport. ``send.py`` PUBLISHES a signed inner envelope
to ``agent/<to>/inbox`` on a broker; ``listen`` runs on the RECIPIENT's machine,
subscribes that topic, and turns each received envelope into the SAME on-disk inbox
record the file transport writes — so ``read`` (``inbox.py``) is unchanged and never
has to know whether a record arrived by file or by broker.

★ listen IS THE TRUST BOUNDARY. ``inbox.py`` documents a SECURITY INVARIANT: its
enforce-mode gate trusts the ``_verify`` status stamped by whatever WROTE the record,
which is only safe when the writer is local + trusted — "ANY cross-machine / remote-
writer read path MUST locally re-parse the payload and re-verify the signature
against ITS OWN keyring, and gate on THAT result — never on a writer-supplied
verify." This bridge is exactly that: it re-verifies every received envelope against
the LOCAL keyring and stamps its OWN verdict via ``wire.wrap_outer``. The record
``inbox.py`` later reads was therefore written by this LOCAL trusted bridge, so the
existing read-side gate stays sound. The broker's and the sender's claims are never
trusted — only this machine's local verification.

Replay defense (two layers, composed):
  * NONCE dedup — a bounded seen-``(_from,_nonce)`` cache drops qos1 broker duplicates
    and naive replays WITHIN the window.
  * ``_at`` FRESHNESS — ``_at`` is a SIGNED field, so an attacker cannot forge a fresh
    timestamp without the sender's key. A verified message older than ``SYN_MAX_AGE_SEC``
    is dropped, catching replays BEYOND the dedup window. Honest bound: the defense is
    "no replay older than N", not "replay-proof". Set ``SYN_MAX_AGE_SEC=0`` to disable
    (e.g. when machine clocks cannot be kept within the window).

Foreground process (Ctrl-C to stop). Running it under systemd / launchd / a Windows
scheduled task is the operator's choice — supervision is deliberately out of scope
for v0.4.0 (one concern per release).

Requires the optional MQTT dependency: ``pip install synnoesis[mqtt]``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sign     # noqa: E402  — Ed25519 verify + keyring + DS_TAG_ENVELOPE
import wire     # noqa: E402  — the ONE outer-record builder (field-identity)
import paths    # noqa: E402  — portable data-home resolver
import mqtt as mq  # noqa: E402  — broker transport + security floor
import presence  # noqa: E402  — retained, record-signed online/offline state

# A message's nonce is always deduped for at least this long, even when freshness
# is disabled, so qos1 broker duplicates (delivered seconds apart) never double-write.
DEDUP_MIN_TTL_SEC = 120

# Hard cap on the seen-nonce cache. Expiry-based eviction already bounds it to ~one
# window of normal traffic, but a FLOOD of unique-nonce junk (dedup runs before verify)
# could grow it within a single window. This caps memory outright. Safe by design: the
# signed-_at FRESHNESS check is the real replay defense, so trimming the cache can never
# admit an OLD replay — at worst, under an extreme flood, a WITHIN-window duplicate could
# be re-processed. Bounding memory beats that rare, harmless case.
SEEN_MAX = 10000


def _parse_at(s) -> datetime | None:
    """Parse a signed ``_at`` ISO-8601 string to a tz-aware datetime (naive ⇒ UTC).
    Returns None on anything unparseable — freshness then simply doesn't apply."""
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@dataclass
class Decision:
    action: str            # 'append' or 'drop'
    reason: str            # human-readable why
    status: str = ""       # local verify verdict (ok/unsigned/no-key/bad/unavailable)
    sender: str = "?"      # claimed _from
    outer: dict | None = None   # the record to append (action == 'append')


def process_incoming(payload: bytes, *, me: str, keyring: dict, enforce: bool,
                     max_age_sec: int, seen: dict, now: datetime) -> Decision:
    """Pure per-message decision — NO paho, NO file I/O. This is the real production
    logic (the paho callback just wraps it with the actual append), so tests exercise
    it directly rather than a replica.

    Order: parse → dedup → freshness → LOCAL verify → enforce-gate → wrap.
    ``seen`` is mutated in place (bounded seen-nonce cache; expiries are datetimes).
    """
    try:
        inner = json.loads(payload.decode("utf-8"))
    except Exception:  # noqa: BLE001 — malformed publish
        return Decision("drop", "payload is not valid JSON")
    if not isinstance(inner, dict):
        return Decision("drop", "payload is not a JSON object")

    sender = inner.get("_from") or "?"
    nonce = inner.get("_nonce") or ""

    # --- dedup: evict expired, then check-or-record (qos1 dups + in-window replay) ---
    for k in [k for k, exp in seen.items() if exp <= now]:
        del seen[k]
    if len(seen) > SEEN_MAX:
        # oversized after expiry-eviction (a unique-nonce flood): drop the soonest-to-
        # expire entries down to the cap. See SEEN_MAX for why this is replay-safe.
        for k, _exp in sorted(seen.items(), key=lambda kv: kv[1])[:len(seen) - SEEN_MAX]:
            del seen[k]
    if nonce:
        key = (sender, nonce)
        if key in seen:
            return Decision("drop", "duplicate nonce (qos1 dup or in-window replay)",
                            sender=sender)
        ttl = max(max_age_sec, DEDUP_MIN_TTL_SEC)
        seen[key] = now + timedelta(seconds=ttl)

    # --- freshness: a signed _at can't be forged fresh; drop replays beyond window ---
    if max_age_sec > 0:
        at = _parse_at(inner.get("_at"))
        if at is not None:
            age = (now - at).total_seconds()
            if age > max_age_sec:
                return Decision(
                    "drop",
                    f"stale message (age {int(age)}s > SYN_MAX_AGE_SEC {max_age_sec}s) "
                    "— replay defense", sender=sender)

    # --- LOCAL re-verify against THIS machine's keyring (the trust boundary) ---
    status, detail = sign.verify_against_keyring(
        inner, keyring, expected_tag=sign.DS_TAG_ENVELOPE)

    # --- enforce gate: deliver only 'ok' when SYN_ENFORCE_SIGNING is set ---
    if enforce and status != "ok":
        return Decision("drop", f"enforce mode: verify={status} ({detail})",
                        status=status, sender=sender)

    outer = wire.wrap_outer(inner, status, detail)
    return Decision("append", "delivered", status=status, sender=sender, outer=outer)


def _resolve_me(explicit: str | None) -> str:
    """Who am I listening AS? Explicit flag wins; else reuse send.py's resolver
    (PA_AGENT_ID → nearest .agent-id marker → hostname), so listen and send agree."""
    if explicit and explicit.strip():
        return explicit.strip()
    import send  # noqa: PLC0415 — reuse the one identity resolver
    return send._resolve_agent_id()


def run(me: str, *, max_age_sec: int, enforce: bool) -> int:
    cfg = mq.resolve_broker()
    if cfg is None:
        print("error: listen requires a broker — set SYN_BROKER=host[:port]. "
              "(The file transport needs no listener: send.py writes records "
              "directly to the shared inbox.)", file=sys.stderr)
        return 2
    # Fail LOUD, not silent: enforce without crypto would reject 100% of traffic
    # (absence-masquerading-as-success). Mirror inbox.py's startup guard.
    if enforce and not sign.CRYPTO_AVAILABLE:
        print("error: SYN_ENFORCE_SIGNING is set but 'cryptography' is not installed "
              "— cannot verify signatures; install cryptography or unset the flag.",
              file=sys.stderr)
        return 1

    topic = f"agent/{me}/inbox"
    inbox_path = paths.service_dir("comms", create=True) / f"{me}-inbox.jsonl"
    seen: dict = {}
    started_at = datetime.now(timezone.utc).isoformat()

    def on_message(_client, _userdata, message):
        keyring = sign.load_keyring()          # reload per message ⇒ new keys w/o restart
        now = datetime.now(timezone.utc)
        d = process_incoming(message.payload, me=me, keyring=keyring, enforce=enforce,
                             max_age_sec=max_age_sec, seen=seen, now=now)
        if d.action == "append":
            try:
                with inbox_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(d.outer, ensure_ascii=False) + "\n")
            except OSError as e:
                print(f"error: could not append to {inbox_path}: {e}", file=sys.stderr)
                return
            print(f"← {d.sender} (verify={d.status}) → appended to {inbox_path.name}")
        else:
            print(f"· dropped from {d.sender}: {d.reason}")

    holder = {}

    def on_ready(host, top, session_present=None):
        mode = "ENFORCE (ok-only)" if enforce else "warn (deliver + mark)"
        fresh = f"{max_age_sec}s" if max_age_sec > 0 else "disabled"
        print(f"listening as {me!r} on {host} [{top}] — signing: {mode}, "
              f"freshness: {fresh}. Ctrl-C to stop.")
        if session_present is not None:
            # "The broker had a session for me" and "the broker had nothing" are
            # indistinguishable without this line, and they mean very different things
            # for whether a backlog is about to arrive.
            print("  durable session: RESUMED — any messages sent while this listener "
                  "was down are being delivered now."
                  if session_present else
                  "  durable session: NEW — the broker held no prior session, so "
                  "nothing was queued for this agent.")
        # Announce ONLINE on the live client — no second connection, and it rides the
        # session we just established. Retained so a peer connecting later still sees it.
        c = holder.get("client")
        if c is not None:
            try:
                c.publish(presence.topic_for(me),
                          presence.payload(me, online=True, via=presence.VIA_CONNECT,
                                           listener_started_at=started_at),
                          qos=1, retain=True)
            except Exception as e:  # noqa: BLE001 — presence must never kill the listener
                print(f"  warning: could not publish presence: {e}", file=sys.stderr)

    try:
        client = mq.open_subscriber(
            cfg, topic, on_message,
            client_id=f"synnoesis-listen-{me}", on_ready=on_ready,
            # DURABLE DELIVERY: a stable client-id plus a persistent session means the
            # broker queues qos1 messages published while this listener is down.
            # NOTE the durability window is the FRESHNESS window: a message queued
            # longer than SYN_MAX_AGE_SEC is dropped as stale on delivery. Raise it
            # deliberately if listeners are expected to be down longer -- and accept
            # the correspondingly wider replay tolerance.
            clean_session=False,
            will=presence.will_tuple(me))
        holder["client"] = client
    except mq.SynBrokerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        # Clean shutdown: say so explicitly, so peers can tell "went away on purpose"
        # from "died" (which the broker reports via the Last Will instead).
        try:
            client.publish(presence.topic_for(me),
                           presence.payload(me, online=False,
                                            via=presence.VIA_CLEAN_SHUTDOWN),
                           qos=1, retain=True).wait_for_publish(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="listen",
        description="receive signed mesh messages from a broker and deliver them to "
                    "this agent's inbox (the cross-machine RECEIVER bridge)")
    ap.add_argument("--agent-id", default=None,
                    help="agent to listen AS (default: $PA_AGENT_ID / .agent-id / hostname)")
    ap.add_argument("--max-age-sec", type=int,
                    default=int(os.environ.get("SYN_MAX_AGE_SEC", "300")),
                    help="drop verified messages older than this many seconds "
                         "(replay defense; 0 disables). Default: SYN_MAX_AGE_SEC or 300.")
    args = ap.parse_args(argv)
    me = _resolve_me(args.agent_id)
    enforce = (os.environ.get("SYN_ENFORCE_SIGNING") or "").strip() not in ("", "0")
    return run(me, max_age_sec=args.max_age_sec, enforce=enforce)


if __name__ == "__main__":
    sys.exit(main())
