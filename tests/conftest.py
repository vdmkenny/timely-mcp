import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as timely  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_globals():
    timely._backfill_plan_cache.clear()
    timely._csrf_token_cache.clear()
    yield
    timely._backfill_plan_cache.clear()
    timely._csrf_token_cache.clear()
