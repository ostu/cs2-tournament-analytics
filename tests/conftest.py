import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINE_DIR = ROOT_DIR / "pipeline"

for p in (str(ROOT_DIR), str(PIPELINE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def root_dir():
    return ROOT_DIR
