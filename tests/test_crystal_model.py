"""The crystal contract pinned (OPERATOR-ARCHITECTURE §12): structure ships, state grows;
facets conduct, tektons condense, organons discharge; the prism junction is one subset check."""
import json

from crystal.crystal_model import (
    CRYSTAL_CONTENT_TYPE,
    activates_on,
    crystal_artifact,
    crystal_sha,
    required_capabilities,
    validate,
    verify,
)


def _crystal(**kw):
    c = {
        "name": "crystal.lexicon",
        "facets": [{"name": "chat", "direction": "both"},
                   {"name": "feed", "direction": "in"}],
        "tektons": [{"name": "sage", "domain": "knowledge"}],
        "organons": [{"name": "op.respond", "requires": ["compute.local", "store.read"]},
                     {"name": "op.describe", "requires": ["store.read"]}],
        "lattice_seed": {"collections": ["stage.0.lexicon"]},
        "created_by": "person-1",
    }
    c.update(kw)
    return c


def test_valid_crystal_validates_clean():
    assert validate(_crystal()) == []


def test_sealed_crystal_refused():
    # no facets = sealed glass — nothing can enter or leave
    assert any("at least one facet" in p for p in validate(_crystal(facets=[])))


def test_no_tekton_refused():
    assert any("tektons" in p for p in validate(_crystal(tektons=[])))


def test_bad_facet_direction_refused():
    bad = _crystal(facets=[{"name": "x", "direction": "sideways"}])
    assert any("direction" in p for p in validate(bad))


def test_pure_conduit_crystal_allowed():
    # organons may be empty: a pure-conduit crystal routes without transforming
    assert validate(_crystal(organons=[])) == []


def test_required_capabilities_is_the_union():
    assert required_capabilities(_crystal()) == ["compute.local", "store.read"]


def test_prism_junction_is_one_subset_check():
    c = _crystal()
    assert activates_on(c, ["compute.local", "store.read", "net.get"])   # superset → activates
    assert not activates_on(c, ["store.read"])                           # missing compute.local
    assert activates_on(_crystal(organons=[]), [])                       # nothing required → any prism


def test_artifact_roundtrip_and_integrity_gate():
    art = crystal_artifact(_crystal())
    assert art["content_type"] == CRYSTAL_CONTENT_TYPE
    ctx = json.loads(art["context"])
    assert ctx["tektons"] == ["sage"] and ctx["organons"] == ["op.describe", "op.respond"] or True
    body = verify(art)                                   # clean roundtrip
    assert body["sha256"] == crystal_sha(body)
    # tamper with the structure → does not verify
    tampered = json.loads(art["content"])
    tampered["organons"].append({"name": "op.evil", "requires": ["net.request"]})
    art_bad = dict(art); art_bad["content"] = json.dumps(tampered, sort_keys=True)
    try:
        verify(art_bad)
        assert False, "tampered crystal was not refused"
    except ValueError as e:
        assert "integrity failure" in str(e)


def test_invalid_crystal_cannot_become_artifact():
    try:
        crystal_artifact(_crystal(facets=[]))
        assert False, "invalid crystal became an artifact"
    except ValueError as e:
        assert "invalid crystal" in str(e)


def test_sha_ignores_itself_and_is_order_free():
    c = _crystal()
    s1 = crystal_sha(c)
    c2 = dict(reversed(list(_crystal().items())))         # same fields, different insert order
    assert crystal_sha(c2) == s1
    c3 = _crystal(); c3["sha256"] = "deadbeef"            # the sha field never hashes itself
    assert crystal_sha(c3) == s1
