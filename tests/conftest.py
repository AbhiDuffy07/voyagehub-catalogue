import pytest

from catalogue.http import PoliteClient
from catalogue.settings import load_settings

EMAIL = "pipeline@example.invalid"


@pytest.fixture
def settings():
    return load_settings(environ={"CONTACT_EMAIL": EMAIL}, env_file=None)


@pytest.fixture
def client(settings):
    """A polite client that never really waits, so tests run fast."""
    polite = PoliteClient(settings, sleep=lambda seconds: None)
    yield polite
    polite.close()
