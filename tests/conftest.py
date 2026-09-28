import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.browser import Browser  # noqa: E402
from agent.config import Settings  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def browser(tmp_path):
    b = Browser(Settings(headless=True, profile_dir=tmp_path / "profile"))
    b.start()
    yield b
    b.close()


@pytest.fixture
def open_fixture(browser):
    def _open(name: str):
        page = browser.current()
        page.goto((FIXTURES / name).as_uri())
        return page
    return _open
