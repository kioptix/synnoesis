"""presence.py -- who is on the mesh, and how much you may trust that answer.

A listener publishes a RETAINED, RECORD-SIGNED state document to ``agent/<id>/state``:

    on connect         -> {"online": true,  "via": "connect"}
    on clean shutdown  -> {"online": false, "via": "clean-shutdown"}
    on ungraceful death-> {"online": false, "via": "lwt"}   (published BY THE BROKER)

Signed from day one with ``sign.DS_TAG_PRESENCE``. An unsigned presence channel is a
channel where anyone who can reach the broker can declare anyone else online or
offline, which is worse than having no presence at all -- it is confident and wrong.

HONESTY RULES BAKED IN (these are the whole point of the module)
---------------------------------------------------------------
1. **RETAINED IS NOT ALIVE.** A retained record is the LAST thing the agent said, not
   the current truth. The broker replays it to every new subscriber forever. An agent
   whose host lost power without the broker noticing still reads ``online: true``.

2. **AGE IS CROSS-CLOCK AND THEREFORE APPROXIMATE.** ``who`` shows reader-now minus the
   record's ``_at``, and ``_at`` came from the AGENT's clock. Clock skew shifts it. That
   is acceptable here because presence is an INFORMATIONAL DISPLAY -- skew makes a
   display fuzzy. It would be unacceptable in a liveness GATE, where skew makes a
   dead-man's switch fire on a healthy agent. Do not reuse this math for a gate.

3. 🔴 **AN LWT RECORD IS ALWAYS "OLD", AND THAT IS CORRECT.** The Last Will is signed
   and handed to the broker at CONNECT time but published at DEATH time -- possibly
   hours later. Its ``_at`` is the connect timestamp. So an LWT record legitimately
   looks stale, and a freshness check would reject exactly the message that tells you
   an agent died. Presence is therefore NEVER freshness-gated. The staleness is
   displayed, not enforced.

4. **A FORGED RECORD IS SHOWN AS FORGED.** A ``bad`` verify is never folded into
   "offline" or quietly hidden; it is displayed as a forgery, because someone
   attempting to spoof presence is more interesting than the presence itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sign

SCHEMA = 1
WILDCARD = "agent/+/state"

VIA_CONNECT = "connect"
VIA_CLEAN_SHUTDOWN = "clean-shutdown"
VIA_LWT = "lwt"


def topic_for(agent_id: str) -> str:
    return f"agent/{agent_id}/state"


def agent_id_from_topic(topic: str) -> str:
    """``agent/<id>/state`` -> ``<id>``. Returns '' for anything else.

    NOTE the topic is the BROKER's view; the record's own ``agent_id`` field is what
    the signature covers. ``who`` compares them -- a mismatch means someone published
    a validly-signed record for themselves onto somebody else's topic.
    """
    parts = topic.split("/")
    if len(parts) == 3 and parts[0] == "agent" and parts[2] == "state":
        return parts[1]
    return ""


def build(agent_id: str, *, online: bool, via: str,
          listener_started_at: str = "", now: datetime | None = None) -> dict:
    """Build an UNSIGNED presence record. Pure -- no clock reads unless ``now`` is None,
    no key access, so tests can build records deterministically."""
    ts = (now or datetime.now(timezone.utc)).isoformat()
    rec = {
        "schema": SCHEMA,
        "agent_id": agent_id,
        "online": bool(online),
        "via": via,
        "_at": ts,
    }
    if listener_started_at:
        rec["listener_started_at"] = listener_started_at
    return rec


def sign_record(rec: dict, agent_id: str) -> dict:
    """Sign in place with the presence domain tag. Unsigned (unchanged) when no private
    key exists or crypto is absent -- consistent with the rest of the floor, and the
    consumer will mark it ``unsigned`` rather than trusting it."""
    priv = sign.load_private_key(agent_id)
    if not priv:
        return rec
    return sign.attach_record_signature(rec, priv, key_id=agent_id,
                                        ds_tag=sign.DS_TAG_PRESENCE)


def payload(agent_id: str, *, online: bool, via: str,
            listener_started_at: str = "", now: datetime | None = None) -> bytes:
    rec = sign_record(build(agent_id, online=online, via=via,
                            listener_started_at=listener_started_at, now=now),
                      agent_id)
    return json.dumps(rec, ensure_ascii=False).encode("utf-8")


def will_tuple(agent_id: str, *, now: datetime | None = None):
    """The Last Will, as ``open_subscriber(will=...)`` wants it.

    Built at connect time (see honesty rule 3). Retained, qos1: retained so a
    subscriber connecting after the death still learns about it, qos1 so the broker
    does not drop the one message that reports the agent is gone.
    """
    return (topic_for(agent_id),
            payload(agent_id, online=False, via=VIA_LWT, now=now),
            1, True)


def publish(cfg, agent_id: str, *, online: bool, via: str,
            listener_started_at: str = "", mq=None) -> None:
    """Publish a retained presence record. Raises SynBrokerError on failure -- a
    presence publish that silently failed would leave peers reading a stale record and
    believing it current."""
    if mq is None:
        import mqtt as mq  # noqa: PLC0415 -- lazy so pure users need no paho
    mq.publish_one(cfg, topic_for(agent_id),
                   payload(agent_id, online=online, via=via,
                           listener_started_at=listener_started_at),
                   client_id=f"synnoesis-presence-{agent_id}", retain=True)


def parse(raw: bytes, topic: str, keyring: dict) -> dict:
    """Parse + LOCALLY verify one retained presence record.

    Returns a display dict; never raises. ``status`` uses the same vocabulary as
    message verification (ok / unsigned / no-key / bad / unavailable / malformed) so a
    reader does not have to learn a second one.
    """
    claimed = agent_id_from_topic(topic)
    try:
        rec = json.loads(raw.decode("utf-8"))
        if not isinstance(rec, dict):
            raise ValueError("not an object")
    except Exception:  # noqa: BLE001
        return {"agent_id": claimed or "?", "status": "malformed",
                "detail": "payload is not a JSON object", "online": None,
                "via": "", "_at": ""}

    status, detail = sign.verify_record_against_keyring(
        rec, keyring, expected_tag=sign.DS_TAG_PRESENCE)

    # The topic is the broker's routing; the signature covers the RECORD's agent_id.
    # If they disagree, a validly-signed record is sitting on someone else's topic.
    inner_id = str(rec.get("agent_id") or "")
    if claimed and inner_id and inner_id != claimed:
        status, detail = "bad", (f"record claims agent_id {inner_id!r} but was "
                                 f"published on {claimed!r}'s topic")

    return {
        "agent_id": inner_id or claimed or "?",
        "online": rec.get("online"),
        "via": str(rec.get("via") or ""),
        "_at": str(rec.get("_at") or ""),
        "status": status,
        "detail": detail,
    }


def peek(cfg, agent_id: str, *, timeout: float = 0.6, mq_mod=None):
    """Best-effort read of ONE agent's retained presence. Returns an entry dict, or
    None if nothing was retained or anything at all went wrong.

    Deliberately swallows every error and is bounded by ``timeout``: this exists to
    add a helpful note to a send that has ALREADY SUCCEEDED. A presence lookup must
    never fail, delay, or cast doubt on a delivered message.
    """
    if mq_mod is None:
        import mqtt as mq_mod  # noqa: PLC0415
    import time as _time
    box: dict = {}

    def on_message(_c, _u, message):
        try:
            box["entry"] = parse(message.payload, message.topic, sign.load_keyring())
        except Exception:  # noqa: BLE001
            pass

    client = None
    try:
        client = mq_mod.open_subscriber(
            cfg, topic_for(agent_id), on_message,
            client_id=f"synnoesis-peek-{agent_id}-{int(_time.time() * 1000) % 100000}")
        client.loop_start()
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline and "entry" not in box:
            _time.sleep(0.05)
    except Exception:  # noqa: BLE001
        return None
    finally:
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
    return box.get("entry")


def offline_note(entry, recipient: str) -> str:
    """The stderr note for a send whose recipient does not look online. Empty string
    when there is nothing worth saying -- silence is the default, not a shrug."""
    if entry is None:
        return (f"note: no presence record for {recipient!r} — cannot tell whether "
                f"they are listening. The message is queued on the broker and will "
                f"be delivered within its freshness window if they connect.")
    if entry.get("status") == "bad":
        return (f"warning: {recipient!r}'s presence record FAILED verification "
                f"({entry.get('detail', '')}) — ignoring it; delivery is unaffected.")
    if entry.get("online") is False:
        return (f"note: {recipient!r} appears offline (last said "
                f"{age_str(entry.get('_at', ''))}, via {entry.get('via') or '?'}) — "
                f"the message queues on the broker and is delivered when they "
                f"reconnect, provided it is still inside the freshness window.")
    return ""


def age_str(at_iso: str, now: datetime | None = None) -> str:
    """Human age of a presence record, computed on the READER's clock.

    Approximate by construction (honesty rule 2) -- the caller labels it as such.
    """
    if not at_iso:
        return "unknown"
    try:
        at = datetime.fromisoformat(at_iso.replace("Z", "+00:00"))
    except ValueError:
        return "unparseable"
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    secs = ((now or datetime.now(timezone.utc)) - at).total_seconds()
    if secs < 0:
        # The record is from the FUTURE on our clock -- skew, not fraud (the signature
        # already told us whether to trust it). Say so rather than printing "-4m".
        return f"clock-skew (+{int(-secs)}s ahead)"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return f"{int(secs)}s ago"


def describe(entry: dict, now: datetime | None = None) -> str:
    """One display line. Forgery is called out FIRST -- an attempt to spoof presence
    matters more than what the spoofed record claims."""
    aid = entry.get("agent_id", "?")
    st = entry.get("status", "?")
    age = age_str(entry.get("_at", ""), now)
    via = entry.get("via") or "?"

    if st == "bad":
        return f"  {aid:<16} ⚠ FORGED presence record — {entry.get('detail', '')}"
    online = entry.get("online")
    if online is True:
        state = "online"
    elif online is False:
        state = "offline"
    else:
        state = "unknown"
    note = {"ok": "", "unsigned": "  [UNSIGNED]", "no-key": "  [NO KEY]",
            "unavailable": "  [CANNOT VERIFY: no cryptography]",
            "malformed": "  [MALFORMED]"}.get(st, f"  [{st}]")
    return f"  {aid:<16} {state:<8} via {via:<14} last said {age}{note}"
