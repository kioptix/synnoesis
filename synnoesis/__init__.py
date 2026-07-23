"""Synnoesis -- minds thinking together.

A personal-assistant system where multiple AI agents confer and collaborate
to solve problems together.

Transports: a file-backed floor (one machine, standard library only) and an
optional MQTT broker for cross-machine delivery. Messages are Ed25519-signed and
verified locally against the receiver's own keyring — the broker moves bytes, it
never vouches for identity.

Copyright 2026 Groupe Kioptix Inc. Released under the Apache License 2.0.
"""

__version__ = "0.5.0"
