"""The ontology tokenises in the lexicon's own alphabet, from one definition.

## The question this answers

Why is `café` not the same concept as `cafe`?

It is. They are one entry — the lexicon holds `cafe` and holds nothing else, because of 147,306
distinct lemma surfaces, zero carry a character outside ASCII. An accented input is the same word
written with marks the lexicon does not use, so reaching its entry means removing them.

## What a hand-picked letter range did instead

`[A-Za-z]` treats every other letter as a separator, so an accented word is not folded — it is cut
into the fragments between its accents. Over 40 accented words an encyclopedic corpus really
contains, that shattered 39, and 12 of the fragments were themselves real words:

    Zürich  -> `rich`  -> rich_people.n.01     a city read as a social class
    naïve   -> `na`    -> sodium.n.01          an adjective read as an element
    résumé  -> `sum`   -> kernel.n.03
    El Niño -> `ni`    -> nickel.n.01
    façade  -> `fa`    -> fa.n.01

Nothing downstream can tell a mis-grounding from a grounding: both are a synset with a distance. So
the rule below is that an unknown character does not cut the word. `Bjørn` folds to nothing the
lexicon holds and places nowhere, which is reported as unplaced — absence is a measurement.

## Why this alphabet is not the lexical arm's

The FTS index splits `mother-in-law` into three terms, and it should: BM25 ranks terms. The lexicon
is keyed on surfaces that name concepts, and 92.3% of its 7,028 hyphenated surfaces have no
space-separated twin. Forcing `[^\\W_]+` here would cost 6,485 lemmas. Two questions, two keys — not
two implementations of one question.
"""
from __future__ import annotations

import pytest

from crystal.ontology.lookup import LEMMA_WORD, fold_to_lexicon, lemma_tokens

#: Accented words with an ASCII entry in the lexicon, and the fragment the old range produced.
_FOLDS = [
    ("café", "cafe", "caf"),
    ("résumé", "resume", "sum"),
    ("Zürich", "zurich", "rich"),
    ("naïve", "naive", "na"),
    ("façade", "facade", "fa"),
    ("Ångström", "angstrom", "ngstr"),
    ("jalapeño", "jalapeno", "jalape"),
    ("doppelgänger", "doppelganger", "doppelg"),
]

#: Inputs whose tokenisation must not move. The hyphen and apostrophe rows are the ones a switch to
#: the lexical arm's class would break.
_UNCHANGED = [
    ("mother-in-law", ["mother-in-law"]),
    ("o'clock", ["o'clock"]),
    ("state-of-the-art", ["state-of-the-art"]),
    ("don't", ["don't"]),
    ("x-ray", ["x-ray"]),
    ("baseball bat", ["baseball", "bat"]),
    ("a dog", ["dog"]),                        # a single letter is below the default minimum
    ("snake_case_name", ["snake", "case", "name"]),
    ("3.14 pi", ["pi"]),                       # digits are not letters
    ("glacier", ["glacier"]),
]


@pytest.mark.parametrize("written,spelled,fragment", _FOLDS)
def test_an_accented_word_reaches_its_own_entry(written, spelled, fragment):
    """One token, in the lexicon's spelling — not a fragment, and not several."""
    assert lemma_tokens(written) == [spelled], \
        "%r tokenised to %r rather than [%r]" % (written, lemma_tokens(written), spelled)


@pytest.mark.parametrize("written,spelled,fragment", _FOLDS)
def test_the_fragment_the_old_range_produced_is_gone(written, spelled, fragment):
    """The negative control, stated per case rather than in prose.

    Each `fragment` is what `[A-Za-z][A-Za-z'-]+` returned for this input. Most are real words, and
    a real word is exactly what makes the failure silent — it grounds, and the grounding is wrong.
    """
    assert fragment not in lemma_tokens(written), \
        "%r still produces the fragment %r" % (written, fragment)


@pytest.mark.parametrize("text,expected", _UNCHANGED)
def test_ascii_tokenisation_does_not_move(text, expected):
    """Folding is the identity on ASCII and the letter class accepts the same characters there, so
    ASCII tokenisation is untouched. That bounds what folding can affect to input that would
    otherwise produce fragments."""
    assert lemma_tokens(text) == expected


def test_an_unfoldable_letter_does_not_cut_the_word():
    """`ø` and `ß` have no canonical decomposition. Keeping them in the token means the word places
    nowhere; cutting on them means it places somewhere wrong. `Bjørn` cut on `ø` gives `bj` and
    `rn`, and `rn` is a lemma — radon."""
    assert lemma_tokens("Bjørn") == ["bjørn"]
    assert lemma_tokens("Straße") == ["straße"]


def test_folding_is_unicodes_own_decomposition_not_a_table():
    """A transliteration table is a list somebody maintains and forgets. This covers letters nobody
    listed, which is why the check below uses marks that appear in no fixture above."""
    assert fold_to_lexicon("Ǎǔ Ḥḍ Ṣṭ") == "Au Hd St"
    assert fold_to_lexicon("plain ascii") == "plain ascii"


def test_the_class_is_defined_once_and_not_retyped():
    """Four modules held their own copy of `[A-Za-z][A-Za-z'-]+`, agreeing only because they had
    been typed the same way — and one of them had drifted to `*` where the others had `+`.

    This is a source scan rather than an identity check, because `re.compile` caches on the pattern
    string: two modules that separately compile the same text receive the SAME object, so `is`
    would report them as one definition when they are four.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    offenders = []
    for pkg in ("agience-crystal/src", "agience-ember/src", "agience-chorus/src",
                "agience-mantle/src"):
        base = root / pkg
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            body = path.read_text(encoding="utf-8", errors="replace")
            if "[A-Za-z][A-Za-z'-]" in body:
                offenders.append(str(path.relative_to(root)))
    assert not offenders, (
        "the ontology's letter range is typed again in: %s\n\n"
        "Import `crystal.ontology.lookup.lemma_tokens` instead. A second copy is how the range "
        "came to shatter accented words in three packages at once." % ", ".join(offenders))


def test_the_pattern_itself_admits_letters_beyond_ascii():
    """The property behind every case above, asserted directly on the class so a future edit that
    narrows it back to ASCII fails here with a clear reason rather than as eight parametrised
    surprises."""
    assert LEMMA_WORD.fullmatch("naïve"), "the class rejects a non-ASCII letter"
    assert LEMMA_WORD.fullmatch("mother-in-law"), "the class rejects a hyphenated surface"
    assert LEMMA_WORD.fullmatch("o'clock"), "the class rejects an apostrophe surface"
    assert not LEMMA_WORD.fullmatch("x86"), "the class admitted a digit"
    assert not LEMMA_WORD.fullmatch("snake_case"), "the class admitted an underscore"
