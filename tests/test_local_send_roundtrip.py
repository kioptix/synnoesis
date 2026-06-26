#!/usr/bin/env python3
"""test_local_send_roundtrip.py — self-running gate for the `--local` send path.

Exercises the REAL ``comms/send.py --local`` CLI end-to-end (as a consumer
invokes it — via subprocess, not a REPL import), into a throwaway ``PA_HOME``,
then reads the recipient inbox JSONL the send produced and asserts the wire
record carries the right inner body / sender and the canonical outer shape.

It hand-writes NOTHING about the wire format: the bytes on disk are produced by
the real ``sign.new_message`` + ``wire.wrap_outer`` chain inside ``send.py``,
and this gate only parses them back and checks the fields. If the producers
ever change the outer-record shape (topic/received_at/payload/_verify) or the
inner envelope (_from/body), this gate fails.

Run it directly — it prints PASS/FAIL lines and exits 0 (all pass) or 1 (any
fail). No pytest, no third-party deps.

  python tests/test_local_send_roundtrip.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Repo layout: this file is <repo>/tests/, send.py lives in <repo>/comms/.
REPO = Path(__file__).resolve().parent.parent
SEND_PY = REPO / "comms" / "send.py"

SENDER = "alice"
RECIPIENT = "bob"
BODY = "hi"

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    """Record one assertion; print a PASS/FAIL line."""
    if cond:
        print(f"PASS  {label}")
    else:
        suffix = f"  ({detail})" if detail else ""
        print(f"FAIL  {label}{suffix}")
        _failures.append(label)


def main() -> int:
    if not SEND_PY.is_file():
        print(f"FAIL  send.py present at {SEND_PY}")
        return 1

    # Fresh, isolated data root so we never touch a real inbox. tempfile.mkdtemp
    # tolerates a space in the temp path; send.py resolves PA_HOME via pathlib,
    # so a space there can't break it.
    tmp = Path(tempfile.mkdtemp(prefix="synnoesis-localsend-"))
    try:
        env = dict(os.environ)
        env["PA_HOME"] = str(tmp)        # comms dir -> <tmp>/comms/
        env["PA_AGENT_ID"] = SENDER      # inner envelope _from == "alice"

        # Invoke the real CLI the way a user would.
        proc = subprocess.run(
            [sys.executable, str(SEND_PY), "--local", "--to", RECIPIENT, BODY],
            env=env, capture_output=True, text=True,
        )
        check("send.py --local exits 0", proc.returncode == 0,
              f"rc={proc.returncode} stderr={proc.stderr.strip()!r}")
        if proc.returncode != 0:
            # No record to inspect — bail with what we have.
            return 1

        # The send appends to <PA_HOME>/comms/<to>-inbox.jsonl.
        inbox = tmp / "comms" / f"{RECIPIENT}-inbox.jsonl"
        check("recipient inbox JSONL was written", inbox.is_file(),
              f"expected {inbox}")
        if not inbox.is_file():
            return 1

        lines = [ln for ln in inbox.read_text(encoding="utf-8").splitlines() if ln.strip()]
        check("inbox has exactly one record", len(lines) == 1,
              f"got {len(lines)} lines")
        if not lines:
            return 1

        outer = json.loads(lines[-1])

        # --- OUTER record shape: topic / received_at / payload / _verify -----
        for key in ("topic", "received_at", "payload", "_verify"):
            check(f"outer record has key {key!r}", key in outer,
                  f"keys={sorted(outer)}")

        check("outer topic targets recipient",
              outer.get("topic") == f"agent/{RECIPIENT}/inbox",
              f"topic={outer.get('topic')!r}")

        # payload is a JSON STRING (opaque, signature-preserving) — parse it back.
        check("outer payload is a JSON string",
              isinstance(outer.get("payload"), str),
              f"payload type={type(outer.get('payload')).__name__}")
        inner = json.loads(outer["payload"])

        # --- INNER envelope: body == "hi", _from == "alice" ------------------
        check("inner body == sent text",
              inner.get("body") == BODY, f"body={inner.get('body')!r}")
        check("inner _from == sender agent id",
              inner.get("_from") == SENDER, f"_from={inner.get('_from')!r}")
        check("inner _to == recipient",
              inner.get("_to") == RECIPIENT, f"_to={inner.get('_to')!r}")
    finally:
        # Best-effort cleanup of the throwaway data root.
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print("RESULT  PASS  (all assertions passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
