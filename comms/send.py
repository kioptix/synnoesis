#!/usr/bin/env python3
"""send.py — send a signed message to an agent over the Synnoesis mesh (file transport).

The FLOOR transport: no broker, no MQTT, pure stdlib + the local ``sign`` / ``wire``
/ ``paths`` modules. It signs the message, records the local verify verdict, wraps
the canonical OUTER record, and appends it to the recipient's inbox JSONL under the
comms service dir. Zero infrastructure — the always-works path for two or more
sessions on one machine (a shared filesystem IS the transport).

A cross-machine transport (a broker) is planned for a later release; until then the
file transport is the only transport, so it is the default. The on-disk record shape
is defined once in ``wire.wrap_outer`` so a future second writer stays field-identical
and the reader (``inbox.py``) never has to branch on who wrote a record.

Examples:
  send.py --to bob "hello from alice"
  send.py --to bob "FYI: build finished" --fyi
  send.py --to bob --body-file note.txt

Defaults: --to peer, urgency=normal.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

# Local mesh libs live beside this script (sys.path.insert + bare imports) so this
# runs as a plain script without packaging the comms/ dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sign  # noqa: E402
import wire  # noqa: E402
import paths  # noqa: E402


def _resolve_agent_id() -> str:
    """Resolve THIS sender's agent id for the message `_from` field.

    Order:
      1. $PA_AGENT_ID                -- explicit; wins.
      2. nearest `.agent-id` marker  -- walk up from cwd, then this script's dir.
      3. hostname                    -- safe last resort.

    NEVER use the OS username ($USER/$USERNAME) as an agent id: on many hosts that is
    the human operator's name, which a recipient's inbox filters as own-outbound —
    using it would silently drop the message. The marker/hostname chain avoids it.
    """
    v = (os.environ.get("PA_AGENT_ID") or "").strip()
    if v:
        return v
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        d = start
        while True:
            marker = d / ".agent-id"
            if marker.is_file():
                try:
                    lines = marker.read_text(encoding="utf-8").splitlines()
                    val = lines[0].strip() if lines else ""
                    if val:
                        return val
                except OSError:
                    pass
            if d.parent == d:
                break
            d = d.parent
    return socket.gethostname().split(".")[0].lower()


def cmd_send(args) -> int:
    """Sign, verify, wrap the canonical OUTER record, and append it to the
    recipient's inbox JSONL under the comms service dir. No broker, no paho."""
    urgency = ("urgent" if args.urgent else
               "fyi"    if args.fyi    else
               "queue"  if args.queue  else
               "normal")
    me = _resolve_agent_id()
    inner = sign.new_message(me, args.to, args.text, urgency)
    status, detail = sign.verify_against_keyring(
        inner, sign.load_keyring(), expected_tag=sign.DS_TAG_ENVELOPE)
    outer = wire.wrap_outer(inner, status, detail)
    inbox = paths.service_dir("comms", create=True) / (args.to + "-inbox.jsonl")
    with inbox.open("a", encoding="utf-8") as f:
        f.write(json.dumps(outer, ensure_ascii=False) + "\n")
    print(f"appended -> {inbox}  ({urgency} from {me}; verify={status})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="send",
        description="send a signed message to an agent over the Synnoesis mesh (file transport)")
    ap.add_argument("text", nargs="?", help="message body")
    ap.add_argument("--to", default="peer", help="target agent_id (default: peer)")
    ap.add_argument("--local", action="store_true",
                    help="use the local file transport (the only transport for now; the default)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--urgent", action="store_true", help="urgency=urgent")
    g.add_argument("--queue",  action="store_true", help="urgency=queue (do after current)")
    g.add_argument("--fyi",    action="store_true", help="urgency=fyi (no response needed)")
    ap.add_argument("--body-file", default=None, metavar="PATH",
                    help="read message body from a file instead of argv "
                         "(avoids ARG_MAX limits for large/multiline bodies)")
    args = ap.parse_args()
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            args.text = f.read()
    if not args.text:
        ap.print_help()
        return 2
    return cmd_send(args)


if __name__ == "__main__":
    sys.exit(main())
