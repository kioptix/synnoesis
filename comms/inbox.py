#!/usr/bin/env python3
"""inbox — print recent inbox messages for an agent in a human-
readable form. Used by the session-boot directive to catch up on
inbound messages that arrived while the session wasn't listening.

Usage:
  python inbox.py [--agent-id alice] [--since 2h] [--limit 10]
                  [--not-from alice,owner]

Output: one line per message, oldest first:
  [HH:MM EDT] bob (urg=normal, age=12m): got your draft — sending critique back now...

Or, if no matches:
  (no inbound mesh messages in last 2h)

Exit code is always 0 — this is a read-only diagnostic helper, not a
test.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Windows default stdout encoding is cp1252 which crashes on arrows / em
# dashes / ellipsis common in agent-to-agent comms. Force UTF-8 so the
# session-boot directive output renders cleanly across consoles.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pylint: disable=broad-except
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths
import sign

INBOX_DIR = paths.service_dir("comms")

DISPLAY_TZ = os.environ.get("PA_DISPLAY_TZ", "UTC")   # override with your locale, e.g. America/New_York


def parse_since(s: str) -> timedelta:
    """Parse '2h' / '90m' / '1d' / '30' (minutes) into a timedelta."""
    s = s.strip().lower()
    m = re.match(r"^(\d+)\s*([hmd]?)$", s)
    if not m:
        raise ValueError(f"can't parse --since {s!r}")
    n = int(m.group(1))
    unit = m.group(2) or "m"
    if unit == "h":
        return timedelta(hours=n)
    if unit == "d":
        return timedelta(days=n)
    return timedelta(minutes=n)


def parse_iso(s: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, ALWAYS returning a tz-aware datetime.

    A naive timestamp (no offset) is treated as UTC — the bridge
    writes all timestamps with +00:00, but older / synthetic records may
    omit the offset. Better to coerce to UTC than to crash on the
    naive-vs-aware comparison downstream. See feedback_timestamp_timezones.md.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print recent agent inbox tail")
    ap.add_argument("--agent-id", default=os.environ.get("PA_AGENT_ID"),
                    help="agent whose inbox to read (default: $PA_AGENT_ID)")
    ap.add_argument("--since", default="2h",
                    help="lookback window: 90m / 2h / 1d (default: 2h)")
    ap.add_argument("--limit", type=int, default=10,
                    help="max items to print (default: 10)")
    ap.add_argument("--not-from", default=None,
                    help="comma-separated _from values to EXCLUDE "
                         "(default: <agent-id>,<owner> — own outbound + owner relay; "
                         "owner via PA_OWNER_ID)")
    ap.add_argument("--include-fyi", action="store_true",
                    help="include urgency=fyi items (default: skip)")
    ap.add_argument("--include-state", action="store_true",
                    help="include presence/state-topic messages (default: skip)")
    args = ap.parse_args(argv)
    # Opt-in read-side signing enforcement (default unset/OFF -> today's warn-only
    # behavior, byte-for-byte). ON: deliver ONLY records whose _verify.status == "ok";
    # suppress bad/no-key/unsigned. Gates on the already-stamped status string -- no
    # new verifier, no new field. See sign.verify_against_keyring for the vocabulary.
    enforce = (os.environ.get("SYN_ENFORCE_SIGNING") or "").strip() not in ("", "0")
    # If enforce is requested but this host lacks `cryptography`, every record would
    # verify as "unavailable" -- silently rejecting 100% of traffic is the "absence
    # masquerading as success" failure mode. Fail LOUD at startup instead of black-
    # holing the inbox: one-line stderr config-error + non-zero exit.
    if enforce and not sign.CRYPTO_AVAILABLE:
        print("error: SYN_ENFORCE_SIGNING is set but the 'cryptography' library is "
              "not installed -- cannot verify signatures; install cryptography or "
              "unset SYN_ENFORCE_SIGNING", file=sys.stderr)
        return 1
    if not args.agent_id:
        print("error: pass --agent-id or set PA_AGENT_ID", file=sys.stderr)
        return 1
    # Default exclusion is DYNAMIC to --agent-id: exclude your OWN id + the owner
    # handle (own outbound + the owner's relay). The owner is config-driven
    # (PA_OWNER_ID), never a hardcoded name, so any agent's tail excludes the
    # right pair without a baked-in identity.
    if args.not_from is None:
        owner = os.environ.get("PA_OWNER_ID", "owner")
        args.not_from = f"{args.agent_id},{owner}"

    try:
        delta = parse_since(args.since)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    inbox = INBOX_DIR / f"{args.agent_id}-inbox.jsonl"
    if not inbox.exists():
        print(f"(no inbox file at {inbox})")
        return 0

    excluded = {x.strip() for x in args.not_from.split(",") if x.strip()}
    now = datetime.now(timezone.utc)
    cutoff = now - delta

    matches: list[dict] = []
    try:
        with open(inbox, "rb") as f:
            for raw in f:
                try:
                    r = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                # Skip bridge-emitted sentinel records (e.g. backlog-end
                # marker — they're not directed messages). Added 2026-05-26
                # alongside the bridge's backlog-marker support.
                if r.get("_marker"):
                    continue
                topic = r.get("topic", "")
                if not args.include_state and topic.endswith("/state"):
                    continue

                # The payload is a JSON-string with the actual message inside.
                inner_raw = r.get("payload", "{}")
                try:
                    inner = json.loads(inner_raw) if isinstance(inner_raw, str) else inner_raw
                except Exception:
                    continue
                if not isinstance(inner, dict):
                    continue

                frm = inner.get("_from", "?")
                if frm in excluded:
                    continue
                urg = (inner.get("_urgency") or "normal").lower()
                if urg == "fyi" and not args.include_fyi:
                    continue

                at_iso = inner.get("_at") or r.get("received_at")
                dt = parse_iso(at_iso)
                if not dt:
                    continue
                if dt < cutoff:
                    continue

                age_min = int((now - dt).total_seconds() // 60)
                body = (inner.get("body") or "").strip()
                preview = body.replace("\n", " ").strip()
                matches.append({
                    "ts": dt,
                    "from": frm,
                    "urg": urg,
                    "age_min": age_min,
                    "preview": preview,
                    "nonce": inner.get("_nonce"),
                    # Signature-verify status the BRIDGE stamped on the OUTER record
                    # (ok / unsigned / no-key / bad / error). Surfaced in the printed
                    # line below so forgery risk is visible AT THE POINT OF ACTION --
                    # the _from identity is UNTRUSTED unless verify == "ok".
                    # The bridge tags each record; the consumer must surface it.
                    "verify": (r.get("_verify") or {}).get("status"),
                })
    except OSError as e:
        print(f"error reading inbox: {e}", file=sys.stderr)
        return 0

    # Oldest first — read top-to-bottom, like a chat history.
    matches.sort(key=lambda m: m["ts"])
    if args.limit and len(matches) > args.limit:
        # Show the last N — those are the most recent and likely most
        # important. Keep oldest-first order within the slice.
        matches = matches[-args.limit:]

    if not matches:
        # Friendly zero-message line. The IDE session will see this and
        # know there's nothing to catch up on.
        print(f"(no inbound mesh messages in last {args.since}, "
              f"excluding {sorted(excluded)})")
        return 0

    try:
        tz = ZoneInfo(DISPLAY_TZ)
    except ZoneInfoNotFoundError:
        # No time-zone database on this host -- e.g. a clean Windows venv with no
        # `tzdata` PyPI package, where even ZoneInfo("UTC") (the DISPLAY_TZ default)
        # raises. Degrade to stdlib UTC + a one-time warning instead of crashing the
        # read. Do NOT retry ZoneInfo("UTC") -- it needs tzdata too. This preserves
        # the "stdlib only, no required runtime deps" promise (graceful degradation).
        tz = timezone.utc
        print("warning: no time-zone database (tzdata) available; showing times in "
              "UTC -- run `pip install tzdata` for local-zone display.",
              file=sys.stderr)
    print(f"# {args.agent_id} inbox — last {args.since}, {len(matches)} item(s)")
    suppressed = 0
    for m in matches:
        vstat = m.get("verify")
        # Enforce mode (SYN_ENFORCE_SIGNING set): deliver ONLY "ok"; SKIP (do not
        # print) any record whose verify status is not "ok" (bad/no-key/unsigned/
        # absent), counting the suppression so we can report it on stderr.
        if enforce and vstat != "ok":
            suppressed += 1
            continue
        local = m["ts"].astimezone(tz)
        stamp = local.strftime("%H:%M")
        # Dynamic zone abbreviation off the tz-aware `local` -- NOT a hardcoded
        # literal. DISPLAY_TZ defaults to UTC, so a fixed "EDT" would mislabel the
        # default user's times by their UTC offset. %Z renders UTC->"UTC",
        # America/New_York->"EDT"/"EST" (DST-aware), and the tzdata-absent fallback
        # (tz=timezone.utc) ->"UTC", keeping the label honest in every case.
        zlabel = local.strftime("%Z")
        age_str = (f"{m['age_min']}m" if m['age_min'] < 60
                   else f"{m['age_min']//60}h{m['age_min']%60:02d}m")
        # Truncate preview at 200 chars so the boot summary stays readable
        # on the IDE side; full body is in the JSONL file.
        prev = m["preview"][:200]
        if len(m["preview"]) > 200:
            prev += "…"
        # Surface a forgery/trust warning whenever the bridge's signature check
        # is not "ok". Treat _from as UNTRUSTED unless ok. EVERY non-ok status
        # gets a marker -- incl. ERROR/UNAVAILABLE (cannot-confirm = possible
        # forgery; Phase 0 DELIVERS these, doesn't quarantine) AND ABSENT/None
        # (an untagged message must NOT render clean). Phase-0 safety is coupled
        # to this Phase-1 marker coverage.
        vmark = "" if vstat == "ok" else f" [!{(vstat or 'UNVERIFIED').upper()}]"
        print(f"  [{stamp} {zlabel}] {m['from']}{vmark} (urg={m['urg']}, age={age_str}): {prev}")
    # Enforce mode NEVER suppresses silently: always announce a non-zero count on
    # stderr so the consumer knows traffic was withheld. Exit code stays 0 (read-
    # only diagnostic invariant).
    if enforce and suppressed >= 1:
        print(f"# enforce: suppressed {suppressed} unverified record(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
