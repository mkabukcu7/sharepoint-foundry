"""Shared pytest configuration.

Living at the repository root, this file makes the ``backend`` package
importable regardless of the directory pytest is invoked from, and pins the
controlled taxonomy to a synthetic fixture.

The real taxonomy lives in ``taxonomy/``, which is deliberately untracked
because it is customer material. Tests that relied on it ambiently failed on a
fresh clone, so they now use committed synthetic terms instead and behave the
same on every machine.
"""

import os
from pathlib import Path

os.environ["FOUNDRY_TAXONOMY_PATH"] = str(
    Path(__file__).parent / "tests" / "fixtures" / "test-taxonomy.json"
)
