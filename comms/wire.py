"""wire.py — the single builder for the OUTER Synnoesis inbox record.

THE ONE PLACE the outer wire-record shape is defined. This mirrors how
``sign.new_message`` is the one place the INNER mesh-message envelope is
defined: there, every send path assembles the signed envelope through a
single function so the signed wire-format lives in exactly one place; here,
every path that writes an inbox record builds the OUTER record through a
single function so its shape lives in exactly one place too.

Two layers, two builders:
  * INNER envelope  — ``sign.new_message`` — the signed mesh message
                      (_from/_to/_at/_urgency/body/_nonce/_sig...).
  * OUTER record    — ``wire.wrap_outer`` (here) — the on-disk inbox record
                      that CARRIES one inner envelope plus delivery metadata.

Field-identity by construction (design decision): v0.1.0 has ONE writer of inbox
records — ``send.py`` (the file transport). Its record SHAPE is defined ONLY here,
so the reader (``inbox.py``) consumes it through one code path, and a future second
writer (a cross-machine transport) stays byte-for-byte identical by construction
rather than by hope. Do NOT hand-assemble an outer record anywhere else; call
``wrap_outer`` so a field change (or reorder) happens once, here, for every writer
at once.

The outer record has exactly four keys, in this order:
  topic        — the MQTT-style inbox topic the record belongs on,
                 ``"agent/<recipient>/inbox"`` where <recipient> is the inner
                 envelope's ``_to``. Derived from the inner envelope, never
                 self-declared by the caller, so the topic can't drift from
                 the actual recipient.
  received_at  — ISO-8601 UTC timestamp of when the record was built (i.e.
                 received / locally delivered). Distinct from the inner
                 envelope's ``_at`` (when the SENDER minted the message):
                 received_at is the DELIVERY clock, ``_at`` is the SEND clock.
  payload      — the inner envelope serialized as a JSON STRING (not a nested
                 object). The reader parses it back with ``json.loads``. Kept
                 as an opaque string so the signed bytes are preserved verbatim
                 end-to-end — re-serializing a parsed dict could reorder keys
                 and silently break a signature the reader still needs to verify.
  _verify      — the verification verdict for the inner envelope, ``{"status",
                 "detail"}``, using the same vocabulary as
                 ``sign.verify_against_keyring`` ('ok' / 'unsigned' / 'no-key' /
                 'bad' / 'unavailable'). The file transport records its own verdict
                 (it verifies the locally-minted, locally-signed message); a future
                 cross-machine transport would record the keyring verdict for a
                 received message — the same field, either way.

stdlib-only: ``json`` + ``datetime``. No third-party deps, no I/O — building a
record can never fail for a missing library or an unreachable broker.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def wrap_outer(inner: dict, status: str, detail: str) -> dict:
    """Build the canonical OUTER inbox record that carries one inner envelope.

    This is the SINGLE definition of the outer wire-record shape — every writer
    (``send.py`` today; a future transport tomorrow) calls it so their records are
    field-identical and the reader (``inbox.py``) needs exactly one parse path.

    Args:
        inner:   the inner mesh-message envelope (as produced by
                 ``sign.new_message``). Its ``_to`` field names the recipient
                 and drives the ``topic``; the whole dict is serialized into
                 ``payload``. A missing ``_to`` degrades to an empty recipient
                 ("agent//inbox") rather than raising — a malformed message
                 still lands as a record the reader can inspect.
        status:  the verification status for ``inner`` ('ok' / 'unsigned' /
                 'no-key' / 'bad' / 'unavailable' — the
                 ``sign.verify_against_keyring`` vocabulary).
        detail:  human-readable explanation of the status.

    Returns:
        A dict with exactly four keys — ``topic``, ``received_at``,
        ``payload`` (a JSON STRING of ``inner``), and ``_verify``
        (``{"status", "detail"}``) — ready to write to the inbox store.
    """
    return {
        "topic": "agent/" + str(inner.get("_to", "")) + "/inbox",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": json.dumps(inner, ensure_ascii=False),
        "_verify": {"status": status, "detail": detail},
    }
