"""Gateway configuration (env-driven)."""
from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Where the label-blind database lives.
MANTLE_URI = os.getenv("MANTLE_URI", "http://localhost:8081").rstrip("/")

# Optional server credential for user-less / service calls (delegation auth is
# preferred — the gateway forwards the caller's bearer token where present).
MANTLE_API_KEY = os.getenv("MANTLE_API_KEY", "")

# The content_type that identifies a type-definition artifact in Mantle.
TYPE_CONTENT_TYPE = os.getenv("TYPE_CONTENT_TYPE", "application/vnd.agience.type+json")

# Listen port.
CRYSTAL_PORT = _int("CRYSTAL_PORT", 8085)
CRYSTAL_HOST = os.getenv("CRYSTAL_HOST", "0.0.0.0")

# Crystal service identity + event-driven describers. KEYS_DIR holds
# crystal.private.pem (written by init when the gateway joined the trust mesh);
# ORIGIN_URI is where the gateway mints delegations. If the key is absent the
# event-driven describer is disabled (the inline, gateway-mediated one still runs).
KEYS_DIR = os.getenv("KEYS_DIR", "")
ORIGIN_URI = os.getenv("ORIGIN_URI", "http://localhost:8080").rstrip("/")
EVENTS_ENABLED = os.getenv("CRYSTAL_EVENTS_ENABLED", "1").lower() not in ("0", "false", "no")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Embeddings host (prism) — Crystal fronts it so embeddings route through the gateway.
EMBEDDINGS_URI = os.getenv("EMBEDDINGS_URI", "").rstrip("/")
EMBEDDINGS_API_KEY = os.getenv("EMBEDDINGS_API_KEY", "")
