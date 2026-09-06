"""Who authors a row when the caller names nobody.

`crystal.ontology.seed_lattice` writes IC measurements and relation vertices onto a store, and each
entry point takes an `author=`. That default used to be a maintainer's own address written into the
source — wrong twice over in a published package: it puts a personal address in every install, and
it attributes every row anyone seeds to a person who had nothing to do with it. Provenance naming
the wrong author is worse than provenance admitting it does not know.

So the default is read from the environment, and the fallback is deliberately *unresolvable*.
`AGIENCE_AUTHOR` names the principal that authors seeded rows. Absent it, rows are authored by
`crystal-local`, which resolves to no principal — so an unattributed seed is visible to a store's
integrity checks rather than passing as somebody real. A plausible-looking default would pass those
checks while recording a fiction.

Read once, at import: these are default parameter values, which Python evaluates when the function
is defined. A caller that needs a different author passes one.
"""

from __future__ import annotations

import os

#: The unresolvable fallback. Named rather than inlined so a store's integrity checks and this
#: module cannot disagree about which string means "nobody said".
UNATTRIBUTED = "crystal-local"

DEFAULT_AUTHOR = os.environ.get("AGIENCE_AUTHOR") or UNATTRIBUTED


def default_author() -> str:
    """`DEFAULT_AUTHOR`, re-read from the environment.

    For callers that set `AGIENCE_AUTHOR` after import — a test, or a process that resolves its
    principal during boot. The module constant is what the `author=` defaults bind to, and it is
    fixed at import; this is the live answer.
    """
    return os.environ.get("AGIENCE_AUTHOR") or UNATTRIBUTED
