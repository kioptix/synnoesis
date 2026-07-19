#!/usr/bin/env python3
"""test_listen_process.py — the receiver bridge's per-message decision logic AND the
file↔broker field-identity bet, tested WITHOUT a broker.

``listen.process_incoming`` is the real production decision (the paho callback just
wraps it with the actual file append), so exercising it directly IS testing the real
path, not a replica. Covers: local re-verify (ok/bad/no-key), enforce-mode drop,
nonce dedup, signed-``_at`` freshness (replay defense), malformed payload, and — the
core v0.4.0 design bet — that a broker-delivered record is field-identical to a file
record for the same inner envelope (same topic, same payload bytes, same verdict).

Redirects ``sign._mesh_keys_dir`` at a temp tree (the ``test_sign_verify_floor``
pattern) so no keys touch the working tree. SKIP + exit 0 if ``cryptography`` is
absent.

Run: ``python tests/test_listen_process.py``  (exit 0 = pass/skip, 1 = fail)
"""
from __future__ import annotations

import json
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_COMMS = Path(__file__).resolve().parents[1] / "comms"
if str(_COMMS) not in sys.path:
    sys.path.insert(0, str(_COMMS))

import sign     # noqa: E402
import wire     # noqa: E402
import listen   # noqa: E402

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _signed(priv_b64: str, sender: str, body: str, at: datetime) -> dict:
    """A validly-signed envelope with a caller-controlled _at (for freshness tests)."""
    msg = {"_urgency": "normal", "_from": sender, "_to": "bob",
           "_at": at.isoformat(), "_nonce": secrets.token_hex(8), "body": body}
    sign.attach_signature(msg, priv_b64, key_id=sender)
    return msg


def _payload(msg: dict) -> bytes:
    return json.dumps(msg, ensure_ascii=False).encode("utf-8")


def main() -> int:
    if not sign.CRYPTO_AVAILABLE:
        print("SKIP: cryptography not available; process_incoming statuses degrade "
              "to 'unavailable' (covered by the no-crypto floor test).")
        return 0

    failures: list[str] = []

    def check(label, fn):
        try:
            fn()
            print(f"PASS: {label}")
        except AssertionError as e:
            failures.append(label)
            print(f"FAIL: {label}\n      {e}")

    with tempfile.TemporaryDirectory(prefix="synnoesis-listen-") as td:
        tmp = Path(td)
        keys_dir = tmp / "mesh-keys"
        keys_dir.mkdir(parents=True)
        priv_b64, pub_b64 = sign.generate_keypair()
        (keys_dir / "alice.key").write_text(priv_b64, encoding="utf-8")
        (tmp / "mesh-keyring.json").write_text(
            json.dumps({"agents": {"alice": {"pubkey": pub_b64}}}), encoding="utf-8")
        orig = sign._mesh_keys_dir
        sign._mesh_keys_dir = lambda: keys_dir
        try:
            ring = sign.load_keyring()

            def valid_signed_appends_ok():
                msg = _signed(priv_b64, "alice", "hi", NOW)
                d = listen.process_incoming(_payload(msg), me="bob", keyring=ring,
                                            enforce=False, max_age_sec=300, seen={}, now=NOW)
                assert d.action == "append" and d.status == "ok", d
                assert set(d.outer) == {"topic", "received_at", "payload", "_verify"}
            check("valid signed → append, verify=ok", valid_signed_appends_ok)

            def tampered_is_bad_and_enforce_drops():
                msg = _signed(priv_b64, "alice", "hi", NOW)
                msg["body"] = "TAMPERED"                       # break the signature
                warn = listen.process_incoming(_payload(msg), me="bob", keyring=ring,
                                               enforce=False, max_age_sec=0, seen={}, now=NOW)
                assert warn.action == "append" and warn.status == "bad", warn
                enf = listen.process_incoming(_payload(msg), me="bob", keyring=ring,
                                              enforce=True, max_age_sec=0, seen={}, now=NOW)
                assert enf.action == "drop" and enf.status == "bad", enf
            check("tampered → bad; enforce drops it", tampered_is_bad_and_enforce_drops)

            def no_key_warn_vs_enforce():
                msg = _signed(priv_b64, "alice", "hi", NOW)
                empty = {"agents": {}}
                warn = listen.process_incoming(_payload(msg), me="bob", keyring=empty,
                                               enforce=False, max_age_sec=0, seen={}, now=NOW)
                assert warn.action == "append" and warn.status == "no-key", warn
                enf = listen.process_incoming(_payload(msg), me="bob", keyring=empty,
                                              enforce=True, max_age_sec=0, seen={}, now=NOW)
                assert enf.action == "drop", enf
            check("unknown sender → no-key (warn delivers, enforce drops)",
                  no_key_warn_vs_enforce)

            def dedup_drops_second():
                msg = _signed(priv_b64, "alice", "hi", NOW)
                seen: dict = {}
                a = listen.process_incoming(_payload(msg), me="bob", keyring=ring,
                                            enforce=False, max_age_sec=300, seen=seen, now=NOW)
                b = listen.process_incoming(_payload(msg), me="bob", keyring=ring,
                                            enforce=False, max_age_sec=300, seen=seen, now=NOW)
                assert a.action == "append" and b.action == "drop", (a, b)
                assert "duplicate" in b.reason
            check("qos1 duplicate nonce → second dropped", dedup_drops_second)

            def stale_is_replay_dropped():
                old = _signed(priv_b64, "alice", "old", NOW - timedelta(seconds=9999))
                drop = listen.process_incoming(_payload(old), me="bob", keyring=ring,
                                               enforce=False, max_age_sec=300, seen={}, now=NOW)
                assert drop.action == "drop" and "stale" in drop.reason, drop
                # a VALID signature, just old — freshness (not verify) is what drops it:
                ok = listen.process_incoming(_payload(old), me="bob", keyring=ring,
                                             enforce=False, max_age_sec=0, seen={}, now=NOW)
                assert ok.action == "append" and ok.status == "ok", ok
            check("stale signed message → dropped as replay (max_age=0 delivers)",
                  stale_is_replay_dropped)

            def malformed_dropped():
                d = listen.process_incoming(b"not json at all", me="bob", keyring=ring,
                                            enforce=False, max_age_sec=0, seen={}, now=NOW)
                assert d.action == "drop", d
            check("malformed payload → dropped", malformed_dropped)

            def field_identity_file_vs_broker():
                """THE design bet: same inner ⇒ identical outer record shape/content
                (topic, payload bytes, verdict) whether written by the file path or by
                the broker path. received_at legitimately differs (delivery clock)."""
                inner = sign.new_message("alice", "bob", "identical?")
                fs, fd = sign.verify_against_keyring(inner, ring,
                                                     expected_tag=sign.DS_TAG_ENVELOPE)
                file_outer = wire.wrap_outer(inner, fs, fd)
                d = listen.process_incoming(_payload(inner), me="bob", keyring=ring,
                                            enforce=False, max_age_sec=0, seen={}, now=NOW)
                mqtt_outer = d.outer
                assert set(file_outer) == set(mqtt_outer), "outer key set drifted"
                assert file_outer["topic"] == mqtt_outer["topic"], "topic drift"
                assert file_outer["payload"] == mqtt_outer["payload"], \
                    "payload bytes differ — signature would not survive"
                assert file_outer["_verify"] == mqtt_outer["_verify"], "verdict drift"
            check("file record ≡ broker record (field-identity)",
                  field_identity_file_vs_broker)

            def dedup_cache_hard_capped():
                """A unique-nonce flood must not grow the seen-cache without bound: the
                hard cap (SEEN_MAX) trims it. Freshness (signed _at) stays the real
                replay defense, so the trim is safe. Temporarily shrink SEEN_MAX so the
                test is fast + deterministic."""
                orig_max = listen.SEEN_MAX
                listen.SEEN_MAX = 5
                try:
                    seen: dict = {}
                    for i in range(30):                    # 30 distinct nonces (secrets.token_hex)
                        m = _signed(priv_b64, "alice", f"flood-{i}", NOW)
                        listen.process_incoming(_payload(m), me="bob", keyring=ring,
                                                enforce=False, max_age_sec=0,
                                                seen=seen, now=NOW)
                    # cap trims BEFORE recording the new nonce, so the steady-state bound
                    # is SEEN_MAX + 1 — the point is it stays bounded, not that it grew to 30.
                    assert len(seen) <= listen.SEEN_MAX + 1, \
                        f"seen cache exceeded cap: {len(seen)} (SEEN_MAX={listen.SEEN_MAX})"
                finally:
                    listen.SEEN_MAX = orig_max
            check("dedup cache is hard-capped under a unique-nonce flood",
                  dedup_cache_hard_capped)
        finally:
            sign._mesh_keys_dir = orig

    print("-" * 64)
    if failures:
        print(f"RESULT: FAIL — {len(failures)} failed: {', '.join(failures)}")
        return 1
    print("RESULT: PASS — listen decision logic + file↔broker field-identity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
