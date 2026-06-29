#!/usr/bin/env python3
"""test_dispatch_contract_roundtrip.py -- gate #1: console-dispatcher contract round-trip.

Proves the new console entrypoint (``python synnoesis.py send``) routes a message
through the SAME ``sign.new_message`` + ``wire.wrap_outer`` chain as the original
``python comms/send.py``, so the on-disk inbox record is the same canonical record
the contract froze -- the dispatcher does NOT reimplement the wire format.

"Byte-identical" caveat (documented, not a loophole): two independent sends can
never be literally byte-identical -- ``sign.new_message`` mints a fresh ``_at``
timestamp and a random ``_nonce`` each call, and ``wire.wrap_outer`` stamps a fresh
``received_at``. So a literal ``open(a).read() == open(b).read()`` would fail for
reasons that have NOTHING to do with the contract. This gate instead asserts the
records are byte-identical AFTER normalizing exactly those inherently-nondeterministic
fields (``received_at`` on the outer record; ``_at`` and ``_nonce`` inside the inner
payload). Both sends run UNSIGNED (no keys in either throwaway PA_HOME), so there is
no ``_sig`` to differ either. Everything that is contract-bearing -- the four outer
keys and their order, ``topic``, the ``_verify`` verdict, and the full inner-envelope
key set + values -- must match exactly, byte-for-byte, between the two writers.

Run: python tests/test_dispatch_contract_roundtrip.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SYNNOESIS_PY = REPO / "synnoesis.py"
SEND_PY = REPO / "comms" / "send.py"

SENDER = "alice"
RECIPIENT = "bob"
BODY = "contract round-trip body"

# Fields that legitimately differ between two independent sends -- normalize
# these to a constant before comparing, so the comparison is byte-identical on
# everything ELSE (the contract-bearing structure + values).
_OUTER_NONDET = ("received_at",)
_INNER_NONDET = ("_at", "_nonce")

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        suffix = f"  ({detail})" if detail else ""
        print(f"FAIL  {label}{suffix}")
        _failures.append(label)


def _send(cmd: list[str], home: Path) -> int:
    env = dict(os.environ)
    env["PA_HOME"] = str(home)
    env["PA_AGENT_ID"] = SENDER
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"      send failed rc={proc.returncode} stderr={proc.stderr.strip()!r}")
    return proc.returncode


def _read_record(home: Path) -> dict:
    inbox = home / "comms" / f"{RECIPIENT}-inbox.jsonl"
    lines = [ln for ln in inbox.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) != 1:
        raise AssertionError(f"expected exactly 1 record in {inbox}, got {len(lines)}")
    return json.loads(lines[-1])


def _normalize(rec: dict) -> dict:
    """Return a deep copy with the inherently-nondeterministic fields blanked,
    so two records from two independent sends compare on structure + content."""
    out = dict(rec)
    for k in _OUTER_NONDET:
        if k in out:
            out[k] = "<normalized>"
    # payload is a JSON STRING -- parse, normalize the inner envelope, re-dump
    # with the SAME serialization the writer uses so the string compares cleanly.
    inner = json.loads(out["payload"])
    for k in _INNER_NONDET:
        if k in inner:
            inner[k] = "<normalized>"
    out["payload"] = json.dumps(inner, ensure_ascii=False)
    return out


def main() -> int:
    if not SYNNOESIS_PY.is_file():
        check("synnoesis.py present", False, f"missing {SYNNOESIS_PY}")
        return 1
    if not SEND_PY.is_file():
        check("comms/send.py present", False, f"missing {SEND_PY}")
        return 1

    home_disp = Path(tempfile.mkdtemp(prefix="syn-disp-"))
    home_direct = Path(tempfile.mkdtemp(prefix="syn-direct-"))
    try:
        rc_d = _send([sys.executable, str(SYNNOESIS_PY), "send", "--to", RECIPIENT, BODY],
                     home_disp)
        check("python synnoesis.py send exits 0", rc_d == 0, f"rc={rc_d}")
        rc_s = _send([sys.executable, str(SEND_PY), "--to", RECIPIENT, BODY],
                     home_direct)
        check("python comms/send.py send exits 0", rc_s == 0, f"rc={rc_s}")
        if rc_d != 0 or rc_s != 0:
            return 1

        rec_disp = _read_record(home_disp)
        rec_direct = _read_record(home_direct)

        # Outer key set + ORDER must match (the contract froze 4 keys in order).
        check("outer key order identical",
              list(rec_disp.keys()) == list(rec_direct.keys()),
              f"disp={list(rec_disp.keys())} direct={list(rec_direct.keys())}")

        # Both must be unsigned (no keys present) -- proves we're comparing the
        # same verdict path and there's no _sig nondeterminism in play.
        check("dispatcher record is unsigned (verify=unsigned)",
              rec_disp.get("_verify", {}).get("status") == "unsigned",
              f"status={rec_disp.get('_verify', {}).get('status')!r}")
        check("direct record is unsigned (verify=unsigned)",
              rec_direct.get("_verify", {}).get("status") == "unsigned",
              f"status={rec_direct.get('_verify', {}).get('status')!r}")

        # The headline assertion: byte-identical after normalizing only the
        # inherently-nondeterministic fields. Compare the serialized bytes.
        norm_disp = json.dumps(_normalize(rec_disp), ensure_ascii=False, sort_keys=True)
        norm_direct = json.dumps(_normalize(rec_direct), ensure_ascii=False, sort_keys=True)
        check("normalized records are BYTE-IDENTICAL "
              "(dispatcher routes through wrap_outer)",
              norm_disp == norm_direct,
              f"\n      disp  ={norm_disp}\n      direct={norm_direct}")
    finally:
        import shutil
        shutil.rmtree(home_disp, ignore_errors=True)
        shutil.rmtree(home_direct, ignore_errors=True)

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print("RESULT  PASS  (dispatcher record == direct send.py record, contract intact)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
