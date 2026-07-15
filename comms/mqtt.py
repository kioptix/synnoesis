#!/usr/bin/env python3
"""mqtt.py — the cross-machine transport for the Synnoesis mesh (v0.4.0).

The FLOOR transport is a shared file (``send.py`` appends, ``inbox.py`` reads); it
needs no infrastructure and stays the default. THIS module adds the CEILING: an
MQTT broker so two machines can exchange the SAME signed messages. It is the one
place the broker connection, its security posture, and transport-selection live —
``send.py`` (publisher) and ``listen.py`` (subscriber/bridge) both call in here.

Design invariants (why this file is shaped the way it is):

  * **The broker is transport-only; it is NOT trusted.** A message's authenticity
    comes from its Ed25519 signature, verified by the RECIPIENT against its own
    keyring (see ``listen.py``) — never from the broker and never from a sender's
    self-reported verdict. A malicious/compromised broker can withhold or reorder,
    but it cannot forge a signature.

  * **Signing ≠ encryption (state it plainly).** Synnoesis signs (authenticity +
    integrity). TLS (``SYN_BROKER_TLS_CA``) protects messages *in transit*. Neither
    hides content FROM THE BROKER — a broker sees plaintext message bodies. End-to-
    end content-confidentiality is a NON-GOAL of v0.4.0 (the tag registry reserves
    ``synnoesis/v2/encmsg`` for a future encrypted envelope). The security-refusal
    text below says so, so an operator can't miss it.

  * **Fail closed off-box.** A non-loopback broker without TLS is REFUSED unless the
    operator explicitly opts in with ``SYN_ALLOW_PLAINTEXT=1`` — and even then every
    connection warns. A leftover ``SYN_BROKER`` must never silently leak content onto
    an unencrypted network.

  * **No secrets in argv.** The broker password is read from a FILE
    (``SYN_BROKER_PASSFILE``), never a command-line flag — process lists are world-
    readable on most hosts.

Environment surface (all NEW vars are ``SYN_*`` from day one):
  SYN_BROKER           host[:port]  — enables the MQTT transport (unset ⇒ file only).
  SYN_BROKER_TLS_CA    path to a CA cert  — enables TLS, verifies the broker cert.
  SYN_BROKER_USER      broker username (optional).
  SYN_BROKER_PASSFILE  path to a file whose contents are the broker password.
  SYN_ALLOW_PLAINTEXT  '1' ⇒ permit a non-loopback broker WITHOUT TLS (warns every
                       connect). For trusted encrypted underlays (WireGuard/tailnet).

``paho-mqtt`` is an OPTIONAL dependency (``pip install synnoesis[mqtt]``); it is
imported lazily so the file-transport floor stays stdlib-only. All the config +
security logic here is pure and importable WITHOUT paho, so it is unit-testable
with no broker and no third-party dep.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass


class SynBrokerError(Exception):
    """A broker mis-configuration or a refused (unsafe) transport. Fail-closed."""


# Loopback targets are not a network exposure, so plaintext to them is fine
# (a broker on the same host, common for local multi-agent testing).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class BrokerConfig:
    host: str
    port: int
    tls_ca: str | None = None
    user: str | None = None
    passfile: str | None = None
    allow_plaintext: bool = False

    @property
    def use_tls(self) -> bool:
        return bool(self.tls_ca)

    @property
    def is_loopback(self) -> bool:
        return self.host.strip().lower() in LOOPBACK_HOSTS


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() not in ("", "0", "false", "no", "off")


def _parse_hostport(raw: str, default_port: int) -> tuple[str, int]:
    """Parse 'host', 'host:port', 'ipv6' (bare), or '[ipv6]:port'. Missing port ⇒
    default_port.

    IPv6 note: a bare IPv6 address contains multiple colons (``::1``,
    ``fe80::1``), so it is NOT split on the last colon — that would mangle it into
    host ``::`` port ``1``. To attach a port to an IPv6 literal, bracket it
    (``[::1]:8883``), the standard URL form. Only a single-colon string is treated
    as ``host:port``."""
    raw = raw.strip()
    if raw.startswith("["):                       # bracketed IPv6: [::1]:8883
        host, _, rest = raw[1:].partition("]")
        port = rest.lstrip(":")
        return host, int(port) if port.isdigit() else default_port
    if raw.count(":") > 1:                         # bare IPv6, no port
        return raw, default_port
    host, sep, maybe = raw.rpartition(":")
    if sep and maybe.isdigit():
        return host, int(maybe)
    return raw, default_port                       # no port component


def resolve_broker(env: dict | None = None) -> BrokerConfig | None:
    """Build a BrokerConfig from the environment, or None if ``SYN_BROKER`` is
    unset/blank (⇒ the file transport, unchanged). Pure: reads only ``env``."""
    e = os.environ if env is None else env
    raw = (e.get("SYN_BROKER") or "").strip()
    if not raw:
        return None
    tls_ca = (e.get("SYN_BROKER_TLS_CA") or "").strip() or None
    # MQTT convention: 8883 for TLS, 1883 plaintext — used only when the operator
    # omitted an explicit port in SYN_BROKER.
    host, port = _parse_hostport(raw, default_port=8883 if tls_ca else 1883)
    return BrokerConfig(
        host=host,
        port=port,
        tls_ca=tls_ca,
        user=(e.get("SYN_BROKER_USER") or "").strip() or None,
        passfile=(e.get("SYN_BROKER_PASSFILE") or "").strip() or None,
        allow_plaintext=_truthy(e.get("SYN_ALLOW_PLAINTEXT")),
    )


def check_transport_security(cfg: BrokerConfig, *, warn=_stderr) -> None:
    """Enforce the off-box confidentiality floor. Returns None if the transport is
    acceptable; RAISES ``SynBrokerError`` if it is an unencrypted off-box connection
    that the operator has not explicitly allowed. WARNS (every call — never a one-
    time note) when plaintext-off-box IS explicitly allowed, so a user who set the
    flag once cannot silently ride plaintext forever."""
    if cfg.use_tls or cfg.is_loopback:
        return
    if not cfg.allow_plaintext:
        raise SynBrokerError(
            f"refusing to connect to broker {cfg.host}:{cfg.port} over PLAINTEXT "
            "(no TLS). Off-box broker traffic must be encrypted.\n"
            "  • Set SYN_BROKER_TLS_CA=<ca.pem> to use TLS, OR\n"
            "  • Set SYN_ALLOW_PLAINTEXT=1 ONLY on a trusted encrypted underlay "
            "(WireGuard / tailnet).\n"
            "Note: TLS protects messages in transit but the BROKER still sees "
            "content — Synnoesis signs (authenticity), it does not encrypt "
            "(confidentiality). Do not send secrets you would not show the broker.")
    warn(f"WARNING [SYN_ALLOW_PLAINTEXT]: connecting to {cfg.host}:{cfg.port} "
         "WITHOUT TLS. Safe ONLY on a trusted encrypted network (WireGuard/tailnet). "
         "The broker sees message content in plaintext.")


def read_passfile(path: str, *, warn=_stderr) -> str:
    """Read a broker password from ``path`` (never argv — process lists leak). Warns
    if the file is group/other-readable (POSIX) or unverifiable (Windows ACLs).
    Raises ``SynBrokerError`` on a missing/empty/unreadable file."""
    from pathlib import Path
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise SynBrokerError(f"cannot read SYN_BROKER_PASSFILE {path!r}: {e}") from e
    pw = text.strip()
    if not pw:
        raise SynBrokerError(f"SYN_BROKER_PASSFILE {path!r} is empty")
    try:
        mode = p.stat().st_mode
        if os.name == "nt":
            warn(f"note: cannot verify permissions of {p} on Windows (chmod is a "
                 "no-op); ensure the file's ACL restricts it to your user.")
        elif mode & 0o077:
            warn(f"WARNING: broker passfile {p} is group/other-accessible "
                 f"(mode {oct(mode & 0o777)}); tighten it with `chmod 600 {p}`.")
    except OSError:
        pass
    return pw


def select_transport(*, local: bool, via: str | None, broker_configured: bool) -> str:
    """Pure transport selection. Returns 'file' or 'mqtt'.

      --local           ⇒ always 'file' (force the floor).
      --via file|mqtt   ⇒ honor it; 'mqtt' with no broker configured is an ERROR
                          (never silently fall back to file when mqtt was demanded).
      (default, 'auto') ⇒ 'mqtt' if SYN_BROKER is configured, else 'file'.
    """
    if local:
        return "file"
    if via in ("file", "local"):
        return "file"
    if via == "mqtt":
        if not broker_configured:
            raise SynBrokerError(
                "--via mqtt requested but SYN_BROKER is not set (nothing to connect "
                "to). Set SYN_BROKER=host[:port], or drop --via to use the file "
                "transport.")
        return "mqtt"
    if via not in (None, "auto"):
        raise SynBrokerError(f"unknown --via {via!r} (expected auto|file|mqtt)")
    return "mqtt" if broker_configured else "file"


def announce_line(cfg: BrokerConfig) -> str:
    """The one-line transport announcement printed on EVERY MQTT send, so an auto-
    selected network egress is never silent (a leftover SYN_BROKER can't leak by
    surprise)."""
    sec = "TLS" if cfg.use_tls else ("plaintext" if cfg.is_loopback else "PLAINTEXT")
    return f"→ via broker {cfg.host}:{cfg.port} ({sec})  [--local for the file transport]"


# ---- paho-touching layer (lazy import; not needed for the pure logic above) ----

def require_paho():
    """Import and return ``paho.mqtt.client`` or raise a clear install hint. Lazy so
    the file-transport floor never needs the optional dependency."""
    try:
        import paho.mqtt.client as mqtt  # noqa: PLC0415 — intentional lazy import
        return mqtt
    except Exception as e:  # noqa: BLE001
        raise SynBrokerError(
            "the MQTT transport needs 'paho-mqtt', which is not installed.\n"
            "  install it with:  pip install 'paho-mqtt>=2.0'   (or, from a package "
            "install, pip install 'synnoesis[mqtt]')\n"
            "  or use the file transport with --local.") from e


def _new_client(mqtt, cfg: BrokerConfig, client_id: str):
    """Build a paho Client (callback API v2) wired for TLS + auth per ``cfg``. Does
    NOT connect — the caller connects (publisher one-shot, subscriber loop)."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if cfg.use_tls:
        import ssl
        client.tls_set(ca_certs=cfg.tls_ca, cert_reqs=ssl.CERT_REQUIRED,
                       tls_version=ssl.PROTOCOL_TLS_CLIENT)
        # No tls_insecure_set(True): skip-verify + credentials in flight is exactly
        # how tokens leak — there is deliberately NO opt-out here.
    if cfg.passfile and not cfg.user:
        # A passfile without a username is a silent no-op: MQTT auth is username-keyed,
        # so the password is never sent. Warn rather than let auth quietly fail.
        _stderr("WARNING: SYN_BROKER_PASSFILE is set but SYN_BROKER_USER is not — "
                "MQTT auth is username-keyed, so the password is IGNORED. Set "
                "SYN_BROKER_USER to authenticate with the broker.")
    if cfg.user:
        pw = read_passfile(cfg.passfile) if cfg.passfile else None
        client.username_pw_set(cfg.user, pw)
    return client


def publish_one(cfg: BrokerConfig, topic: str, payload: bytes, *,
                qos: int = 1, client_id: str, timeout: float = 15.0) -> None:
    """Connect, publish ONE message, block until the broker acks (qos1), disconnect.
    Enforces the security floor first. Raises ``SynBrokerError`` on any failure so a
    caller never believes an unsent message was delivered."""
    check_transport_security(cfg)
    mqtt = require_paho()
    client = _new_client(mqtt, cfg, client_id)
    try:
        client.connect(cfg.host, cfg.port, keepalive=30)
    except Exception as e:  # noqa: BLE001 — unreachable broker, TLS failure, auth
        raise SynBrokerError(
            f"cannot connect to broker {cfg.host}:{cfg.port}: {e}") from e
    try:
        client.loop_start()
        info = client.publish(topic, payload, qos=qos)
        info.wait_for_publish(timeout=timeout)
        if not info.is_published():
            raise SynBrokerError(
                f"publish to {topic} was not acknowledged within {timeout:.0f}s "
                "(broker reachable but did not confirm delivery)")
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001 — cleanup best-effort
            pass


def open_subscriber(cfg: BrokerConfig, topic: str, on_message, *,
                    client_id: str, on_ready=None):
    """Connect a subscriber to ``topic`` (qos1) with ``on_message(client, userdata,
    message)``. Enforces the security floor first. Returns the connected client; the
    caller runs ``client.loop_forever()``. ``on_ready(host, topic)`` fires once the
    subscription is confirmed."""
    check_transport_security(cfg)
    mqtt = require_paho()
    client = _new_client(mqtt, cfg, client_id)

    def _on_connect(_c, _u, _flags, reason_code, _props=None):
        rc = int(getattr(reason_code, "value", reason_code) or 0)
        if rc != 0:
            _stderr(f"error: broker refused the connection (reason {rc}); "
                    "check auth / TLS / credentials.")
            return
        client.subscribe(topic, qos=1)
        if on_ready:
            on_ready(cfg.host, topic)

    client.on_connect = _on_connect
    client.on_message = on_message
    try:
        client.connect(cfg.host, cfg.port, keepalive=30)
    except Exception as e:  # noqa: BLE001
        raise SynBrokerError(
            f"cannot connect to broker {cfg.host}:{cfg.port}: {e}") from e
    return client
