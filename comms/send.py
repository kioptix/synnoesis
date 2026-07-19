#!/usr/bin/env python3
"""send.py — send a signed message to an agent over the Synnoesis mesh.

Signs the message once, then delivers it by the selected transport:
  * FILE (the floor, default): no broker, pure stdlib — wraps the canonical OUTER
    record and appends it to the recipient's inbox JSONL under the comms service dir.
    Zero infrastructure; the always-works path for agents sharing one machine (a
    shared filesystem IS the transport).
  * MQTT (v0.4.0): publishes the signed inner envelope to a broker so agents on
    DIFFERENT machines can exchange it; the recipient's ``listen`` bridge verifies it
    locally and writes the same record. Enabled by SYN_BROKER (see ``mqtt.py``).

The on-disk record shape is defined once in ``wire.wrap_outer``, so both writers
(this file's file path, and the ``listen`` bridge on the MQTT path) produce
field-identical records and the reader (``inbox.py``) never branches on who wrote one.

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
import mqtt as mq  # noqa: E402  — broker transport (v0.4.0); lazy paho, stdlib-safe to import


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
    """Sign a message, then deliver it by the selected transport:

      * FILE (the floor, default): wrap the canonical OUTER record and append it to
        the recipient's inbox JSONL under the comms service dir. No broker, no paho.
      * MQTT (v0.4.0 ceiling): publish the INNER envelope bytes VERBATIM to
        ``agent/<to>/inbox`` on the broker. The recipient's ``listen`` bridge verifies
        locally and builds the outer record — field-identical to the file path by
        construction (both go through ``wire.wrap_outer``).

    Transport is chosen by ``mqtt.select_transport``: ``--local`` forces file;
    ``--via`` overrides; otherwise SYN_BROKER decides. An auto-selected MQTT send
    ANNOUNCES its transport (never a silent local→network switch)."""
    urgency = ("urgent" if args.urgent else
               "fyi"    if args.fyi    else
               "queue"  if args.queue  else
               "normal")
    me = _resolve_agent_id()
    inner = sign.new_message(me, args.to, args.text, urgency)

    cfg = mq.resolve_broker()
    try:
        transport = mq.select_transport(
            local=args.local, via=args.via, broker_configured=cfg is not None)
    except mq.SynBrokerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if transport == "mqtt":
        # Publish the inner envelope bytes exactly as minted — re-serializing could
        # reorder keys and break a signature the recipient still needs to verify.
        payload = json.dumps(inner, ensure_ascii=False).encode("utf-8")
        topic = "agent/" + args.to + "/inbox"
        print(mq.announce_line(cfg))              # visible egress: an auto-selected network send is never silent
        try:
            mq.publish_one(cfg, topic, payload, client_id=f"synnoesis-send-{me}")
        except mq.SynBrokerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"published -> {topic}  ({urgency} from {me})")
        return 0

    # --- file transport (the floor) — behavior unchanged from v0.3.0 ---
    status, detail = sign.verify_against_keyring(
        inner, sign.load_keyring(), expected_tag=sign.DS_TAG_ENVELOPE)
    outer = wire.wrap_outer(inner, status, detail)
    inbox = paths.service_dir("comms", create=True) / (args.to + "-inbox.jsonl")
    with inbox.open("a", encoding="utf-8") as f:
        f.write(json.dumps(outer, ensure_ascii=False) + "\n")
    print(f"appended -> {inbox}  ({urgency} from {me}; verify={status})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="send",
        description="send a signed message to an agent over the Synnoesis mesh (file or MQTT transport)")
    ap.add_argument("text", nargs="?", help="message body")
    ap.add_argument("--to", default="peer", help="target agent_id (default: peer)")
    ap.add_argument("--local", action="store_true",
                    help="force the local file transport (overrides SYN_BROKER)")
    ap.add_argument("--via", choices=("auto", "file", "mqtt"), default="auto",
                    help="transport: auto (SYN_BROKER decides; default), file, or "
                         "mqtt (errors if SYN_BROKER is unset — never silently falls "
                         "back to file)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--urgent", action="store_true", help="urgency=urgent")
    g.add_argument("--queue",  action="store_true", help="urgency=queue (do after current)")
    g.add_argument("--fyi",    action="store_true", help="urgency=fyi (no response needed)")
    ap.add_argument("--body-file", default=None, metavar="PATH",
                    help="read message body from a file instead of argv "
                         "(avoids ARG_MAX limits for large/multiline bodies)")
    args = ap.parse_args(argv)
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            args.text = f.read()
    if not args.text:
        ap.print_help()
        return 2
    return cmd_send(args)


if __name__ == "__main__":
    sys.exit(main())
