#!/usr/bin/env python3
"""sign.py — Ed25519 message signing for the Synnoesis mesh.

Shared by the send and receive paths, so the signed wire-format is defined in
exactly one place.

Identity model: each agent holds an Ed25519 private key on its own machine
(NEVER transmitted). Its public key lives in the host-maintained keyring
(state/mesh-keyring.json). A message is signed over a canonical
serialization of its identity-bearing fields; a recipient verifies the
signature against the claimed sender's public key from the keyring. A node
cannot forge another agent's signature without that agent's private key.

Crypto: Ed25519 via the `cryptography` library. Keys + signatures are
base64 for JSON transport.

Import-safe: if `cryptography` is missing this module still imports with
CRYPTO_AVAILABLE = False — callers degrade gracefully (sign() -> None,
verify_against_keyring() -> 'unavailable') so a missing dependency can
never crash a caller.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey)
    from cryptography.hazmat.primitives import serialization
    CRYPTO_AVAILABLE = True
except Exception:  # noqa: BLE001 — a missing dep must not crash importers
    CRYPTO_AVAILABLE = False

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # sibling: portable data-home resolver (no hardcoded host paths)

SIG_ALG = "ed25519"

# Message fields covered by the signature. Canonical form sorts keys, so
# order is irrelevant. `_to` (recipient agent id) is bound in so a captured
# message cannot be replayed to a different recipient.
SIGNED_FIELDS = ("_from", "_to", "_at", "_urgency", "body", "_nonce")


def canonical(msg: dict) -> bytes:
    """Deterministic byte serialization of a message's signed fields.
    Signer and verifier MUST produce identical bytes — both call this."""
    subset = {k: msg.get(k) for k in SIGNED_FIELDS}
    return json.dumps(subset, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


# ---- key generation / loading -------------------------------------------

def generate_keypair() -> tuple[str, str]:
    """Return (private_b64, public_b64) for a fresh Ed25519 keypair."""
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography library not available")
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(serialization.Encoding.Raw,
                                  serialization.PrivateFormat.Raw,
                                  serialization.NoEncryption())
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return (base64.b64encode(priv_raw).decode("ascii"),
            base64.b64encode(pub_raw).decode("ascii"))


def _load_private(private_b64: str):
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))


def _load_public(public_b64: str):
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))


# ---- public-key fingerprint (stdlib-only; works without `cryptography`) ---
# A short, stable string two agents compare OUT-OF-BAND to confirm they hold the
# same key before pinning it (the Signal "safety number" idea). It is sha256 over
# the RAW 32-byte Ed25519 public key (base64-decode the stored pubkey), hex-
# encoded, behind a `synnoesis-fp:` label.
#
# Deliberately NOT OpenSSH-compatible: `ssh-keygen -lf` hashes the ssh-ed25519
# WIRE BLOB and base64-no-pad encodes the digest — different bytes. Claiming
# interop and being subtly wrong would train operators to ignore fingerprint
# mismatches, the exact anti-pattern a fingerprint exists to stop. Two Synnoesis
# agents compute THIS identically, which is all an OOB compare needs. Pure
# hashlib -> available even when `cryptography` is not installed.
FP_PREFIX = "synnoesis-fp:"


def decode_pubkey(public_b64: str) -> bytes:
    """base64-decode an Ed25519 public key and assert it is the right size.
    Raises ValueError on bad base64 OR a wrong length (an Ed25519 raw public key
    is exactly 32 bytes). This is the ONE validator every WRITE path should run
    so a malformed key can never be silently pinned."""
    try:
        raw = base64.b64decode(public_b64, validate=True)
    except Exception as e:  # noqa: BLE001 — malformed base64 -> ValueError
        raise ValueError(f"not a valid base64 public key: {e}") from e
    if len(raw) != 32:
        raise ValueError(f"Ed25519 public key must be 32 bytes, got {len(raw)}")
    return raw


def pubkey_fingerprint(public_b64: str) -> str:
    """Return 'synnoesis-fp:<hex sha256 of the raw 32-byte pubkey>' for the
    base64 Ed25519 public key `public_b64`. Raises ValueError if the input is
    not a valid 32-byte Ed25519 public key."""
    return FP_PREFIX + hashlib.sha256(decode_pubkey(public_b64)).hexdigest()


# ---- signature domain separation (crypto-design v0.2 §3a) ----------------
# One Ed25519 identity key signs MULTIPLE protocols. A bare signature over
# canonical bytes carries no "which protocol am I" label, so signed bytes
# minted for one protocol could in principle be coerced to validate in
# another (cross-protocol confusion). Fix:
# tagged signatures sign DS(tag) || payload, where DS(tag) is the tag
# LENGTH-PREFIXED (prefix-free, unambiguous — no delimiter games).
#
# MIGRATION SAFETY (the disjointness proof): legacy
# signed-bytes are canonical() JSON and therefore ALWAYS begin 0x7B ('{');
# DS-tagged bytes ALWAYS begin 0x00 (uint16_be length high byte — every tag
# is < 256 chars). The two byte-domains are disjoint at byte 0, so a legacy
# signature can never validate as a tagged one or vice versa. Dual-accept
# during the overlap window is provably safe. Ladder: (1) verifiers accept
# BOTH mesh-wide (this code — accept-widening ships consumer-first), then
# (2) producers emit tagged, then (3) enforcement rejects untagged.
#
# Tag registry — the verifier picks the tag for the message type it expects
# on a topic; it must NEVER trust a tag the message self-declares.

DS_TAG_ENVELOPE = "synnoesis/v1/envelope"        # v1 directed message (new_message)
DS_TAG_CONTROL = "synnoesis/control/v1"          # pa_control commands
DS_TAG_BLESSED_VOTE = "synnoesis/blessed-vote/v1"  # blessed-version manifest votes
DS_TAG_PRESENCE = "synnoesis/presence/v1"        # bridge state/presence publishes
DS_TAG_ENCMSG = "synnoesis/v2/encmsg"            # v2 encrypted envelope

DS_TAG_INFLIGHT = "synnoesis/inflight/v1"        # agent self-reported in-flight status

KNOWN_DS_TAGS = (DS_TAG_ENVELOPE, DS_TAG_CONTROL, DS_TAG_BLESSED_VOTE,
                 DS_TAG_PRESENCE, DS_TAG_ENCMSG, DS_TAG_INFLIGHT)

# Signature-meta fields — the signature triple itself, never part of the
# bytes being signed.
_SIG_META = ("_sig", "_sig_alg", "_key_id")


def ds_prefix(tag: str) -> bytes:
    """Length-prefixed domain tag: uint16_be(len(tag)) || tag. Prefix-free by
    construction; first byte is 0x00 for every tag under 256 chars, which is
    what makes tagged bytes provably disjoint from legacy canonical() JSON
    (always 0x7B). Raises on tags that would break that property."""
    raw = tag.encode("utf-8")
    if not raw or len(raw) > 255:
        raise ValueError(f"ds tag length {len(raw)} outside 1..255: {tag!r}")
    return len(raw).to_bytes(2, "big") + raw


# ---- sign / verify ------------------------------------------------------

def sign(msg: dict, private_b64: str, ds_tag: str | None = None) -> str | None:
    """Sign a message's canonical form. Returns the base64 signature, or
    None if crypto is unavailable (caller then sends unsigned — the
    warn-phase verifier tolerates that).

    ds_tag=None — legacy bytes (canonical only), today's wire format.
    ds_tag=<tag> — domain-separated: sign(DS(tag) || canonical). Producers
    flip to tagged ONLY after dual-accept verifiers are mesh-wide (§3a ladder)."""
    if not CRYPTO_AVAILABLE:
        return None
    payload = canonical(msg)
    if ds_tag is not None:
        payload = ds_prefix(ds_tag) + payload
    return base64.b64encode(
        _load_private(private_b64).sign(payload)).decode("ascii")


def attach_signature(msg: dict, private_b64: str, key_id: str = "",
                     ds_tag: str | None = None) -> dict:
    """Add _sig / _sig_alg / _key_id to msg in place and return it. If crypto
    is unavailable, msg is returned unchanged (unsigned)."""
    s = sign(msg, private_b64, ds_tag=ds_tag)
    if s is not None:
        msg["_sig"] = s
        msg["_sig_alg"] = SIG_ALG
        if key_id:
            msg["_key_id"] = key_id
    return msg


def verify(msg: dict, public_b64: str,
           expected_tag: str | None = None) -> bool:
    """True iff msg['_sig'] is a valid Ed25519 signature over msg's canonical
    form by public_b64. Any error (bad sig, bad key, bad base64) -> False.

    expected_tag=None — legacy-only verify (today's behavior, unchanged).
    expected_tag=<tag> — DUAL-ACCEPT for the §3a overlap window: accept a
    signature over DS(tag)||canonical OR over legacy canonical bytes. The
    byte-domains are disjoint (see ds_prefix), so exactly one form can match
    a given signature; accepting both adds no cross-protocol surface. The
    tag comes from the CALLER's context (topic / message type) — never from
    the message itself."""
    if not CRYPTO_AVAILABLE:
        return False
    sig = msg.get("_sig")
    if not sig:
        return False
    try:
        raw_sig = base64.b64decode(sig)
        pub = _load_public(public_b64)
        base = canonical(msg)
    except Exception:  # noqa: BLE001 — any failure means "not verified"
        return False
    if expected_tag is not None:
        try:
            pub.verify(raw_sig, ds_prefix(expected_tag) + base)
            return True
        except Exception:  # noqa: BLE001 — fall through to legacy form
            pass
    try:
        pub.verify(raw_sig, base)
        return True
    except Exception:  # noqa: BLE001 — any failure means "not verified"
        return False


# ---- RECORD signing (state / presence / inflight) -----------------------
# The message path above signs only SIGNED_FIELDS (the envelope subset:
# _from/_to/_at/_urgency/body/_nonce). A STATE/PRESENCE/INFLIGHT record has
# NONE of those — its meaning lives in agent_id/task/phase/online/etc. So
# signing such a record via canonical() signs an all-null subset: a CONSTANT
# that authenticates nothing (forgeable). These helpers sign the WHOLE record
# (all fields except the signature triple), so the content is actually bound.
# Record sender id is the `agent_id` field (records have no `_from`).
# (Latent: the bridge presence path still uses the message helpers — flagged
# for a separate mesh-wide fix; inflight uses these from day one.)

def canonical_record(payload: dict) -> bytes:
    """Deterministic bytes over ALL record fields except the signature
    triple. Signer and verifier MUST produce identical bytes."""
    subset = {k: v for k, v in payload.items() if k not in _SIG_META}
    return json.dumps(subset, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def attach_record_signature(payload: dict, private_b64: str, key_id: str = "",
                            ds_tag: str | None = None) -> dict:
    """Sign a full record in place (adds _sig/_sig_alg/_key_id). ds_tag adds
    §3a domain separation. No-op (returns payload unchanged) if crypto is
    unavailable."""
    if not CRYPTO_AVAILABLE:
        return payload
    data = canonical_record(payload)
    if ds_tag is not None:
        data = ds_prefix(ds_tag) + data
    payload["_sig"] = base64.b64encode(
        _load_private(private_b64).sign(data)).decode("ascii")
    payload["_sig_alg"] = SIG_ALG
    if key_id:
        payload["_key_id"] = key_id
    return payload


def verify_record(payload: dict, public_b64: str,
                  expected_tag: str | None = None) -> bool:
    """True iff payload['_sig'] validly signs the full record (dual-accept
    tagged|legacy during the §3a overlap window). Any error -> False."""
    if not CRYPTO_AVAILABLE:
        return False
    sig = payload.get("_sig")
    if not sig:
        return False
    try:
        raw_sig = base64.b64decode(sig)
        pub = _load_public(public_b64)
        data = canonical_record(payload)
    except Exception:  # noqa: BLE001
        return False
    if expected_tag is not None:
        try:
            pub.verify(raw_sig, ds_prefix(expected_tag) + data)
            return True
        except Exception:  # noqa: BLE001 — fall through to legacy form
            pass
    try:
        pub.verify(raw_sig, data)
        return True
    except Exception:  # noqa: BLE001
        return False


def verify_record_against_keyring(payload: dict, keyring: dict,
                                  expected_tag: str | None = None
                                  ) -> tuple[str, str]:
    """Record analog of verify_against_keyring. The claimed sender is the
    record's `agent_id` field. Returns the same (status, detail) vocabulary."""
    if not CRYPTO_AVAILABLE:
        return "unavailable", "cryptography library not installed"
    sender = payload.get("agent_id") or "?"
    if not payload.get("_sig"):
        return "unsigned", f"no signature from {sender}"
    entry = (keyring.get("agents") or {}).get(sender)
    if not entry or not entry.get("pubkey"):
        return "no-key", f"{sender} is not in the keyring"
    if verify_record(payload, entry["pubkey"], expected_tag=expected_tag):
        return "ok", f"valid record signature from {sender}"
    return "bad", f"record signature from {sender} FAILED verification"


def verify_against_keyring(msg: dict, keyring: dict,
                           expected_tag: str | None = None) -> tuple[str, str]:
    """Check msg's signature against the claimed sender's keyring entry.
    Returns (status, detail), status in:
      'ok'          — valid signature
      'unsigned'    — no _sig present (un-upgraded sender — warn phase)
      'no-key'      — sender absent from the keyring
      'bad'         — _sig present but does NOT verify (forgery / tamper)
      'unavailable' — cryptography library missing on this host

    expected_tag: §3a domain tag for the message type this caller expects on
    this topic (dual-accept with legacy during the overlap window). Derived
    from caller context, never from the message.
    """
    if not CRYPTO_AVAILABLE:
        return "unavailable", "cryptography library not installed"
    sender = msg.get("_from") or "?"
    if not msg.get("_sig"):
        return "unsigned", f"no signature from {sender}"
    entry = (keyring.get("agents") or {}).get(sender)
    if not entry or not entry.get("pubkey"):
        return "no-key", f"{sender} is not in the keyring"
    if verify(msg, entry["pubkey"], expected_tag=expected_tag):
        return "ok", f"valid signature from {sender}"
    return "bad", f"signature from {sender} FAILED verification"


# ---- key loading + message assembly (Phase 2.2: sign on send) ------------

def _mesh_keys_dir() -> Path:
    """Mesh-keys dir under the resolved data home (paths.pa_home(): PA_HOME, else
    ~/.synnoesis). No hardcoded host path — the keyring lives at pa_home()/mesh-keyring.json
    (one level up), so both default to the same gitignored data root as every other service."""
    return paths.pa_home() / "mesh-keys"


def load_private_key(agent_id: str) -> str | None:
    """Return the base64 private key for `agent_id` from this machine's
    mesh-keys dir, or None if there is no key here. None is not an error:
    the caller then sends UNSIGNED, which the warn-phase verifier tolerates."""
    try:
        return (_mesh_keys_dir() / f"{agent_id}.key").read_text(
            encoding="utf-8").strip()
    except OSError:
        return None


def load_keyring() -> dict:
    """Load the mesh public-key ring from this machine's state dir. Returns
    the parsed dict ({"agents": {...}}), or {"agents": {}} when the file is
    absent or unreadable - an empty ring makes every signed sender verify as
    'no-key', which the warn-phase verifier tolerates."""
    path = _mesh_keys_dir().parent / "mesh-keyring.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"agents": {}}
    except (OSError, ValueError):
        return {"agents": {}}


def load_keyring_strict() -> dict:
    """Like load_keyring, but for callers that must NOT silently build on a
    corrupt ring (writers, diagnostics). Returns {"agents": {}} for a MISSING
    file, but RAISES ValueError if the file exists and is unreadable / unparseable
    / structurally wrong — so a transient corruption is surfaced, never silently
    overwritten (which would destroy every pinned key, the worst failure for a
    trust store)."""
    path = _mesh_keys_dir().parent / "mesh-keyring.json"
    if not path.exists():
        return {"agents": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"keyring at {path} is unreadable/corrupt: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("agents", {}), dict):
        raise ValueError(f"keyring at {path} is structurally invalid")
    data.setdefault("agents", {})
    return data


def new_message(from_id: str, to_id: str, body: str,
                urgency: str = "normal") -> dict:
    """Assemble a mesh-message envelope and sign it with from_id's private
    key, if one is available on this machine. Returns the complete dict,
    ready to json.dumps + publish.

    Shared by every send path so the signed wire-format is defined in exactly
    one place. If no private key
    (or no cryptography) is available the message is returned UNSIGNED; a
    send never fails for lack of a key, and the warn-phase verifier accepts
    unsigned messages during the rollout."""
    msg = {
        "_urgency": urgency,
        "_from": from_id,
        "_to": to_id,
        "_at": datetime.now(timezone.utc).isoformat(),
        "_nonce": secrets.token_hex(8),
        "body": body,
    }
    priv = load_private_key(from_id)
    if priv:
        attach_signature(msg, priv, key_id=from_id)
    return msg
