import pytest


@pytest.fixture(autouse=True)
def disable_operator_delivery(monkeypatch):
    """Never use workstation operator credentials during tests."""
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
