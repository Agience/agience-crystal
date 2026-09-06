"""Tests for self-registration type collection (`crystal.type_registration`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `_shared` modules + `platform` importable (mirrors test_gateway_middleware).
_CHORUS_DIR = Path(__file__).resolve().parents[1]

import crystal.type_registration as tr


def test_content_type_from_rel_maps_folders():
    assert tr._content_type_from_rel(("application", "vnd.agience.chat+json")) == \
        "application/vnd.agience.chat+json"
    assert tr._content_type_from_rel(("text", "_wildcard")) == "text/*"
    assert tr._content_type_from_rel(("application",)) is None
    assert tr._content_type_from_rel(("a", "b", "c")) is None


def test_collect_server_types_walks_ui_tree(tmp_path):
    srv = tmp_path / "server.py"
    srv.write_text("# fake server module", encoding="utf-8")

    demo = tmp_path / "ui" / "application" / "vnd.agience.demo+json"
    demo.mkdir(parents=True)
    (demo / "type.json").write_text(
        json.dumps({"content_type": "application/vnd.agience.demo+json", "ui": {"label": "Demo"}}),
        encoding="utf-8",
    )

    # A wildcard overlay WITHOUT a declared content_type — derived from the folder.
    wild = tmp_path / "ui" / "text" / "_wildcard"
    wild.mkdir(parents=True)
    (wild / "type.json").write_text(json.dumps({"ui": {"label": "Text"}}), encoding="utf-8")

    types = tr.collect_server_types(str(srv))

    assert set(types) == {"application/vnd.agience.demo+json", "text/*"}
    assert types["application/vnd.agience.demo+json"]["ui"]["label"] == "Demo"
    # content_type back-filled from the folder for the override-only overlay.
    assert types["text/*"]["content_type"] == "text/*"


def test_collect_server_types_empty_when_no_ui(tmp_path):
    srv = tmp_path / "server.py"
    srv.write_text("# fake", encoding="utf-8")
    assert tr.collect_server_types(str(srv)) == {}
