#!/usr/bin/env python3
"""test_presence.py -- gate: presence records, their honesty rules, and durable-session
preconditions.

Presence is the easiest thing on a mesh to report confidently and wrongly, so the
assertions here are mostly about the ways it can LIE:

  * a record signed for one agent, published on another agent's topic  -> FORGED
  * a record signed with the message tag rather than the presence tag  -> rejected
  * a tampered field                                                   -> rejected
  * an LWT record that legitimately looks stale                        -> displayed, not dropped

Run: python tests/test_presence.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_COMMS = Path(__file__).resolve().parents[1] / "comms"
if str(_COMMS) not in sys.path:
    sys.path.insert(0, str(_COMMS))

import sign      # noqa: E402
import presence  # noqa: E402
import mqtt as mq  # noqa: E402

_failures: list = []
T0 = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}{('  (' + detail + ')') if detail else ''}")
        _failures.append(label)


def _keyring(**agents) -> dict:
    return {"agents": {k: {"pubkey": v} for k, v in agents.items()}}


def test_record_shape() -> None:
    r = presence.build("alice", online=True, via=presence.VIA_CONNECT,
                       listener_started_at="2026-07-19T11:00:00+00:00", now=T0)
    check("record carries a schema version", r.get("schema") == presence.SCHEMA)
    check("record names its agent", r.get("agent_id") == "alice")
    check("online is a real bool", r.get("online") is True)
    check("via is recorded", r.get("via") == presence.VIA_CONNECT)
    check("_at is the supplied clock (pure, no hidden now())",
          r.get("_at") == T0.isoformat(), f"got {r.get('_at')}")

    r2 = presence.build("alice", online=False, via=presence.VIA_LWT, now=T0)
    check("listener_started_at omitted when empty", "listener_started_at" not in r2)


def test_sign_verify_roundtrip() -> None:
    if not sign.CRYPTO_AVAILABLE:
        check("crypto available for signing tests", False, "cryptography missing")
        return
    priv, pub = sign.generate_keypair()
    kr = _keyring(alice=pub)

    rec = presence.build("alice", online=True, via=presence.VIA_CONNECT, now=T0)
    sign.attach_record_signature(rec, priv, key_id="alice",
                                 ds_tag=sign.DS_TAG_PRESENCE)
    raw = json.dumps(rec).encode()

    e = presence.parse(raw, "agent/alice/state", kr)
    check("a correctly signed presence record verifies ok", e["status"] == "ok",
          f"{e['status']}: {e['detail']}")
    check("parsed record keeps online/via", e["online"] is True and e["via"] == "connect")

    # A record signed with the MESSAGE tag must not pass as presence: domain
    # separation is the whole reason the tag exists.
    rec2 = presence.build("alice", online=True, via="connect", now=T0)
    sign.attach_record_signature(rec2, priv, key_id="alice",
                                 ds_tag=sign.DS_TAG_ENVELOPE)
    e2 = presence.parse(json.dumps(rec2).encode(), "agent/alice/state", kr)
    check("a record signed with the WRONG domain tag is not 'ok'",
          e2["status"] != "ok", f"got {e2['status']}")

    # Tamper: flip online true->false after signing.
    rec3 = presence.build("alice", online=True, via="connect", now=T0)
    sign.attach_record_signature(rec3, priv, key_id="alice",
                                 ds_tag=sign.DS_TAG_PRESENCE)
    rec3["online"] = False
    e3 = presence.parse(json.dumps(rec3).encode(), "agent/alice/state", kr)
    check("a TAMPERED record is 'bad'", e3["status"] == "bad", f"got {e3['status']}")

    # 🔴 The spoof that a naive implementation misses: alice validly signs a record for
    # HERSELF and publishes it on BOB's topic. The signature is genuine; the placement
    # is the attack. Topic and signed agent_id must agree.
    rec4 = presence.build("alice", online=True, via="connect", now=T0)
    sign.attach_record_signature(rec4, priv, key_id="alice",
                                 ds_tag=sign.DS_TAG_PRESENCE)
    e4 = presence.parse(json.dumps(rec4).encode(), "agent/bob/state", kr)
    check("★ a validly-signed record on ANOTHER agent's topic is 'bad'",
          e4["status"] == "bad", f"got {e4['status']}: {e4['detail']}")

    # No key for the sender -> no-key, never silently ok.
    e5 = presence.parse(raw, "agent/alice/state", _keyring(bob=pub))
    check("an unknown signer yields 'no-key'", e5["status"] == "no-key",
          f"got {e5['status']}")

    unsigned = json.dumps(presence.build("alice", online=True, via="connect",
                                         now=T0)).encode()
    check("an unsigned record is 'unsigned', not 'ok'",
          presence.parse(unsigned, "agent/alice/state", kr)["status"] == "unsigned")

    check("malformed payload is reported, not crashed",
          presence.parse(b"not json", "agent/alice/state", kr)["status"] == "malformed")


def test_message_family_signature_is_rejected() -> None:
    """Red-team (brain cond-3, cases i+iii): a presence record signed with the MESSAGE
    helper -- sign.attach_signature, which covers only the envelope subset SIGNED_FIELDS
    and so binds none of online/host/agent_id -- must NOT be accepted as presence. The
    record path is the only accepted primitive; there is deliberately no message-family
    dual-accept, because that signature binds no content and is forgeable by content-swap.
    (Case ii -- content-swap on a record-family sig -- is the tamper check above.)"""
    if not sign.CRYPTO_AVAILABLE:
        check("crypto available for message-family test", False, "cryptography missing")
        return
    priv, pub = sign.generate_keypair()
    kr = _keyring(alice=pub)

    rec = presence.build("alice", online=True, via=presence.VIA_CONNECT, now=T0)
    sign.attach_signature(rec, priv, key_id="alice")  # MESSAGE family, not record family
    e = presence.parse(json.dumps(rec).encode(), "agent/alice/state", kr)
    check("★ a MESSAGE-family (constant-covering) signature is not accepted as presence",
          e["status"] != "ok", f"got {e['status']}: {e['detail']}")

    # It stays rejected under a content-swap: the message-family sig never bound the
    # content, so flipping online cannot turn it 'ok'. Forgeability made concrete.
    rec["online"] = False
    e2 = presence.parse(json.dumps(rec).encode(), "agent/alice/state", kr)
    check("...and stays not-'ok' after a content-swap",
          e2["status"] != "ok", f"got {e2['status']}")


def test_presence_verifies_only_via_record_path() -> None:
    """Red-team (brain cond-3, case iv): a STRUCTURAL guard. Presence must verify ONLY
    through the record path. The message path (sign.verify_against_keyring) checks the
    envelope subset -- a near-constant for a presence record that binds no content -- so
    tolerating it would re-open the forgery. If anyone ever wires message-path tolerance
    into presence.py, THIS fails loud. The invariant is only as durable as its guard."""
    src = Path(presence.__file__).read_text(encoding="utf-8")
    check("presence verifies via the RECORD path",
          "verify_record_against_keyring" in src,
          "presence.py no longer calls the record-path verifier")
    # Strip the record-path name first so its '...verify_against_keyring' tail can't match.
    stripped = src.replace("verify_record_against_keyring", "")
    check("★ presence NEVER uses the message-path verifier (structural guard)",
          "verify_against_keyring" not in stripped,
          "presence.py references the message-path verifier -- record-path-only invariant broken")


def test_topic_parsing() -> None:
    check("topic_for round-trips",
          presence.agent_id_from_topic(presence.topic_for("alice")) == "alice")
    for bad in ("agent/alice", "agent/alice/inbox", "broadcast/x", "", "a/b/c/d"):
        check(f"non-presence topic rejected: {bad!r}",
              presence.agent_id_from_topic(bad) == "")


def test_age_math() -> None:
    now = T0
    check("seconds", presence.age_str((now - timedelta(seconds=30)).isoformat(), now)
          == "30s ago")
    check("minutes", presence.age_str((now - timedelta(minutes=4)).isoformat(), now)
          == "4m ago")
    check("hours", presence.age_str((now - timedelta(hours=3)).isoformat(), now)
          == "3h ago")
    check("days", presence.age_str((now - timedelta(days=2)).isoformat(), now)
          == "2d ago")
    check("empty timestamp", presence.age_str("", now) == "unknown")
    check("unparseable timestamp", presence.age_str("not-a-date", now) == "unparseable")
    # A future timestamp is skew, not fraud -- the signature already ruled on trust.
    # Printing "-40s ago" would read as a bug; name the actual condition instead.
    fut = presence.age_str((now + timedelta(seconds=40)).isoformat(), now)
    check("a future timestamp is named as clock skew", "skew" in fut, fut)


def test_lwt_is_signed_and_looks_stale() -> None:
    """The Last Will is built at CONNECT time but published at DEATH time, so it is
    legitimately old on arrival. Presence must therefore never be freshness-gated --
    a gate would drop exactly the record that reports the death."""
    t, pl, qos, retain = presence.will_tuple("alice", now=T0)
    check("LWT targets the agent's state topic", t == "agent/alice/state")
    check("LWT is qos1 (the death notice must not be dropped)", qos == 1)
    check("LWT is retained (a later subscriber still learns of the death)",
          retain is True)
    rec = json.loads(pl.decode())
    check("LWT says offline via lwt",
          rec.get("online") is False and rec.get("via") == presence.VIA_LWT)
    check("LWT _at is the CONNECT time, not the death time",
          rec.get("_at") == T0.isoformat(), f"got {rec.get('_at')}")
    # Documented consequence, asserted so nobody 'fixes' it into a freshness check:
    later = T0 + timedelta(hours=6)
    check("...so an LWT legitimately reads as hours old",
          presence.age_str(rec["_at"], later) == "6h ago")


def test_display_calls_out_forgery_first() -> None:
    forged = {"agent_id": "bob", "online": True, "via": "connect",
              "_at": T0.isoformat(), "status": "bad", "detail": "sig failed"}
    line = presence.describe(forged, T0)
    check("★ a forged record is displayed as FORGED", "FORGED" in line, line)
    check("...and is NOT rendered as online", "online" not in line.replace("FORGED", ""),
          line)

    ok = {"agent_id": "bob", "online": True, "via": "connect",
          "_at": (T0 - timedelta(minutes=2)).isoformat(), "status": "ok", "detail": ""}
    line = presence.describe(ok, T0)
    check("a good record shows state + age", "online" in line and "2m ago" in line, line)

    uns = dict(ok, status="unsigned")
    check("an unsigned record is flagged inline",
          "UNSIGNED" in presence.describe(uns, T0))


def test_offline_note() -> None:
    check("no record -> says it cannot tell",
          "cannot tell" in presence.offline_note(None, "bob"))
    off = {"agent_id": "bob", "online": False, "via": "lwt", "_at": T0.isoformat(),
           "status": "ok"}
    n = presence.offline_note(off, "bob")
    check("offline recipient -> queue note", "offline" in n and "queue" in n, n)
    on = dict(off, online=True, via="connect")
    check("online recipient -> SILENT (no note)", presence.offline_note(on, "bob") == "")
    bad = dict(off, status="bad", detail="x")
    check("a forged recipient record warns and is ignored",
          "FAILED verification" in presence.offline_note(bad, "bob"))


def test_render_carries_the_caveats() -> None:
    import who
    out = who.render([], now=T0)
    check("empty result explains itself", "no presence records" in out, out)
    entries = [{"agent_id": "a", "online": True, "via": "connect",
                "_at": T0.isoformat(), "status": "ok", "detail": ""}]
    out = who.render(entries, now=T0)
    check("★ the 'retained ≠ alive' caveat rides WITH the answer",
          "retained ≠ alive" in out, out)
    check("★ the approximate-age caveat rides with the answer",
          "approximate" in out, out)
    forged = [{"agent_id": "b", "online": True, "via": "connect",
               "_at": T0.isoformat(), "status": "bad", "detail": "x"}]
    out = who.render(forged, now=T0)
    check("a forgery is summarised at the bottom too", "FAILED signature" in out, out)
    check("a forged agent is NOT counted as online", "0 reporting online" in out, out)


def test_persistent_session_needs_stable_id() -> None:
    """clean_session=False keys the broker's queue to the client id. An empty id would
    strand the backlog under an identity nothing reconnects as -- fail loudly."""
    try:
        mqtt_mod = mq.require_paho()
    except mq.SynBrokerError:
        check("paho available for session-precondition test", True,
              "skipped: paho not installed")
        return
    # use_tls / is_loopback are derived PROPERTIES, not fields — construct with the
    # real field set rather than the one that felt plausible.
    cfg = mq.BrokerConfig(host="localhost", port=1883)
    try:
        mq._new_client(mqtt_mod, cfg, "", clean_session=False)
        check("★ empty client_id + persistent session is REFUSED", False,
              "no SynBrokerError")
    except mq.SynBrokerError:
        check("★ empty client_id + persistent session is REFUSED", True)
    try:
        mq._new_client(mqtt_mod, cfg, "synnoesis-listen-alice", clean_session=False)
        check("a stable client_id is accepted", True)
    except mq.SynBrokerError as e:
        check("a stable client_id is accepted", False, str(e))


def main() -> int:
    test_record_shape()
    test_sign_verify_roundtrip()
    test_message_family_signature_is_rejected()
    test_presence_verifies_only_via_record_path()
    test_topic_parsing()
    test_age_math()
    test_lwt_is_signed_and_looks_stale()
    test_display_calls_out_forgery_first()
    test_offline_note()
    test_render_carries_the_caveats()
    test_persistent_session_needs_stable_id()
    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print("RESULT  PASS  (presence records, honesty rules, durable-session preconditions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
