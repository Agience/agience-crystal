"""The built wheel contains the `crystal` package and nothing else.

`src/` holds three directories and only one of them is this distribution. `src/tests/` is a single
geometry memo test and `src/types/` is the builtin content-type skeletons — JSON with no `.py`, read
by a test through a repo-relative path and by nothing at runtime. Neither is a package, so
`packages.find` never returns them, and that is exactly why this was invisible: setuptools wrote
both into `top_level.txt` anyway and shipped `src/tests/` in the wheel, so `pip install
agience-crystal` put a top-level `tests` module into site-packages and took that name away from
everything else installed there.

`include = ["crystal*"]` in `pyproject.toml` is the fix. This builds the real wheel and reads what
came out, because the defect lived in the distribution and not in any import: the suite was green,
the package imported, and only the artifact was wrong.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> zipfile.ZipFile:
    """Build a wheel from this checkout and hand back the archive."""
    out = tmp_path_factory.mktemp("wheel")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--wheel", "--outdir", str(out)],
        cwd=REPO, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.skip("`python -m build` unavailable here:\n%s" % (proc.stderr[-2000:],))
    built = sorted(out.glob("*.whl"))
    assert built, "build reported success and produced no wheel"
    return zipfile.ZipFile(built[-1])


def test_the_wheel_has_one_top_level_package(wheel: zipfile.ZipFile) -> None:
    roots = {name.split("/")[0] for name in wheel.namelist()}
    roots = {r for r in roots if not r.endswith(".dist-info")}
    assert roots == {"crystal"}, (
        "the wheel ships %s. Only `crystal` may be installed by this distribution — anything else "
        "here is a directory under `src/` that is not part of the package." % (sorted(roots),)
    )


def test_top_level_txt_names_only_crystal(wheel: zipfile.ZipFile) -> None:
    """`top_level.txt` is a separate claim from what the archive holds, and it was the wrong one.

    It named `tests` and `types` while the archive held only `tests`, so checking the file list
    alone would have missed half of it.
    """
    path = [n for n in wheel.namelist() if n.endswith("dist-info/top_level.txt")]
    assert path, "the wheel declares no top_level.txt"
    declared = wheel.read(path[0]).decode().split()
    assert declared == ["crystal"], (
        "top_level.txt declares %r. A name here is a claim on the environment's import namespace, "
        "whether or not the archive carries files for it." % (declared,)
    )
