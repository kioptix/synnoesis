#!/usr/bin/env python3
"""test_floor_runs_paho_absent.py — the lazy-paho guard for the FLOOR.

Synnoesis has two transports (send.py docstring): the MQTT CEILING (needs
`paho-mqtt`) and the file-transport FLOOR (`--local`, broker-free, stdlib-only).
The contract is that importing `send` and running its `--local` path must NEVER
touch paho — the import is lazy, buried inside the MQTT-only functions
(`_new_client` / `_fetch_retained`), so a host with no broker and no paho
installed can still deliver locally.

THIS TEST IS THE GUARD. It poisons `paho` (and its submodules) in
`sys.modules` so any attempt to `import paho` raises ImportError, THEN imports
`send` and drives `cmd_send` against a throwaway PA_HOME. If a future
edit moves a paho import to module scope, importing `send` blows up here and
the test FAILS — exactly the regression we want to catch.

Self-running: execute the file directly (`python test_floor_runs_paho_absent.py`)
and it prints PASS / FAIL and exits 0 / 1. No pytest required.
"""
from __future__ import annotations

# ---- POISON paho FIRST, before send.py is anywhere near the import system ----
# Setting a module entry to None makes `import <that module>` raise ImportError
# (CPython treats a None entry as "known-absent"). This must happen at the very
# top so a module-level `import paho...` in send.py cannot sneak in ahead of it.
import sys

sys.modules["paho"] = None
sys.modules["paho.mqtt"] = None
sys.modules["paho.mqtt.client"] = None

import importlib
import json
import os
import tempfile
from pathlib import Path


# Locate the comms/ dir that holds send.py (sibling of this tests/ dir), so the
# test runs from a fresh checkout regardless of cwd. pathlib throughout, so a
# space anywhere in the clone path is harmless.
_COMMS_DIR = Path(__file__).resolve().parent.parent / "comms"


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _confirm_paho_blocked() -> None:
    """The guard only means something if `import paho` actually fails now.
    (paho may or may not be installed on this host; either way the poison
    above must win, so the test exercises the no-paho world deterministically.)"""
    try:
        importlib.import_module("paho.mqtt.client")
    except ImportError:
        return  # good — paho is blocked, the floor must cope without it
    raise AssertionError(
        "precondition failed: `import paho.mqtt.client` succeeded despite the "
        "sys.modules poison — the guard cannot prove the floor is paho-free")


def run() -> None:
    _confirm_paho_blocked()

    # Make send.py importable by name without packaging comms/ (mirrors how
    # send.py itself adds its own dir to sys.path).
    _assert(_COMMS_DIR.is_dir(), f"comms dir not found: {_COMMS_DIR}")
    sys.path.insert(0, str(_COMMS_DIR))

    # THE GUARD: if send.py (or anything it imports at module scope) pulls in
    # paho, the poison above turns it into an ImportError right here and the
    # test fails. A clean import proves the lazy-paho contract holds.
    try:
        send = importlib.import_module("send")
    except ImportError as exc:
        raise AssertionError(
            "importing `send` triggered a paho import (paho must be lazy, "
            f"inside the MQTT-only functions only): {exc}") from exc

    # Drive the FLOOR (--local) end to end against a throwaway PA_HOME so we
    # write nothing into the real ~/.synnoesis. Every path send.py touches for
    # --local resolves through paths.service_dir, which honors PA_HOME.
    with tempfile.TemporaryDirectory(prefix="synnoesis-floor-test-") as tmp:
        prev_pa_home = os.environ.get("PA_HOME")
        prev_agent_id = os.environ.get("PA_AGENT_ID")
        os.environ["PA_HOME"] = tmp
        # Pin the sender id so the test never depends on a stray .pa-agent-id
        # marker or the host's name leaking into the record.
        os.environ["PA_AGENT_ID"] = "floor-test-sender"
        try:
            # Build the same args object main() would hand cmd_send.
            args = type("Args", (), {})()
            args.to = "peer"
            args.text = "floor smoke: the file transport works with no paho"
            args.urgent = False
            args.queue = False
            args.fyi = True  # urgency=fyi

            rc = send.cmd_send(args)
            _assert(rc == 0, f"cmd_send returned {rc}, expected 0")

            # Verify the record actually landed in the temp PA_HOME comms dir.
            inbox = Path(tmp) / "comms" / "peer-inbox.jsonl"
            _assert(inbox.is_file(),
                    f"expected inbox file was not written: {inbox}")

            lines = [ln for ln in inbox.read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
            _assert(len(lines) == 1,
                    f"expected exactly 1 record, got {len(lines)}")

            outer = json.loads(lines[0])
            # Outer record shape (wire.wrap_outer): topic / received_at /
            # payload (a JSON STRING) / _verify.
            _assert(outer.get("topic") == "agent/peer/inbox",
                    f"wrong topic: {outer.get('topic')!r}")
            _assert("_verify" in outer and "status" in outer["_verify"],
                    "outer record missing _verify.status")

            inner = json.loads(outer["payload"])
            _assert(inner.get("_from") == "floor-test-sender",
                    f"wrong _from: {inner.get('_from')!r}")
            _assert(inner.get("_to") == "peer",
                    f"wrong _to: {inner.get('_to')!r}")
            _assert(inner.get("_urgency") == "fyi",
                    f"wrong _urgency: {inner.get('_urgency')!r}")
            _assert(inner.get("body") == args.text,
                    "inner body did not round-trip")
        finally:
            # Restore the env we touched so a test runner's process is clean.
            for key, prev in (("PA_HOME", prev_pa_home),
                              ("PA_AGENT_ID", prev_agent_id)):
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev


def main() -> int:
    try:
        run()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — any unexpected error is a failure
        print(f"FAIL (unexpected {type(exc).__name__}): {exc}")
        return 1
    print("PASS: send.py imports and --local delivers with paho blocked "
          "(lazy-paho contract holds; floor is broker-free)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
