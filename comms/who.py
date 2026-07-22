"""who.py -- who is on the mesh, with the caveats printed next to the answer.

Subscribes ``agent/+/state``, collects the RETAINED records the broker replays on
subscribe, verifies each one locally against this machine's keyring, and prints a
line per agent.

The interesting design constraint is that presence is the easiest thing on a mesh to
report confidently and wrongly. Three failure modes, all handled visibly rather than
silently:

  * a retained record is the last thing an agent SAID, not what is true now
  * the age is cross-clock, so it is approximate -- fine for a display, never for a gate
  * anyone who can reach the broker can publish to a topic; only the SIGNATURE says
    whether the record is really from the agent it names

So this command deliberately prints staleness and verify status inline instead of
reducing everything to a green dot. See presence.py for the honesty rules in full.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import sign
import presence
import mqtt as mq

DEFAULT_COLLECT_SEC = 2.0


def collect(cfg, *, collect_sec: float, client_id: str, mq_mod=None) -> list:
    """Subscribe to the presence wildcard and gather retained records for a window.

    A fixed window rather than "wait for N agents": we do not know how many agents
    exist, and a broker with none is a legitimate answer that must not hang forever.
    """
    mq_mod = mq_mod or mq
    keyring = sign.load_keyring()
    seen: dict = {}

    def on_message(_c, _u, message):
        entry = presence.parse(message.payload, message.topic, keyring)
        # Last write wins per agent: a live (non-retained) update arriving during the
        # window is newer than the retained one that preceded it.
        seen[entry["agent_id"]] = entry

    client = mq_mod.open_subscriber(cfg, presence.WILDCARD, on_message,
                                    client_id=client_id)
    try:
        client.loop_start()
        time.sleep(collect_sec)
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return [seen[k] for k in sorted(seen)]


def render(entries: list, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if not entries:
        return ("no presence records retained on the broker.\n"
                "(Agents publish presence when `synnoesis listen` connects — if none "
                "have run since the broker started, there is nothing to show.)")
    lines = [presence.describe(e, now) for e in entries]
    online = sum(1 for e in entries if e.get("online") is True
                 and e.get("status") != "bad")
    forged = sum(1 for e in entries if e.get("status") == "bad")
    out = [f"{len(entries)} agent(s) known, {online} reporting online:", *lines, ""]
    # The caveat rides WITH the answer. A reader who scrolls past a doc still sees it.
    out.append("retained ≠ alive: these are the last records each agent published, "
               "replayed by the broker.")
    out.append("ages are computed on THIS machine's clock against the agent's "
               "timestamp — approximate under clock skew, and informational only.")
    if forged:
        out.append(f"⚠ {forged} record(s) FAILED signature verification — someone "
                   f"published presence they cannot sign for.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="who",
        description="list agents' retained presence on the mesh (online/offline, "
                    "age, and signature status)")
    ap.add_argument("--collect-sec", type=float, default=DEFAULT_COLLECT_SEC,
                    help=f"seconds to gather retained records (default "
                         f"{DEFAULT_COLLECT_SEC})")
    args = ap.parse_args(argv)

    cfg = mq.resolve_broker()
    if cfg is None:
        print("error: `who` requires a broker — set SYN_BROKER=host[:port]. "
              "Presence is a broker feature; the file transport has no notion of "
              "who is online.", file=sys.stderr)
        return 2
    if args.collect_sec <= 0:
        print("error: --collect-sec must be positive", file=sys.stderr)
        return 2

    import socket
    # A transient, unique client id: `who` is a one-shot query and must NOT create a
    # persistent broker session, nor collide with a long-lived listener's id.
    cid = f"synnoesis-who-{socket.gethostname()[:12]}-{int(time.time())}"
    try:
        entries = collect(cfg, collect_sec=args.collect_sec, client_id=cid)
    except mq.SynBrokerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(render(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
