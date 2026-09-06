"""The catalog push reads EVERY page, because both of its scans are find-then-create.

`_list` used to read one page and its own docstring flagged the risk without closing it. A
truncated read did not degrade gracefully here — it created a duplicate:

* `_ensure_catalog` scans for the catalog collection and CREATES ONE when the scan comes back
  without it, so a catalog past the first page produced a second catalog;
* `_index` builds the operator-id → existing-artifact map the upsert depends on, so every operator
  beyond the page was invisible and got RE-CREATED on every push — duplicates growing one set per
  boot.

Neither caller passed a `limit`, so both took mantle's default of 100. The failure is silent on
both sides: the server answers correctly and the client stops reading.
"""
from __future__ import annotations

from crystal import push as push_mod


class _FakeGet:
    """Serves `{items, total, has_more}` in pages, and records what it was asked for."""

    def __init__(self, rows, page):
        self.rows, self.page, self.calls = rows, page, []

    def __call__(self, path, params=None):
        params = params or {}
        self.calls.append(dict(params))
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", self.page))
        window = self.rows[offset:offset + limit]
        return {"items": window, "total": None,
                "has_more": offset + len(window) < len(self.rows)}


def _pusher(get):
    p = push_mod._MantleArtifactClient.__new__(push_mod._MantleArtifactClient)
    p._get = get
    return p


def test_every_page_is_read():
    rows = [{"id": "a%d" % i} for i in range(450)]
    get = _FakeGet(rows, push_mod._PAGE)
    out = _pusher(get)._list("/artifacts/visible")
    assert [r["id"] for r in out] == [r["id"] for r in rows], (
        "read %d of %d rows — the scan stopped early" % (len(out), len(rows)))
    assert len(get.calls) > 1, "one request for 450 rows means pagination was not followed"


def test_the_row_past_the_first_page_is_found():
    """The defect stated as the thing it caused: the catalog sitting at row 300 was invisible, and
    invisible meant a second catalog got created."""
    rows = [{"id": "filler%d" % i, "name": "x"} for i in range(300)]
    rows.append({"id": "the-catalog", "name": push_mod.CATALOG_COLLECTION_NAME})
    out = _pusher(_FakeGet(rows, push_mod._PAGE))._list("/artifacts/visible")
    assert any(r.get("name") == push_mod.CATALOG_COLLECTION_NAME for r in out)


def test_a_bare_list_from_an_older_node_still_works():
    """The compatibility path the original helper had, kept."""
    out = _pusher(lambda path, params=None: [{"id": "a"}, {"id": "b"}])._list("/x")
    assert [r["id"] for r in out] == ["a", "b"]


def test_has_more_with_an_empty_page_terminates():
    """A server that cannot make progress must end the scan, not hang the boot."""
    def stuck(path, params=None):
        return {"items": [], "total": None, "has_more": True}

    assert _pusher(stuck)._list("/x") == []


def test_the_filter_is_preserved_across_pages():
    """`_index` scans with `content_type`; dropping it on page two would pull in every artifact."""
    rows = [{"id": "a%d" % i} for i in range(300)]
    get = _FakeGet(rows, push_mod._PAGE)
    _pusher(get)._list("/artifacts/visible", {"content_type": "application/x-op"})
    assert len(get.calls) > 1
    assert all(c.get("content_type") == "application/x-op" for c in get.calls), get.calls


def test_a_catalog_past_the_first_page_is_not_duplicated():
    """THE CONSEQUENCE, not the mechanism. The pagination tests above prove `_list` reads every
    page; this proves the thing that made it matter — `_ensure_catalog` CREATES when its scan comes
    back empty, so a truncated scan did not read less, it wrote more."""
    rows = [{"id": "filler%d" % i, "name": "x"} for i in range(300)]
    rows.append({"id": "the-catalog", "name": push_mod.CATALOG_COLLECTION_NAME})

    posted = []
    p = _pusher(_FakeGet(rows, push_mod._PAGE))
    p._catalog_id = None
    p._post = lambda path, body: posted.append((path, body)) or {"id": "a-second-catalog"}

    found = p._ensure_catalog()
    assert found == "the-catalog", (
        "the catalog on page two was not found, so a second one was created: %r" % posted)
    assert posted == [], "nothing should have been created: %r" % posted
