import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

YES = "107505882767731489358349912513945399560393482969656700824895970500493757150417"
NO = "7305630249804085635496399869905769372294302716159034447326228509068694952392"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def raw(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def fixture():
    return load
