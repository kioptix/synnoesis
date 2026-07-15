#!/usr/bin/env python3
"""test_roundtrip_mqtt.py — REAL end-to-end cross-machine round-trip over a broker.

The honest integration proof: ``send`` publishes to a real MQTT broker, a real
``listen`` process receives + locally verifies + writes the inbox record, and ``read``
shows it with ``verify=ok`` — the whole v0.4.0 path, no mocks. Both roles run on one
host sharing a temp PA_HOME (so bob's keyring holds alice's pubkey); the transport is
genuinely the broker.

GATED so ordinary CI / laptop runs (no broker) stay green:
  * SKIP unless ``SYN_TEST_BROKER=host[:port]`` is set (CI sets it to its Mosquitto
    service; a dev sets it to a local broker).
  * SKIP if ``paho-mqtt`` or ``cryptography`` is not installed.

Run: ``SYN_TEST_BROKER=127.0.0.1:1883 python tests/test_roundtrip_mqtt.py``
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_COMMS = _REPO / "comms"
if str(_COMMS) not in sys.path:
    sys.path.insert(0, str(_COMMS))


def _skip(reason: str) -> int:
    print(f"SKIP: {reason}")
    return 0


def main() -> int:
    broker = (os.environ.get("SYN_TEST_BROKER") or "").strip()
    if not broker:
        return _skip("SYN_TEST_BROKER not set — no live broker to test against.")
    try:
        import paho.mqtt.client  # noqa: F401
    except Exception:
        return _skip("paho-mqtt not installed (pip install synnoesis[mqtt]).")
    import sign
    if not sign.CRYPTO_AVAILABLE:
        return _skip("cryptography not installed — cannot get a verify=ok round-trip.")

    with tempfile.TemporaryDirectory(prefix="synnoesis-rt-") as td:
        home = Path(td)
        keys = home / "mesh-keys"
        keys.mkdir(parents=True)
        # alice's key lives here; bob's keyring (same PA_HOME) holds alice's pubkey.
        priv, pub = sign.generate_keypair()
        (keys / "alice.key").write_text(priv, encoding="utf-8")
        (home / "mesh-keyring.json").write_text(
            json.dumps({"agents": {"alice": {"pubkey": pub}}}), encoding="utf-8")

        base_env = dict(os.environ)
        base_env["PA_HOME"] = str(home)
        base_env["SYN_BROKER"] = broker
        # loopback brokers are plaintext-ok; a non-loopback test broker must supply
        # its own SYN_BROKER_TLS_CA / SYN_ALLOW_PLAINTEXT via the ambient env.

        listen_env = dict(base_env)
        recv = subprocess.Popen(
            [sys.executable, str(_COMMS / "listen.py"), "--agent-id", "bob"],
            env=listen_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            time.sleep(2.5)                       # let it connect + subscribe
            if recv.poll() is not None:
                out = recv.stdout.read() if recv.stdout else ""
                print(f"FAIL: listen exited early (rc={recv.returncode}):\n{out}")
                return 1

            send_env = dict(base_env)
            send_env["PA_AGENT_ID"] = "alice"      # _from = alice
            sr = subprocess.run(
                [sys.executable, str(_COMMS / "send.py"), "--to", "bob", "roundtrip-body"],
                env=send_env, capture_output=True, text=True, timeout=30)
            if sr.returncode != 0:
                print(f"FAIL: send failed (rc={sr.returncode}): {sr.stdout}\n{sr.stderr}")
                return 1
            assert "published ->" in sr.stdout, f"send did not take the broker path: {sr.stdout}"

            inbox = home / "comms" / "bob-inbox.jsonl"
            deadline = time.time() + 10
            line = None
            while time.time() < deadline:
                if inbox.exists():
                    lines = inbox.read_text(encoding="utf-8").splitlines()
                    if lines:
                        line = lines[-1]
                        break
                time.sleep(0.3)
            if not line:
                out = ""
                try:
                    recv.terminate(); out = recv.communicate(timeout=5)[0] or ""
                except Exception:
                    pass
                print(f"FAIL: no record delivered to {inbox} within 10s.\nlisten output:\n{out}")
                return 1

            rec = json.loads(line)
            inner = json.loads(rec["payload"])
            assert inner["_from"] == "alice" and inner["body"] == "roundtrip-body", inner
            assert rec["_verify"]["status"] == "ok", (
                f"expected verify=ok (local re-verify), got {rec['_verify']}")
            assert rec["topic"] == "agent/bob/inbox", rec["topic"]
            print(f"PASS: real broker round-trip — alice → {broker} → bob, verify=ok.")
            return 0
        finally:
            try:
                recv.terminate()
                recv.communicate(timeout=5)
            except Exception:
                try:
                    recv.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())
