"""How the crystal process formats its own logs. Pure stdlib, no dependency.

crystal is Apache-2.0; origin is copyleft (AGPL). This is crystal's own copy of the uvicorn
dictConfig `crystal.host.run` needs, rather than crystal importing `origin.logging_utils` — the AGPL
components are sinks that nothing imports.

Copied rather than shared, because nothing in the system reads another component's log output: origin
and mantle do not share this config either, each carrying its own byte-identical `logging_utils.py`
beside its own `uvicorn_log_config.json`, differing only in the dotted prefix naming their own
package. Not copied: `SuppressNoisyAccessFilter` (referenced by no json config and no code in any
repo) and `configure_logging()` (zero callers in crystal; `host.py` hands the dict to `uvicorn.run`
directly).

A Python dict literal rather than a `.json` side-car, because this repo declares no `package-data` —
a JSON file would read correctly from a source checkout but raise `FileNotFoundError` on an installed
wheel. It is otherwise origin's dict exactly, with `origin.logging_utils` rewritten to
`crystal.logging_config`.
"""

from __future__ import annotations

import logging
import time


class RedactAccessQueryFilter(logging.Filter):
    """Remove query strings from uvicorn access logs.

    Uvicorn's access logger includes the raw request target, which can contain
    sensitive query parameters (OAuth `code`, share tokens, etc.). Strip the
    query string so these values never land in stdout logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            args = record.args
            # Uvicorn access log args: (client_addr, method, path, http_version, status_code)
            # Uvicorn error WebSocket args: (client_addr, path_with_query, status)
            # Scan all tuple elements and strip query strings from any that look
            # like URL paths (start with "/" and contain "?").
            if not isinstance(args, tuple):
                return True
            new_args = list(args)
            changed = False
            for i, arg in enumerate(new_args):
                if isinstance(arg, str) and arg.startswith("/") and "?" in arg:
                    new_args[i] = arg.split("?", 1)[0]
                    changed = True
            if changed:
                record.args = tuple(new_args)
        except Exception:
            # Never break logging.
            pass
        return True


class UTCFormatter(logging.Formatter):
    """Logging formatter that renders %(asctime)s in UTC."""

    converter = time.gmtime


# The dictConfig itself. `disable_existing_loggers` is false so loggers created by earlier imports
# keep working — `crystal.host` calls `logging.basicConfig` at module scope, long before this is
# applied.
#
# The `()` values are dotted strings, not the class objects, so that this dict stays JSON-shaped and
# comparable to the origin/mantle configs it was lifted from. This module must therefore be
# importable as `crystal.logging_config` when `dictConfig` runs — true for every way crystal is
# started, and pinned by `test_logging_config.py`, which resolves the strings rather than trusting
# them.
_LOG_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_access_query": {
            "()": "crystal.logging_config.RedactAccessQueryFilter",
        },
    },
    "formatters": {
        "default": {
            "()": "crystal.logging_config.UTCFormatter",
            "fmt": "%(asctime)s.%(msecs)03dZ %(levelname)s - %(name)s - "
                   "%(filename)s:%(lineno)d - %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
        "access": {
            "()": "crystal.logging_config.UTCFormatter",
            "fmt": "%(asctime)s.%(msecs)03dZ %(levelname)s - %(name)s - %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "filters": ["redact_access_query"],
            "stream": "ext://sys.stdout",
        },
        "access": {
            "class": "logging.StreamHandler",
            "formatter": "access",
            "filters": ["redact_access_query"],
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {
            "handlers": ["default"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.error": {
            "level": "INFO",
        },
        "uvicorn.access": {
            "handlers": ["access"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["default"],
        "level": "INFO",
    },
}


def build_log_config() -> dict:
    """Return the logging dictConfig for the crystal host process.

    A fresh deep-ish copy each call: `dictConfig` mutates the mapping it is handed (it pops `()`
    while constructing each factory), so returning the module-level dict itself would leave the
    second call with a config missing its formatters.
    """
    import copy

    return copy.deepcopy(_LOG_CONFIG)
