#!/usr/bin/env python3
"""test_outer_record_contract.py — the OUTER-record freeze guard (writer <-> reader).

The OUTER inbox record has ONE canonical shape: exactly four keys (``topic``,
``received_at``, ``payload``, ``_verify``), and ``_verify`` itself is exactly
``{"status", "detail"}``. The writer (``send.py``) builds it through the single
builder ``wire.wrap_outer``; the reader (``inbox.py``) consumes exactly those keys.
If either side drifts, the reader silently mis-parses messages. This guard freezes
the shape so a drift fails CI instead of leaking.

"Field-identical by REAL outputs, not a transcription": we do NOT hard-code an
expected key list and check a hand-typed dict against it — a copied list drifts from
the code and would pass while the code is wrong. Instead we BUILD a record the way
the writer does, from the SAME live functions (``sign.new_message`` +
``wire.wrap_outer``), and assert the shape of THAT real output; then we read
``send.py`` and ``inbox.py`` as source and require the writer to route through
``wrap_outer`` (not hand-roll a record) and the reader to consume the frozen keys.

(v0.1.0 has ONE writer: the file-transport ``send.py``. The cross-machine bridge is
a future second writer; because the shape lives only in ``wire.wrap_outer``, it will
stay field-identical by construction when it lands.)

Run: ``python tests/test_outer_record_contract.py``  (exit 0 = pass, 1 = drift)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_COMMS_DIR = Path(__file__).resolve().parents[1] / "comms"
if str(_COMMS_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMS_DIR))

import sign  # noqa: E402 — local comms lib
import wire  # noqa: E402

OUTER_KEYS = {"topic", "received_at", "payload", "_verify"}
VERIFY_KEYS = {"status", "detail"}

_SEND_PY = _COMMS_DIR / "send.py"
_INBOX_PY = _COMMS_DIR / "inbox.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_real_outer_record_shape() -> None:
    """The REAL wrap_outer output has exactly the four frozen outer keys, and its
    _verify sub-record has exactly the two frozen verify keys."""
    inner = sign.new_message("a", "b", "hi")
    rec = wire.wrap_outer(inner, "ok", "d")
    assert set(rec.keys()) == OUTER_KEYS, (
        f"outer keys drifted: got {sorted(rec.keys())}, expected {sorted(OUTER_KEYS)}")
    assert isinstance(rec["_verify"], dict), "_verify must be a dict"
    assert set(rec["_verify"].keys()) == VERIFY_KEYS, (
        f"_verify keys drifted: got {sorted(rec['_verify'].keys())}, "
        f"expected {sorted(VERIFY_KEYS)}")
    assert rec["_verify"]["status"] == "ok" and rec["_verify"]["detail"] == "d"
    assert isinstance(rec["payload"], str), "payload must be a JSON string"


def test_send_py_uses_wrap_outer_not_inline() -> None:
    """send.py must build the outer record via wire.wrap_outer, never inline."""
    src = _source(_SEND_PY)
    assert "wire.wrap_outer" in src, (
        "send.py does not reference wire.wrap_outer — it must build the outer record "
        "through the single builder, not hand-roll its own shape.")
    inline_received_at = re.search(r"""["']received_at["']\s*:""", src)
    inline_payload = re.search(r"""["']payload["']\s*:""", src)
    assert not (inline_received_at and inline_payload), (
        "send.py appears to hand-assemble an outer record inline (found both "
        "'received_at' and 'payload' as dict-literal keys). Build it ONLY via "
        "wire.wrap_outer so the shape lives in one place.")


def test_inbox_reads_the_frozen_keys() -> None:
    """inbox.py (the reader) must consume exactly the frozen outer keys — proving
    the writer's record and the reader's expectations can't drift apart silently."""
    src = _source(_INBOX_PY)
    for key in ("topic", "payload", "received_at", "_verify"):
        assert key in src, (
            f"inbox.py never reads {key!r} — the reader's expected fields drifted "
            f"from the writer's frozen outer record (wire.wrap_outer).")


def _run() -> int:
    failures: list[str] = []
    checks = [
        ("real wrap_outer output has the frozen outer + _verify key sets",
         test_real_outer_record_shape),
        ("send.py builds the outer record via wrap_outer (not inline)",
         test_send_py_uses_wrap_outer_not_inline),
        ("inbox.py reads exactly the frozen outer keys",
         test_inbox_reads_the_frozen_keys),
    ]
    for label, fn in checks:
        try:
            fn()
            print(f"PASS: {label}")
        except AssertionError as e:
            failures.append(label)
            print(f"FAIL: {label}\n      {e}")
    print("-" * 64)
    if failures:
        print(f"RESULT: FAIL — {len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("RESULT: PASS — outer record shape frozen; writer (wrap_outer) and reader agree.")
    return 0


if __name__ == "__main__":
    sys.exit(_run())
