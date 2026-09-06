"""Agience Crystal — condensation, routing: signal to content type. Apache-2.0.

The dispatcher/gateway that condenses incoming signal into typed content and
routes it to the chorus persona services.
"""
__version__ = "0.1.0"

# The live crystal object: structure over the prism contract, flow over an injected embodiment
# (`prism.embodiment`). Its module is stdlib-only at import time and this package imports no
# instrument at all — no aperture, no numpy — so `import crystal` stays light for
# bare-host validation and crystal installs anywhere prism does.
from crystal.crystal import Crystal  # noqa: E402

__all__ = ["Crystal"]
