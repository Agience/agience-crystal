"""The coupling a semantic relation carries — the sign the field reads and the names the readout
matches.

This is a stable grounding surface, read directly by both ember (`ember.ontology.activation`) and
chorus's lumen persona (`lumen.conversation`) rather than through a genesis internal; the op table
does not define it.

`sign`: +1 attraction (broader/narrower/same pulls the concept onto the screen), −1 detraction (the
opposite lands at negative sign). `names`: how a need's operator names the relation.

The two fields carry different verdicts:

  • `sign` is not recoverable from endpoint geometry. Measured over real antonym edges, antonym
    endpoints are positively aligned, never anti-aligned — `love.n.01`↔`hate.n.01` cos=+0.78
    (jc 0.27), `good`↔`evil` +0.56, `increase`↔`decrease` +0.72 — inside the hypernym cosine range
    (mean +0.84). The prototypical antonyms are adjectives (hot/cold, big/small, strong/weak),
    which carry no JC coordinate at all (adjectives are not in the IS-A taxonomy). So
    `Screen.couple` over endpoint coordinates would read antonyms as attracting. `sign` is a
    valence/opposition property orthogonal to taxonomic position — the declared semantics of the
    relation type (antonym = opposite), not an imposed arbitrary constant — so it stays as data on
    the relation type; deriving it would need a valence transducer, not this coordinate.

  • `names` is derivable from the lexicon: "opposite"/"contrary"/"reverse" are synonyms of the
    relation-label word, reachable via the keyed `lex:en` lookup ([[self-contained-wordnet]]).

[[never-impose-knowledge-derive-it]] [[gauge-is-an-artifact-coupling-is-measured]]
[[one-resolution-not-thresholds]] [[no-arbitrary-caps]]
"""
from __future__ import annotations

from typing import Any, Dict

SEED_ETYPE_COUPLING: Dict[str, Dict[str, Any]] = {
    "hypernym":          {"sign": 1.0, "names": ["kind", "type", "sort"]},
    "instance_hypernym": {"sign": 1.0, "names": []},
    "instance_of":       {"sign": 1.0, "names": []},
    "hyponym":           {"sign": 1.0, "names": []},
    "synonym":           {"sign": 1.0, "names": ["same", "synonym", "like"]},
    "antonym":           {"sign": -1.0, "names": ["opposite", "antonym", "contrary", "reverse"]},
}
