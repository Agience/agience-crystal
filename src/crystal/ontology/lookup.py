"""Surface forms -> concepts: the store-side lookup half of matching.

Given a word, which concepts could it name? Given a concept, what is near it? These read the corpus
through the ontology driver and the lattice graph, and they compute nothing that needs the signal.

That asymmetry is the whole reason for the split. mantle is ember's sibling in the DAG and does not
reach up to it, so the signal-touching half stays in ember — the only layer that reaches both
the corpus and the aperture, which `ember/optics.py` holds. The lookup half has no such constraint,
and it is the half the personas actually wanted: `wn_synsets_for` alone accounts for six
`chorus -> ember` import sites, every one of them asking the corpus a question the store can answer
by itself.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

from prism import law as _law

from crystal.ontology import driver as wn

_ASSOC_CACHE: Dict[int, Dict[Tuple[str, str, str], float]] = {}

# ── the ontology's tokeniser: one home, and the alphabet is the lexicon's own ────────────────────
#
# A lookup key has to be written in the alphabet the lexicon is written in, and the lexicon's is
# measured rather than assumed: of 147,306 distinct lemma surfaces, **0 carry a character outside
# ASCII**. So an accented input is not a different word, it is the same word written with marks the
# lexicon does not use, and the way to reach its entry is to remove them.
#
# The word class itself IS derived from those surfaces — 4.77% carry `-` (`mother-in-law`) and 0.88%
# carry `'` (`o'clock`), so both are word characters here, and 92.3% of the hyphenated ones have no
# space-separated twin, which is why this must not split where the FTS index splits. The two arms
# are keyed on different things: the index on terms that BM25 ranks, the lexicon on surfaces that
# name concepts. Forcing `[^\W_]+` on this side would cost 6,485 lemmas.
#
# What is NOT derived from anything is a letter range, and the range this used to carry — `[A-Za-z]`
# — treated every other letter as a separator. Measured over 40 accented words an encyclopedic
# corpus really contains, that shattered 39 and silently ground 12 of them onto a DIFFERENT concept:
#
#     Zürich  -> `rich`  -> rich_people.n.01        a city read as a social class
#     naïve   -> `na`    -> sodium.n.01             an adjective read as an element
#     résumé  -> `sum`   -> kernel.n.03
#     El Niño -> `ni`    -> nickel.n.01
#
# A wrong concept is worse than no concept, and nothing downstream can tell the two apart. So a
# character this class does not know STAYS IN THE TOKEN rather than cutting it: `Bjørn` folds to
# nothing the lexicon holds, so it places nowhere and is reported unplaced. Absence is a measurement
# and a mis-grounding is not — the same rule `word_information` follows for an unmeasurable word.
#
# `[^\W\d_]` is Python's spelling of "any Unicode letter". Folding first makes it ASCII-identical:
# over ASCII input this and `[A-Za-z]` accept exactly the same characters, so no working token moves.

#: Diacritic removal, canonically decomposed — the transformation from how a word is written to how
#: the lexicon spells it. Not a transliteration table: a table is a list somebody maintains, this is
#: Unicode's own decomposition, so it needs no upkeep and covers letters nobody thought to list.
#: A letter with no decomposition (`ø`, `ß`) passes through and its word places nowhere, which is
#: the honest outcome — the lexicon does not hold it under any spelling.
def fold_to_lexicon(text: str) -> str:
    """`text` in the alphabet the lexicon is written in."""
    return "".join(c for c in unicodedata.normalize("NFD", str(text))
                   if not unicodedata.combining(c))


#: A lemma surface: a letter, then letters, apostrophes and hyphens. Applied to folded text.
LEMMA_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['-])*", re.UNICODE)

#: Kept as a name because `ember.ontology.match` re-exports it and callers say `match._WORD`.
_WORD = LEMMA_WORD


def lemma_tokens(text: str, *, minimum: int = 2) -> List[str]:
    """`text` -> lowercase tokens in the lexicon's alphabet. The ONE ontology tokeniser.

    `minimum` is the caller's policy and stays at the call site, because how short a token may be is
    a question about what that caller is doing — a query word, a surface form harvested from a
    gloss — while the alphabet is a fact about the corpus and is not negotiable per caller.
    """
    folded = fold_to_lexicon(text or "").lower()
    return [m.group(0) for m in LEMMA_WORD.finditer(folded) if len(m.group(0)) >= minimum]


def held_senses(word: str, pos: str):
    """Senses of `word` in `pos` whose lemma set actually CONTAINS the word.

    The lemma index is what makes a lookup safe, and this is that check in one place because the
    alphabet has two readers: `wn_synsets_for` fires a query word, `offer_synsets` places a
    document. They disagreed, and the disagreement was live:

        morphy("is", NOUN)  ->  "i"      and "i" is a lemma of iodine.n.01

    `wn_synsets_for` rejected it — `len(m) > 1` refuses a base morphy stripped down to a bare
    letter — so the query side read "is" as `be.v.01` and nothing else. `offer_synsets` called
    `wn.synsets(tok, pos=NOUN)` raw, with no such guard, so it read "is" as IODINE. Measured, that
    put a chemical element into 7.5% of the fired weight of "what is a glacier":

        glacier.n.01      8.2261   61.7%
        oewn-09312237-n   4.1131   30.8%
        iodine.n.01       1.0000    7.5%     <- from the word "is"

    A synset is accepted when the word itself is one of its lemmas, or when morphy's base is and
    that base is more than one character. Morphy is asked per part of speech because the detachment
    rules differ: `running` bases to `run` as a verb and to `running` as a noun.
    """
    from crystal.ontology import driver as wn

    try:
        m = wn.morphy(word, pos)
    except Exception:
        m = None
    try:
        candidates = wn.synsets(word, pos=pos)
    except Exception:
        return []
    out = []
    for x in candidates:
        lemset = {l.name().lower() for l in x.lemmas()}
        if word in lemset or (m and len(m) > 1 and m in lemset):
            out.append(x)
    return out


def hyphen_fallback_parts(token: str) -> List[str]:
    """The parts of a hyphenated token the lexicon does NOT hold whole — or `[]`.

    One rule, in one place, because the alphabet has two readers: `offer_synsets` places a
    document and `wn_synsets_for` fires a query word. If only one of them split, a document
    would be findable by a name its own query text could not express.

    ## Why splitting is needed at all

    `lemma_tokens` keeps hyphens because the lexicon itself does (`state-of-the-art`,
    `self-esteem`, `well-being`), so a coined name arrives as one token that resolves to
    nothing:

        "UNIVERSAL-ECONOMICS — UNIVERSAL-ECONOMICS"  ->  []
        "dns-continuity-spec"                        ->  []
        "AGENT-HOST-DESIGN — 0. The finding ..."     ->  determination, findings

    Zero position for the document whose title IS the query, while the query "universal
    economics" resolves normally. The parts are the only thing about a coined name the
    ontology can measure.

    ## Held whole wins, in ANY part of speech

    A token the lexicon holds is never taken apart. The probe is POS-free deliberately:
    `state-of-the-art` is an adjective and nothing else, so a noun-only probe reported "not
    held" and split a real lemma into `state` + `art`.

    ## The parts are re-tokenised, not cut to a length

    An earlier version admitted a part when `len(part) >= 3`. That number was FITTED: two wiki
    articles produced positions I did not like (`Kim Jong-il` -> `illinois.n.01`, `Al-Khwarizmi`
    -> `aluminum.n.01`, both two-letter fragments hitting abbreviation entries), and three is
    where those two stopped. Nothing derived it, and nothing measured whether those positions
    changed an ANSWER — only that they existed.

    So there is no length here. The parts go back through `lemma_tokens`, which is the ONE
    ontology tokeniser and already states what counts as a token in this alphabet, its own
    `minimum` included. A hyphen becomes a separator and the existing rule decides the rest; if
    that rule should change it changes once, for every reader of the alphabet, rather than here
    for one of them.

    A fragment that resolves to something unrelated is then handled where reach is handled: a
    position that does not reach the fired field adds to `n` without adding energy, and the
    standardisation in `ranking._reach_rank` divides by it. A bad part costs the candidate that
    carries it, instead of being excluded in advance by a number somebody picked.

    This is a fallback, not a normalisation: it cannot change any answer the lexicon gives.
    """
    from crystal.ontology import driver as wn

    tok = (token or "").strip().lower()
    if "-" not in tok:
        return []
    try:
        if wn.synsets(tok):
            return []                                    # held whole — never taken apart
    except Exception:
        return []                                        # unmeasurable: change nothing
    return lemma_tokens(tok.replace("-", " "))


def offer_synsets(text: str) -> List[str]:
    """The synsets an offer names — its position in meaning-space, not a pooled direction.

    Kept as a set of nodes rather than one vector precisely so real geodesic distance stays
    measurable per node. Most-frequent-sense only (`[:1]`): an offer is a description, and taking
    every sense of every noun smears an offer across the ontology, which is what let
    "health status disk memory" sit near "python function".
    "describes markdown documents" contributes `markdown`/`document` and nothing from "describes"."""
    from crystal.ontology import driver as wn
    if not text or not str(text).strip():
        return []
    out, seen = [], set()

    def _emit(senses):
        if not senses:
            return
        name = senses[0].name()
        if name not in seen:
            seen.add(name)
            out.append(name)

    for tok in lemma_tokens(text):
        senses = held_senses(tok, wn.NOUN)
        if not senses:
            # A coined name the lexicon does not hold whole — its parts are the only thing about
            # it the ontology can measure. The rule, and the measurements that set it, live in
            # `hyphen_fallback_parts`, so the query side fires a name the same way this places it.
            for part in hyphen_fallback_parts(tok):
                _emit(held_senses(part, wn.NOUN))
            continue
        _emit(senses)
    return out


def subject_synsets(text: str) -> List[str]:
    """Where an ARTIFACT sits in meaning-space, from the words it is keyed on. Nouns AND verbs.

    ## Why this is not `offer_synsets`

    They take the same input and answer different questions, and the difference is the part of
    speech. `offer_synsets` places an OFFER — a description of what an operator does — and its
    docstring records the property that makes it right for that: *"describes markdown documents
    contributes `markdown`/`document` and nothing from 'describes'."* In a description the verb is
    the frame, not the subject, and firing it smears the offer across the ontology.

    An ARTIFACT is the other case. `cn-frighten` is not a description that happens to contain a
    verb; the verb IS what the artifact is about. Placing it by its nouns alone places it nowhere.

    Measured 2026-08-25 on 71/home, a uniform sample of 2,500 ConceptNet terms drawn by rowid:

        placed by the noun-only arm            49.0%
        placed ONLY once verbs are asked        2.8%   <- ~33,000 artifacts (+/- 0.7pp)
        placed by neither                      48.2%

    The rescued lead sense is a verb in 14 of 17 cases: `frighten.v.01` · `embrittle.v.01` ·
    `evolve.v.01` · `mislead.v.01` · `militarize.v.01` · `contribute.v.03`.

    ## Why a verb is a position and an adjective still is not

    📄 `wn_synsets_for` states it and this only stops discarding it: *"Both parts of speech carry a
    hypernym tree and an information content, so both have a position the coordinate can measure."*
    Adjectives and adverbs remain excluded by both entry points, because they carry an information
    content and no hypernym parent — `jc_tree` has nothing to measure and a synset admitted without
    a position would score a distance that came from nowhere.

    The cross-taxonomy pair is already handled and needs nothing here. 📄 *"A noun and a verb share
    no ancestor, so a pair drawn across the two taxonomies has no subsumer and `jc_tree` returns
    `IC(a) + IC(b)`. The mass gap already refuses that: the sum exceeds the corpus diameter for all
    but the most generic 1.66% of synsets, and the verb taxonomy's own diameter (1.109) sits inside
    the horizon (1.691), so no within-tree verb pair is refused for being a verb."*

    Most-frequent-sense per token, one entry per synset, in token order — the same shape
    `offer_synsets` returns, so a caller swapping between them gets no new shape to handle.
    """
    from crystal.ontology import driver as wn
    if not text or not str(text).strip():
        return []
    out, seen = [], set()

    def _emit(names):
        for name in names[:1]:                 # most-frequent sense, as the offer side does
            if name not in seen:
                seen.add(name)
                out.append(name)

    for tok in lemma_tokens(text):
        names = wn_synsets_for(tok)            # nouns first, then verbs
        if names:
            _emit(names)
            continue
        # A coined name the lexicon does not hold whole — its parts are the only thing about it the
        # ontology can measure. Same rule and same measurements as the offer side.
        for part in hyphen_fallback_parts(tok):
            _emit(wn_synsets_for(part))
    return out


def _wn_prefix(surface: str) -> bool:
    """Does the lexicon hold any entry starting with this underscore surface? (`wn_store`'s keyed
    range probe — the stopping rule for longest-match, in place of a maximum compound length.)"""
    from crystal.ontology import driver as wn
    try:
        return wn.entry_prefix_exists(surface)
    except Exception:
        return False


def wn_synsets_for(word: str) -> List[str]:
    """The synset names a word resolves to: nouns first, then verbs, each most-frequent-sense first.

    The same walk `offer_synsets` uses, exposed so weighting can pair node -> word.

    Keeps only senses whose lemma set literally contains the query word, or its morphed base when
    that base is more than one character, which excludes a synset reached only because morphy
    over-stripped the word down to a bare letter. Morphy is asked per part of speech, since the
    detachment rules differ: `running` bases to `run` as a verb and to `running` as a noun.

    Both parts of speech carry a hypernym tree and an information content, so both have a position
    the coordinate can measure. Nouns come first because theirs is the denser taxonomy — 481,846
    synsets against 91,393, of which 27,030 sit in a tree — and a caller that takes the lead sense
    gets the noun reading, which is the one a bare query word usually means.

    Adjectives and adverbs are not returned. They carry an information content but no hypernym
    parent, so they have no least common subsumer with anything and `jc_tree` has nothing to
    measure; a synset admitted here without a position would score a distance that came from
    nowhere. `match.coordinate_coverage` reports the tokens this leaves unplaced.

    A noun and a verb share no ancestor, so a pair drawn across the two taxonomies has no
    subsumer and `jc_tree` returns `IC(a) + IC(b)`. The mass gap already refuses that: the sum
    exceeds the corpus diameter for all but the most generic 1.66% of synsets, and the verb
    taxonomy's own diameter (1.109) sits inside the horizon (1.691), so no within-tree verb pair
    is refused for being a verb."""
    from crystal.ontology import driver as wn
    # Folded here as well as in `lemma_tokens`, because this is the word -> concept entry point and
    # a caller may reach it with a word it did not tokenise — `sage.content_search` asks it about
    # query words and their morphed bases directly. Folding is the identity on ASCII and idempotent,
    # so a token that arrives already folded is unchanged and no working lookup moves.
    w = fold_to_lexicon(word or "").strip().lower()
    if len(w) <= 1:
        return []                                        # a bare letter is never a query subject
    res: List[str] = []
    for pos in (wn.NOUN, wn.VERB):
        for x in held_senses(w, pos):            # the shared guard — see `held_senses`
            res.append(x.name())
    if not res:
        # The same fallback `offer_synsets` applies when it PLACES a document, applied here where
        # a query word FIRES. Both read one alphabet, so both must take a coined name apart the
        # same way — otherwise a document is findable by a name its own query text cannot express:
        # `recall("AGENT-HOST-DESIGN")` would fire nothing while the document it names sits at
        # `agent` / `host` / `design`. Only when the word resolved to nothing whole, so this can
        # never displace a real answer. See `hyphen_fallback_parts`.
        seen = set()
        for part in hyphen_fallback_parts(w):
            for name in wn_synsets_for(part):            # a part carries no hyphen: no recursion
                if name not in seen:
                    seen.add(name)
                    res.append(name)
    return res


# ── placing an adjective: a projection, never a position ─────────────────────────────────────────
#
# An adjective is not a kind of anything, so it has no place in the is-a tree and never will: 0 of
# 18,156 adjectives and 0 of 3,621 adverbs carry a hypernym parent, so `jc_tree` has nothing to
# measure and a synset admitted without a position would score a distance that came from nowhere.
#
# What an adjective does have is a noun it is ABOUT, and that noun has a coordinate. `beautiful`
# derives from `beauty`; `hot` is a value of `temperature`; `quickly` pertains to `quick`, which
# derives from `quickness`. Reading the adjective AT that noun is a projection — it says where the
# concept lives, not what the adjective is a kind of — and everything here is named so that stays
# legible. `projected` comes back beside the ids for exactly that reason.
#
# The order is by directness, and each step is measured against the source rather than chosen:
#
#     derivation | attribute      75,924 edges   direct, 41.6% of adjectives
#     similar -> then the above   23,188 edges   satellite to its head adjective, +24.7%
#     pertainym -> then both       8,072 edges   an adverb to its adjective, 62.3% of adverbs
#
# Reach: adjectives 66.4%, adverbs 62.3%, combined 65.7% of 21,777. The rest place nowhere and are
# reported unplaced, which is the honest outcome — the source holds no link for them.
#
# The whole reachable set is kept, never one pick, and that is what bounds the antonym hazard.
# `abundant` projects to {abundance, quantity} and `scarce` to {quantity, scarcity}: near each other
# because both are about quantity, and distinct because they differ. Picking one noun apiece would
# collapse them onto a single point. Measured over every antonym pair where both ends project, only
# 118 land on identical sets — 0.98% of placed adjectives — and those are pairs where the source
# itself offers one shared derived noun (`accessible`/`inaccessible` -> `handiness.n.01`).

#: Sense-derived links from a modifier to a noun, most direct first. `attribute` is synset-level and
#: `derivation` sense-level, and both were already the source's own names for these relations —
#: nothing here renames or invents an edge.
# The label names are the corpus's, and they were measured rather than assumed. Counted on the
# live 676,225-synset lattice, by how many edges of each label start at a modifier — which is the
# only count that matters here, because a relation that never leaves an adjective cannot place one:
#
#     similar         23,196 total   21,458 from a modifier    satellite -> head adjective
#     attribute        1,278 total      639 from a modifier    adjective -> the noun it values
#     pertainym            0                                   absent from this corpus entirely
#     derived_from   301,117 total        0 from a modifier    ConceptNet's, between cn-* nodes
#     similar_to      30,158 total        0 from a modifier    ConceptNet's, between cn-* nodes
#
# The last two are the trap: they are large, their names read like WordNet's derivation and
# similar-to, and they relate ConceptNet nodes to each other. Counting totals rather than
# modifier-sourced edges reads them as the biggest placement relations in the store, and walking
# them places nothing.
#
# `derivation` and `pertainym` are the OEWN sense-relations `stage0_sources` now keeps. They are
# also derivable from the installed nltk WordNet against synsets this store already holds, which is
# what `_scratch/backfill_modifier_edges.py` did rather than re-ingesting 676,225 synsets to add
# 105,404 edges — so on this corpus all four are present. `coordinate_coverage` still reports what
# does not place as unplaced rather than guessing a position.
#: `pertainym` reaches an adjective's own noun. WordNet's `pertainym` means "the thing this
#: relational word pertains to", and what that is depends on the source's part of speech: an
#: adverb's pertainym is its adjective (`abaxially -> abaxial`), an adjective's is its noun
#: (`solar -> sun`, `igneous -> hotness`).
#:
#: One label, two roles, and the label is listed here without splitting them. Listed under
#: `_ADVERB_TO_ADJECTIVE` alone, an adjective whose only relation is a pertainym places nowhere.
#: `_hop` filters its targets by part of speech, so an adverb's pertainym target is an adjective
#: and this walk asks for nouns: it cannot be reached here by mistake.
#:
#: Measured on 4,000 of the store's 21,777 modifier synsets: placement 66.4% -> 72.0%. Visible on
#: the benchmark as `what does solar mean` (`[]` -> `sun`), which walks the store edge
#: `wn-solar.a.01 --pertainym--> wn-sun.n.01`.
_TO_NOUN = ("derivation", "attribute", "pertainym")

#: An adverb reaches a noun only through its adjective, so this hop runs before `_TO_NOUN`.
_ADVERB_TO_ADJECTIVE = ("pertainym",)

#: A satellite adjective carries the cluster's relations on its head, so follow it and try again.
_TO_HEAD_ADJECTIVE = ("similar",)


#: Part of speech from a synset name, in EITHER naming this workspace uses. Princeton names a synset
#: `dog.n.01`, so the pos is the middle dotted field; Open English WordNet names it
#: `oewn-05207437-n`, so the pos is the trailing dash field. Reading only the first shape is not a
#: cosmetic bug: it makes every OEWN modifier report pos `""`, which matches no branch, and the whole
#: projection returns `[]` on the corpus that actually runs — measured, 0 of 600.
_POS_CHARS = frozenset("nvasr")


def _pos_of(name: str) -> str:
    """`n` / `v` / `a` / `s` / `r`, or `""` when the name carries no part of speech."""
    text = str(name or "")
    parts = text.split(".")
    if len(parts) > 2 and parts[-2] in _POS_CHARS:
        return parts[-2]
    tail = text.rsplit("-", 1)
    if len(tail) == 2 and tail[1] in _POS_CHARS:
        return tail[1]
    return ""


def _hop(store, name: str, labels: Tuple[str, ...], keep_pos: str) -> List[str]:
    """One labelled hop from `name`, keeping only targets of `keep_pos`. Order preserved, deduped.

    Reads the edge table directly rather than through `related`: `related` costs every hop in nats
    for ranking, and this is a lookup — a projection either exists in the source or it does not, and
    a cost would be a number nothing here consumes."""
    ids = list(wn.concept_ids(name, store)) or [name]
    try:
        conn = store.artifacts.db.read()
        rows = conn.execute(
            "SELECT dst, label FROM edge WHERE src IN (%s)" % ",".join("?" * len(ids)), ids
        ).fetchall()
    except Exception:
        return []
    out, seen = [], set()
    for dst, label in rows:
        if label not in labels:
            continue
        target = wn._n(str(dst), store)
        if target in seen:
            continue
        if _pos_of(target) == keep_pos:
            seen.add(target)
            out.append(target)
    return out


def projected_nouns_for(store, name: str) -> List[str]:
    """The nouns a modifier is about — its projection, or ``[]`` if the source links it to none.

    ``[]`` is a measurement: it means this corpus holds no link from this modifier to any noun, and
    the caller reports the token unplaced rather than substituting a position. See
    `match.coordinate_coverage`, which counts a projected token separately from a positioned one.
    """
    pos = _pos_of(name)
    if pos == "r":                                       # adverb: reach its adjective first
        out, seen = [], set()
        for adjective in _hop(store, name, _ADVERB_TO_ADJECTIVE, "a") +                 _hop(store, name, _ADVERB_TO_ADJECTIVE, "s"):
            for n in projected_nouns_for(store, adjective):
                if n not in seen:
                    seen.add(n)
                    out.append(n)
        return out
    if pos not in ("a", "s"):
        return []                                        # a noun or verb has its own position
    direct = _hop(store, name, _TO_NOUN, "n")
    if direct:
        return direct
    out, seen = [], set()
    for head in _hop(store, name, _TO_HEAD_ADJECTIVE, "a") +             _hop(store, name, _TO_HEAD_ADJECTIVE, "s"):
        for n in _hop(store, head, _TO_NOUN, "n"):
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


def projected_synsets_for(word: str, store=None) -> List[str]:
    """The nouns an adjective or adverb is about — the projection for one WORD, `[]` if there is none.

    Separate from `wn_synsets_for` on purpose, and the separation is the honest part.
    `wn_synsets_for` answers "which concepts could this word name", and for an adjective the answer
    is still none that the coordinate can measure — that has not changed and callers that depend on
    it are untouched. This answers a different question: "if this modifier cannot be placed, what is
    it about". A caller that wants the second must ask for it, so a projected id can never arrive
    somewhere expecting a positioned one.

    Returns nouns, so `jc_tree` needs no special case and nothing downstream learns a new shape.
    """
    from crystal.ontology import driver as wn
    w = fold_to_lexicon(word or "").strip().lower()
    if len(w) <= 1:
        return []
    out, seen = [], set()
    for pos in (wn.ADJ, wn.ADJ_SAT, wn.ADV):
        try:
            m = wn.morphy(w, pos)
        except Exception:
            m = None
        try:
            candidates = wn.synsets(w, pos=pos)
        except Exception:
            candidates = []
        for x in candidates:
            lemset = {l.name().lower() for l in x.lemmas()}
            if not (w in lemset or (m and len(m) > 1 and m in lemset)):
                continue
            for n in projected_nouns_for(store if store is not None else wn._SOURCE, x.name()):
                if n not in seen:
                    seen.add(n)
                    out.append(n)
    return out


def hop_cost(store, src: str, dst: str, label: str) -> Optional[float]:
    """The ambiguity, in nats, of following `label` from `src` to `dst` — measured both ways.

        d = log(out-degree(src, label)) + log(in-degree(dst, label))

    The missing half is the target's side. `astronomy` is the domain topic of hundreds of terms, so
    arriving there tells you very little — many things point at it. An edge is informative only when
    it is rare in both directions: few ways out of the source and few ways in to the target. Both
    are counted from the graph, in nats, and nothing is chosen.

    **A one-to-one edge costs exactly 0.0**, and that is not a rounding: `log(1) + log(1)`. It is
    the honest reading of what this measures — an edge that is the only way out and the only way in
    introduces no ambiguity — and it carries two consequences a caller must not walk into:

    **Accumulated cost is not a bound on distance travelled.** A chain of 1-1 edges is free, so a
    budget in nats permits unboundedly many hops along one. Any traversal needs a second, separate
    ceiling on what it may spend.

    **A cheap edge is a SPECIFIC edge, not a RELEVANT one, and the two come apart at the tail.**
    A hapax translation — one edge out, one edge in — is the cheapest link in the graph. Measured
    2026-08-25, a best-first traversal minimising this cost placed `cn-singlish` on a Galician synset
    and `cn-nosode` on a Catalan one, because obscurity scores as certainty. The measure is sound;
    reading it as proximity in MEANING is not. 📄 `agience-pharos/status/REACH-THE-WALK-2026-08-25.md`
    carries that measurement and why the traversal it belonged to was removed rather than tuned.
"""
    conn = store.artifacts.db.read()
    cache = _ASSOC_CACHE.setdefault(id(store), {})
    ck = (src, dst, label)
    if ck not in cache:
        try:
            out = conn.execute("SELECT count(dst) FROM edge WHERE src = ? AND label = ?",
                               (src, label)).fetchone()[0]
            inn = conn.execute("SELECT count(src) FROM edge WHERE dst = ? AND label = ?",
                               (dst, label)).fetchone()[0]
        except Exception:
            return None
        if not out or not inn:
            return None          # an edge the graph does not attest in both directions
        cache[ck] = math.log(max(1.0, float(out))) + math.log(max(1.0, float(inn)))
    return cache[ck]


def related(store, name: str, *, exclude: Tuple[str, ...] = ("hypernym", "instance_of",
                                                             "instance_hypernym")) -> List[Tuple[str, float]]:
    """One associative hop from `name`: `[(target, distance_in_nats)]`.

    IS-A is excluded because `jc_tree` already travels it — including it here would count the tree
    twice. Everything else the source named is available."""
    ids = list(wn.concept_ids(name, store)) or [name]
    try:
        conn = store.artifacts.db.read()
        rows = conn.execute("SELECT src, dst, label FROM edge WHERE src IN (%s)"
                            % ",".join("?" * len(ids)), ids).fetchall()
    except Exception:
        return []
    out: List[Tuple[str, float]] = []
    for vid, dst, label in rows:
        if label in exclude:
            continue
        d = hop_cost(store, str(vid), str(dst), label)
        if d is None:
            continue        # cost unmeasurable -> the edge is not traversed, never traversed free
        out.append((wn._n(str(dst), store), d))
    return out
