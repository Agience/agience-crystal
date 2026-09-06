"""crystal's log config, copied out of origin, still produces what origin produced.

`crystal.host` does not import `origin.logging_utils.build_log_config` (see
`test_crystal_does_not_import_origin.py`); it carries its own copy. That is only safe if the process
still logs the same way, so this file proves it three ways rather than asserting it once:

  1. **The dict is origin's dict.** Compared against origin's JSON, pinned below as bytes, with the
     dotted prefix rewritten to name `crystal.logging_config` instead. Exact equality, so a drifted format, a lost
     filter or a changed level all fail here with a diff.
  2. **The dict does what it says.** Applied for real in a subprocess, fed the argument tuple uvicorn
     actually passes its access logger, and the emitted line compared to an expected string computed
     by hand. `()`-by-dotted-string is only a claim until something resolves it.
  3. **The call is not dead, so deleting it would be a regression.** The same record under uvicorn's
     own default config keeps the query string, secrets and all — deleting the call in favor of
     uvicorn's default would silently reintroduce that leak. This comparison is the control; without
     it, (1) and (2) would prove the config is faithfully copied while saying nothing about whether
     it is needed.

The oracle is pinned here, not read from `../agience-origin`. Reaching into the sibling checkout
would trade an import edge for a test-time filesystem edge onto an AGPL repo, and would silently skip
or fail the moment crystal is cloned on its own. A recorded vector is the honest form of "what origin
produced".
"""
from __future__ import annotations

import copy
import json
import logging.config
import subprocess
import sys

from crystal.logging_config import build_log_config

# ── the oracle ───────────────────────────────────────────────────────────────────────────────────
# Verbatim `agience-origin/src/origin/uvicorn_log_config.json`, sha256
# 4eb0aa13f3ebd05cdd94ecedcf44ee982a539091f0f337bd591fcae26465a898. `agience-mantle`'s copy is
# byte-identical apart from the same dotted prefix, and `logging_utils.py` is byte-identical in both
# (sha256 5a28e55e…) — the helper is generic, shared as-is by every component that needs it.
ORIGIN_LOG_CONFIG_JSON = """
{
  "version": 1,
  "disable_existing_loggers": false,
  "filters": {
    "redact_access_query": {
      "()": "origin.logging_utils.RedactAccessQueryFilter"
    }
  },
  "formatters": {
    "default": {
      "()": "origin.logging_utils.UTCFormatter",
      "fmt": "%(asctime)s.%(msecs)03dZ %(levelname)s - %(name)s - %(filename)s:%(lineno)d - %(message)s",
      "datefmt": "%Y-%m-%dT%H:%M:%S"
    },
    "access": {
      "()": "origin.logging_utils.UTCFormatter",
      "fmt": "%(asctime)s.%(msecs)03dZ %(levelname)s - %(name)s - %(message)s",
      "datefmt": "%Y-%m-%dT%H:%M:%S"
    }
  },
  "handlers": {
    "default": {
      "class": "logging.StreamHandler",
      "formatter": "default",
      "filters": ["redact_access_query"],
      "stream": "ext://sys.stdout"
    },
    "access": {
      "class": "logging.StreamHandler",
      "formatter": "access",
      "filters": ["redact_access_query"],
      "stream": "ext://sys.stdout"
    }
  },
  "loggers": {
    "uvicorn": {
      "handlers": ["default"],
      "level": "INFO",
      "propagate": false
    },
    "uvicorn.error": {
      "level": "INFO"
    },
    "uvicorn.access": {
      "handlers": ["access"],
      "level": "INFO",
      "propagate": false
    }
  },
  "root": {
    "handlers": ["default"],
    "level": "INFO"
  }
}
"""

#: The argument tuple uvicorn's access logger is called with, against the format
#: `'%s - "%s %s HTTP/%s" %d'`. The path carries an OAuth authorization code — the reason the filter
#: exists at all.
ACCESS_ARGS = ("127.0.0.1:53124", "GET", "/auth/callback?code=SUPERSECRET&state=xyz", "1.1", 200)


def _rewrite(node):
    """origin's dict with the only thing that legitimately differs — where the classes live."""
    if isinstance(node, dict):
        return {k: _rewrite(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite(v) for v in node]
    if isinstance(node, str):
        return node.replace("origin.logging_utils.", "crystal.logging_config.")
    return node


# ── 1. the dict is origin's dict ─────────────────────────────────────────────────────────────────

def test_the_config_equals_origins_config_with_only_the_module_path_rewritten():
    expected = _rewrite(json.loads(ORIGIN_LOG_CONFIG_JSON))
    assert build_log_config() == expected, (
        "crystal's log config drifted from the origin config it replaced. If the change is "
        "deliberate, the oracle above records what origin produced at the cut and should be "
        "retired rather than edited to match.")


def test_the_oracle_is_not_trivially_equal_to_everything():
    """The control for the comparison above: proves the rewrite is doing work and the equality is
    not vacuous. Un-rewritten, origin's dict must not equal crystal's — otherwise the assertion
    would pass with the dotted paths still pointing into the AGPL package."""
    raw = json.loads(ORIGIN_LOG_CONFIG_JSON)
    assert build_log_config() != raw, "the comparison is vacuous — nothing was rewritten"
    assert "origin.logging_utils.UTCFormatter" in json.dumps(raw)
    assert "origin" not in json.dumps(build_log_config()), (
        "crystal's config still names origin — dictConfig would import the AGPL package at runtime, "
        "which is the same edge the AST scan was meant to have removed")


def test_the_config_is_fresh_each_call_because_dictconfig_mutates_it():
    """`dictConfig` pops `()` out of each formatter/filter while constructing it. origin returns
    `json.loads(...)` — a new object every call — so this is free there too. A module-level dict
    handed out directly would work once and come back gutted."""
    first = build_log_config()
    logging.config.dictConfig(first)                       # consumes/mutates `first`
    second = build_log_config()
    assert second == _rewrite(json.loads(ORIGIN_LOG_CONFIG_JSON)), (
        "the second call returned a mutated config — build_log_config is leaking module state")
    assert "()" in second["formatters"]["default"]


# ── 2. the dict does what it says ────────────────────────────────────────────────────────────────

_EMIT = r"""
import logging, logging.config, sys
CONFIG = __CONFIG__
logging.config.dictConfig(CONFIG)
log = logging.getLogger("uvicorn.access")
log.info('%s - "%s %s HTTP/%s" %d', *__ARGS__)
"""


def _emit_under(config: dict) -> str:
    """Apply `config` for real in a clean interpreter and return what lands on stdout.

    A subprocess, because `dictConfig` reconfigures the whole logging tree, including the handlers
    pytest itself installs — doing it in-process would either be undone by capture or wreck the run.
    """
    program = (_EMIT
               .replace("__CONFIG__", repr(config))
               .replace("__ARGS__", repr(ACCESS_ARGS)))
    r = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert r.returncode == 0, f"the config could not be applied:\n{r.stderr[-2000:]}"
    return r.stdout.strip()


def test_the_access_line_is_formatted_and_redacted_for_real():
    line = _emit_under(build_log_config())

    # The dotted `()` strings resolved — if `crystal.logging_config` were not importable under that
    # name, or the classes renamed, the subprocess would have died in dictConfig above.
    assert line.endswith('127.0.0.1:53124 - "GET /auth/callback HTTP/1.1" 200'), line

    # The point of the filter.
    assert "SUPERSECRET" not in line, "the OAuth code reached the log"
    assert "code=" not in line and "state=" not in line, "the query string survived"

    # The UTC formatter's shape: `YYYY-MM-DDTHH:MM:SS.mmmZ INFO - uvicorn.access - …`
    stamp, _, rest = line.partition(" ")
    assert stamp.endswith("Z") and "T" in stamp, f"not the UTC timestamp format: {stamp!r}"
    assert rest.startswith("INFO - uvicorn.access - "), rest


def test_the_timestamp_is_utc_not_local():
    """`UTCFormatter` is one line (`converter = time.gmtime`) and would be easy to drop in a copy.
    On a UTC-offset machine the difference is invisible unless you compare to the clock."""
    import datetime

    line = _emit_under(build_log_config())
    stamp = line.split(" ", 1)[0].rstrip("Z").split(".")[0]
    logged = datetime.datetime.fromisoformat(stamp)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    assert abs((now - logged).total_seconds()) < 120, (
        f"the timestamp {stamp} is not UTC — it is {(now - logged).total_seconds() / 3600:.1f}h off, "
        f"which is a local-time converter")


# ── 3. the control: the call is not dead ─────────────────────────────────────────────────────────

def test_uvicorns_default_config_leaks_the_query_string():
    """The control for the comparison above: feeding the same record to uvicorn's own default
    config puts the OAuth code in the log. That is
    the behaviour `crystal.host.run` would silently acquire if the call were dropped in favor of
    uvicorn's default, and it is why this config is worth the ~40 lines of copied stdlib it costs.
    """
    import uvicorn.config

    default = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    line = _emit_under(default)

    assert "SUPERSECRET" in line, (
        "uvicorn's default no longer leaks the query string — if uvicorn started redacting, "
        "crystal's filter is redundant and this whole module should be deleted, not kept")
    assert "SUPERSECRET" not in _emit_under(build_log_config()), (
        "and crystal's config must still be the thing that stops it")
