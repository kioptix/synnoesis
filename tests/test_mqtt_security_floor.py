#!/usr/bin/env python3
"""test_mqtt_security_floor.py — the v0.4.0 broker security floor, tested WITHOUT a
broker or paho.

All of ``comms/mqtt.py``'s config + security logic is pure (no I/O, no paho), so the
part a security audience would grill — plaintext refusal, transport selection, passfile
handling — is unit-testable with nothing installed. That is deliberate: the floor's
guarantees are proven by code that runs in CI on a bare interpreter.

Run: ``python tests/test_mqtt_security_floor.py``  (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_COMMS = Path(__file__).resolve().parents[1] / "comms"
if str(_COMMS) not in sys.path:
    sys.path.insert(0, str(_COMMS))

import mqtt as mq  # noqa: E402


def test_resolve_unset_is_file_transport() -> None:
    assert mq.resolve_broker({}) is None, "no SYN_BROKER must mean the file transport"


def test_resolve_hostport_and_default_ports() -> None:
    c = mq.resolve_broker({"SYN_BROKER": "broker.example:1884"})
    assert (c.host, c.port) == ("broker.example", 1884)
    # default port: 1883 plaintext, 8883 when a TLS CA is configured
    assert mq.resolve_broker({"SYN_BROKER": "h"}).port == 1883
    tls = mq.resolve_broker({"SYN_BROKER": "h", "SYN_BROKER_TLS_CA": "/tmp/ca.pem"})
    assert tls.port == 8883 and tls.use_tls is True


def test_resolve_ipv6_bracket() -> None:
    c = mq.resolve_broker({"SYN_BROKER": "[::1]:8883"})
    assert (c.host, c.port) == ("::1", 8883)
    assert c.is_loopback is True


def test_loopback_plaintext_allowed() -> None:
    for h in ("127.0.0.1", "localhost", "::1"):
        cfg = mq.resolve_broker({"SYN_BROKER": h})
        mq.check_transport_security(cfg, warn=_boom)   # must NOT warn or raise


def test_offbox_plaintext_refused() -> None:
    cfg = mq.resolve_broker({"SYN_BROKER": "10.0.0.9:1883"})
    try:
        mq.check_transport_security(cfg, warn=lambda _m: None)
    except mq.SynBrokerError as e:
        assert "PLAINTEXT" in str(e) and "SYN_ALLOW_PLAINTEXT" in str(e)
        # the honesty note (signing != encryption) must be present in the refusal text
        assert "does not encrypt" in str(e) or "confidentiality" in str(e)
        return
    raise AssertionError("off-box plaintext without TLS must be REFUSED")


def test_offbox_plaintext_allowed_warns_every_time() -> None:
    cfg = mq.resolve_broker({"SYN_BROKER": "10.0.0.9:1883", "SYN_ALLOW_PLAINTEXT": "1"})
    warns: list[str] = []
    mq.check_transport_security(cfg, warn=warns.append)
    mq.check_transport_security(cfg, warn=warns.append)
    assert len(warns) == 2, "SYN_ALLOW_PLAINTEXT must warn on EVERY connect, not once"
    assert "WITHOUT TLS" in warns[0]


def test_offbox_tls_ok() -> None:
    cfg = mq.resolve_broker({"SYN_BROKER": "10.0.0.9:8883", "SYN_BROKER_TLS_CA": "/x/ca.pem"})
    mq.check_transport_security(cfg, warn=_boom)   # TLS ⇒ no warn, no raise


def test_select_transport() -> None:
    assert mq.select_transport(local=True, via="auto", broker_configured=True) == "file"
    assert mq.select_transport(local=False, via="auto", broker_configured=True) == "mqtt"
    assert mq.select_transport(local=False, via="auto", broker_configured=False) == "file"
    assert mq.select_transport(local=False, via="file", broker_configured=True) == "file"
    assert mq.select_transport(local=False, via="mqtt", broker_configured=True) == "mqtt"
    try:
        mq.select_transport(local=False, via="mqtt", broker_configured=False)
    except mq.SynBrokerError:
        pass
    else:
        raise AssertionError("--via mqtt with no SYN_BROKER must ERROR, not fall back")


def test_read_passfile() -> None:
    with tempfile.TemporaryDirectory() as td:
        good = Path(td) / "pass"
        good.write_text("s3cret\n", encoding="utf-8")
        assert mq.read_passfile(str(good), warn=lambda _m: None) == "s3cret"
        empty = Path(td) / "empty"
        empty.write_text("  \n", encoding="utf-8")
        for bad in (str(empty), str(Path(td) / "nope")):
            try:
                mq.read_passfile(bad, warn=lambda _m: None)
            except mq.SynBrokerError:
                pass
            else:
                raise AssertionError(f"empty/missing passfile must raise: {bad}")


def test_announce_line() -> None:
    cfg = mq.resolve_broker({"SYN_BROKER": "10.0.0.9:1883", "SYN_ALLOW_PLAINTEXT": "1"})
    line = mq.announce_line(cfg)
    assert "10.0.0.9:1883" in line and "--local" in line and "PLAINTEXT" in line


def _boom(msg: str) -> None:
    raise AssertionError(f"unexpected warning: {msg}")


def _run() -> int:
    checks = [(n, f) for n, f in sorted(globals().items())
              if n.startswith("test_") and callable(f)]
    failures = []
    for name, fn in checks:
        try:
            fn()
            print(f"PASS: {name}")
        except AssertionError as e:
            failures.append(name)
            print(f"FAIL: {name}\n      {e}")
    print("-" * 64)
    if failures:
        print(f"RESULT: FAIL — {len(failures)} failed: {', '.join(failures)}")
        return 1
    print(f"RESULT: PASS — {len(checks)} security-floor checks (no broker/paho needed).")
    return 0


if __name__ == "__main__":
    sys.exit(_run())
