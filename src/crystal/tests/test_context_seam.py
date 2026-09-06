"""The context seam adds domain knowledge and never restates what the store already observed.

Two failures are guarded, and neither is a crash:

* **Enrichment that re-derives a db-level facet.** Then two answers exist for one question and
  nothing says which is authoritative. `mint_context` observed placement and addressing; this side
  must not have an opinion about them.
* **Enrichment that cannot be told from its own absence.** `None` (never asked) and `[]` (asked,
  found nothing) are different facts, and a store with no ontology bound is a supported deployment
  rather than a broken one.
"""
from __future__ import annotations

import pytest

from crystal import context as ctxseam


def test_no_offer_text_yields_nothing_rather_than_an_empty_shell():
    """`None` keeps the mint visibly thin; `{}` would read as 'asked and found nothing'."""
    assert ctxseam.enrich({}) is None


def test_the_offer_is_read_from_the_callers_own_assertion():
    base = {"caller": {"title": "the lattice", "tags": ["memory"]}}
    assert "lattice" in ctxseam._offer_text(base)
    assert "memory" in ctxseam._offer_text(base)


def test_observed_facets_are_never_read_as_offer_text():
    """`placement`/`addressing` are the store's observations. This side has no opinion on them."""
    base = {"placement": {"collection_id": "c1"},
            "addressing": {"content_type": "text/markdown", "sha256": "abc"},
            "minted": {"at": "now"}}
    assert ctxseam._offer_text(base) == ""


def test_an_unreachable_ontology_gives_None_not_an_empty_anchor_list(monkeypatch):
    """A deployment with no ontology must not look like content that names no concept."""
    monkeypatch.setattr(ctxseam, "_anchors", lambda text: None)
    got = ctxseam.enrich({"caller": {"title": "the lattice"}})
    assert got is None or "anchors" not in got


def test_an_ontology_that_names_nothing_records_the_empty_answer(monkeypatch):
    monkeypatch.setattr(ctxseam, "_anchors", lambda text: [])
    got = ctxseam.enrich({"caller": {"title": "zzzz"}})
    assert got is not None and got["anchors"] == []


def test_anchors_record_where_they_came_from(monkeypatch):
    monkeypatch.setattr(ctxseam, "_anchors", lambda text: ["lattice.n.01"])
    got = ctxseam.enrich({"caller": {"title": "the lattice"}})
    assert got["anchors_from"] == "offer"


def test_the_body_is_not_anchored_unless_the_caller_asks(monkeypatch):
    """Measured: anchoring an offer costs ~1% of a write, a body ~50%. A default nobody chose
    would put the second number on every writer."""
    calls = []

    def fake(text):
        calls.append(text)
        return ["x.n.01"]

    monkeypatch.setattr(ctxseam, "_anchors", fake)
    ctxseam.enrich({"caller": {"title": "t"}}, content="a long body " * 200,
                   content_type="text/plain")
    assert len(calls) == 1, "the body was anchored without being asked for"


def test_asking_for_the_body_merges_without_duplicating(monkeypatch):
    monkeypatch.setattr(ctxseam, "_anchors",
                        lambda text: ["a.n.01", "b.n.01"] if "body" in text else ["a.n.01"])
    got = ctxseam.enrich({"caller": {"title": "t", ctxseam.ANCHOR_BODY_KEY: True}},
                         content="body text", content_type="text/plain")
    assert got["anchors"] == ["a.n.01", "b.n.01"]
    assert got["anchors_from"] == "offer+body"


def test_structure_is_gated_on_the_declared_type_not_sniffed():
    """Guessing a format from bytes is a judgement, and a wrong guess writes a false record."""
    md = "# Title\n\nbody\n\n## Section\n"
    assert ctxseam._structure(md, "text/plain") is None
    got = ctxseam._structure(md, "text/markdown")
    assert got["heading"] == "Title" and got["heading_depth"] == 1 and got["headings"] == 2


def test_markdown_with_no_headings_reports_no_structure():
    assert ctxseam._structure("just prose, no headings", "text/markdown") is None


def test_enrich_never_raises_on_a_hostile_base():
    """`mint_context` drops anything that raises, but relying on that would make this side sloppy."""
    for bad in ({"caller": "not-a-mapping"}, {"caller": None}, {"caller": {"tags": None}}):
        ctxseam.enrich(bad)


def test_the_seam_states_no_opinion_about_placement_or_addressing(monkeypatch):
    monkeypatch.setattr(ctxseam, "_anchors", lambda text: ["a.n.01"])
    got = ctxseam.enrich({"caller": {"title": "t"}}, content_type="text/markdown",
                         content="# H\n")
    for forbidden in ("placement", "addressing", "minted", "screen", "collection_id", "sha256"):
        assert forbidden not in got, f"the seam re-stated a db-level facet: {forbidden}"
