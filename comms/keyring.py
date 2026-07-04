#!/usr/bin/env python3
"""keyring.py — manage the Synnoesis mesh keyring (the trust decision).

A thin CLI over ``sign.load_keyring`` / ``sign._mesh_keys_dir`` — keyring writes
are pure JSON (no crypto). Recording an agent's PUBLIC key declares "I trust
messages that agent signs" (quickstart Section 5); nothing is trusted by default.

Actions (pick exactly one):
  --add AGENT_ID --pubkey B64 [--expect-fingerprint FP] [--rotate]
      Register AGENT_ID's public key. REFUSES to silently overwrite an existing,
      DIFFERENT key (the SSH "host key changed" alarm) unless --rotate confirms.
      --expect-fingerprint checks the key's fingerprint BEFORE writing and refuses
      on mismatch — this mechanizes the out-of-band fingerprint compare.
  --export [PATH]
      Write the keyring as known_hosts-shape text ("<agent> ed25519 <pubkey>"),
      to PATH or stdout (default) — a pasteable, diff-able backup.
  --import PATH
      Merge known_hosts-shape text into the keyring. Conflicting (different) keys
      are skipped, not silently overwritten, unless --rotate is given.

The keyring is a plain JSON file at ``<PA_HOME>/mesh-keyring.json`` — exactly
where ``sign.load_keyring`` reads it on verify. The entry shape is UNCHANGED from
v0.2 ({pubkey, added_at, added_by}). Public keys are non-secret and safe to share.

Usage:
  python comms/keyring.py --add alice --pubkey <b64> --expect-fingerprint <fp>
  python comms/keyring.py --export keyring.txt
  python comms/keyring.py --import keyring.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Local mesh libs live beside this script (sys.path.insert + bare import) so this
# runs as a plain script without packaging the comms/ dir — same idiom as send.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sign  # noqa: E402

# The only signature scheme today; emitted as the known_hosts keytype token so
# the export format is self-describing and forward-compatible.
KEYTYPE = "ed25519"


def _keyring_path() -> Path:
    """The keyring file location, resolved IDENTICALLY to sign.load_keyring()
    so this writer and the verify-side reader always agree on one path."""
    return sign._mesh_keys_dir().parent / "mesh-keyring.json"


def _write(kr: dict) -> None:
    # Atomic write (SF-A): serialize to a .tmp sibling, then os.replace onto the
    # real path. os.replace is atomic on the same filesystem, so a crash mid-write
    # leaves the PRIOR ring intact — never a half-written (corrupt) file, which is
    # exactly the failure load_keyring_strict() hard-stops on. The writer must not
    # be able to manufacture the corruption its own guard punishes.
    path = _keyring_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(kr, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _norm_fp(s: str) -> str:
    """Normalize a fingerprint for comparison: strip, lowercase, drop the
    'synnoesis-fp:' label if present (so the operator may paste it either way)."""
    s = s.strip().lower()
    if s.startswith(sign.FP_PREFIX):
        s = s[len(sign.FP_PREFIX):]
    return s


def _safe_fp(pubkey: str) -> str:
    try:
        return sign.pubkey_fingerprint(pubkey)
    except ValueError:
        return "(unfingerprintable)"


def _entry(pubkey: str) -> dict:
    return {
        "pubkey": pubkey,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": (os.environ.get("PA_AGENT_ID") or "").strip() or "local",
    }


def cmd_add(args) -> int:
    if not args.pubkey:
        print("ERROR: --add requires --pubkey", file=sys.stderr)
        return 2
    # Agent id is a single token in the keyring AND the export format ("<id>
    # ed25519 <pubkey>"); whitespace would corrupt the round-trip, so refuse it.
    if not args.add.strip() or any(c.isspace() for c in args.add):
        print(f"ERROR: agent id {args.add!r} must be non-empty and contain no "
              "whitespace.", file=sys.stderr)
        return 2
    # Validate the key on EVERY write (not only when --expect-fingerprint is
    # given), so a malformed key is never silently pinned (the write-side analog
    # of the doctor diagnostic).
    try:
        sign.decode_pubkey(args.pubkey)
    except ValueError as e:
        print(f"ERROR: --pubkey is not a valid Ed25519 public key: {e}",
              file=sys.stderr)
        return 2

    # (B) --expect-fingerprint gate: verify the key matches the fingerprint the
    # operator compared out-of-band, BEFORE pinning. Mismatch -> refuse.
    if args.expect_fingerprint:
        try:
            actual = sign.pubkey_fingerprint(args.pubkey)
        except ValueError as e:
            print(f"ERROR: --pubkey is not a valid public key: {e}",
                  file=sys.stderr)
            return 2
        if _norm_fp(actual) != _norm_fp(args.expect_fingerprint):
            print("ERROR: fingerprint MISMATCH — refusing to add.\n"
                  f"  expected (you):  {args.expect_fingerprint}\n"
                  f"  actual   (key):  {actual}\n"
                  "  The key does NOT match the fingerprint you verified out-of-\n"
                  "  band. Do not trust it; re-check the key with your peer.",
                  file=sys.stderr)
            return 2

    try:
        kr = sign.load_keyring_strict()
    except ValueError as e:
        print(f"ERROR: {e}\n  Refusing to overwrite — back up and inspect the "
              "file before retrying.", file=sys.stderr)
        return 2
    agents = kr.setdefault("agents", {})
    prior = agents.get(args.add)

    # (C) refuse-on-conflict: a DIFFERENT key for a known agent is the SSH
    # "host key changed" alarm — never overwrite silently. --rotate confirms.
    if (prior and prior.get("pubkey") and prior["pubkey"] != args.pubkey
            and not args.rotate):
        print("ERROR: refusing to overwrite an existing, DIFFERENT key for "
              f"'{args.add}'.\n"
              f"  pinned key fingerprint: {_safe_fp(prior['pubkey'])}\n"
              f"  new key fingerprint   : {_safe_fp(args.pubkey)}\n"
              "  A different key for a known agent can be a legitimate rotation\n"
              "  OR an impersonation. Verify the new key out-of-band, then\n"
              "  re-run with --rotate to confirm the identity change:\n"
              f"    python comms/keyring.py --add {args.add} "
              f"--pubkey {args.pubkey} --rotate",
              file=sys.stderr)
        return 2

    agents[args.add] = _entry(args.pubkey)
    _write(kr)

    if not prior:
        verb = "registered"
    elif prior.get("pubkey") == args.pubkey:
        verb = "re-registered (unchanged)"
    else:
        verb = "ROTATED (key replaced)"
    print(f"{args.add} {verb} in keyring: {_keyring_path()}")
    print(f"  fingerprint: {_safe_fp(args.pubkey)}")
    return 0


def cmd_export(args) -> int:
    # SF-B: strict load so --export on a CORRUPT ring refuses loudly instead of
    # the lenient loader silently emitting an empty/garbage "backup".
    try:
        kr = sign.load_keyring_strict()
    except ValueError as e:
        print(f"ERROR: {e}\n  Refusing to export a corrupt keyring (it would "
              "write an empty/garbage backup). Inspect the file first.",
              file=sys.stderr)
        return 2
    lines = [f"{aid} {KEYTYPE} {entry['pubkey']}"
             for aid, entry in sorted((kr.get("agents") or {}).items())
             if entry.get("pubkey")]
    text = "\n".join(lines) + ("\n" if lines else "")
    if args.export == "-":
        sys.stdout.write(text)
    else:
        Path(args.export).write_text(text, encoding="utf-8")
        print(f"exported {len(lines)} key(s) to {args.export}")
    return 0


def cmd_import(args) -> int:
    try:
        text = Path(args.imp).read_text(encoding="utf-8")
    except OSError as e:
        print(f"ERROR: cannot read {args.imp}: {e}", file=sys.stderr)
        return 2
    try:
        kr = sign.load_keyring_strict()
    except ValueError as e:
        print(f"ERROR: {e}\n  Refusing to overwrite — back up and inspect the "
              "file before retrying.", file=sys.stderr)
        return 2
    agents = kr.setdefault("agents", {})
    added = skipped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:  # exactly "<agent> <keytype> <pubkey>" — a stray
            print(f"  skip (need exactly 3 tokens): {line!r}", file=sys.stderr)
            skipped += 1   # token (e.g. a space-bearing id) would else corrupt
            continue
        aid, _keytype, pub = parts
        try:
            sign.decode_pubkey(pub)
        except ValueError:
            print(f"  skip (invalid key): {aid}", file=sys.stderr)
            skipped += 1
            continue
        prior = agents.get(aid)
        if prior and prior.get("pubkey") == pub:
            continue  # idempotent — already pinned
        if prior and prior.get("pubkey") and prior["pubkey"] != pub:
            if not args.rotate:
                print(f"  skip (conflict, use --rotate): {aid} "
                      f"[{_safe_fp(prior['pubkey'])} -> {_safe_fp(pub)}]",
                      file=sys.stderr)
                skipped += 1
                continue
            # --rotate replaces a DIFFERENT key — audit each rotation loudly.
            print(f"  ROTATE {aid}: {_safe_fp(prior['pubkey'])} -> "
                  f"{_safe_fp(pub)}", file=sys.stderr)
        agents[aid] = _entry(pub)
        added += 1
    _write(kr)
    print(f"imported {added} key(s), skipped {skipped}; keyring: {_keyring_path()}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="keyring",
        description="manage the Synnoesis mesh keyring (the trust decision)")
    ap.add_argument("--add", metavar="AGENT_ID",
                    help="register this agent id's public key")
    ap.add_argument("--pubkey", metavar="B64",
                    help="the agent's base64 Ed25519 public key (with --add)")
    ap.add_argument("--expect-fingerprint", metavar="FP",
                    dest="expect_fingerprint",
                    help="refuse --add unless the key's fingerprint matches this "
                         "(the out-of-band verify)")
    ap.add_argument("--rotate", action="store_true",
                    help="confirm replacing an existing DIFFERENT key. With "
                         "--add this is one key; with --import it is BULK — every "
                         "conflicting line is rotated (each audited to stderr)")
    ap.add_argument("--export", nargs="?", const="-", default=None, metavar="PATH",
                    help="export the keyring as known_hosts-shape text "
                         "(PATH, or stdout if omitted)")
    ap.add_argument("--import", dest="imp", default=None, metavar="PATH",
                    help="merge known_hosts-shape text into the keyring")
    args = ap.parse_args(argv)

    chosen = [name for name, on in (
        ("add", args.add is not None),
        ("export", args.export is not None),
        ("import", args.imp is not None),
    ) if on]
    if len(chosen) != 1:
        ap.error("pick exactly one of --add / --export / --import")
    if chosen[0] == "add":
        return cmd_add(args)
    if chosen[0] == "export":
        return cmd_export(args)
    return cmd_import(args)


if __name__ == "__main__":
    sys.exit(main())
