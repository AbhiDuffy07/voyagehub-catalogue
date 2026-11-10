import pytest

from catalogue import __version__
from catalogue.settings import SettingsError, load_settings, user_agent

GOOD = {"CONTACT_EMAIL": "pipeline@example.invalid"}


def test_missing_contact_email_names_the_variable():
    with pytest.raises(SettingsError) as error:
        load_settings(environ={}, env_file=None)

    assert "CONTACT_EMAIL" in str(error.value)


def test_blank_contact_email_counts_as_missing():
    with pytest.raises(SettingsError):
        load_settings(environ={"CONTACT_EMAIL": "   "}, env_file=None)


def test_invalid_contact_email_is_rejected_without_echoing_it():
    with pytest.raises(SettingsError) as error:
        load_settings(environ={"CONTACT_EMAIL": "not-an-address"}, env_file=None)

    assert "not-an-address" not in str(error.value)


def test_user_agent_has_version_website_contact_and_bot():
    settings = load_settings(
        environ={**GOOD, "APP_WEBSITE": "https://example.invalid/voyagehub"}, env_file=None
    )

    assert user_agent(settings) == (
        f"VoyageHub/{__version__} (https://example.invalid/voyagehub; pipeline@example.invalid) bot"
    )


def test_website_has_a_default_when_not_set():
    settings = load_settings(environ=GOOD, env_file=None)

    assert settings.app_website.startswith("https://github.com/")


def test_repr_never_shows_the_email_or_the_database_url():
    settings = load_settings(
        environ={**GOOD, "CATALOGUE_DATABASE_URL": "postgresql://u:pw@host/db"}, env_file=None
    )

    text = repr(settings)

    assert "pipeline@example.invalid" not in text
    assert "pw" not in text


def test_env_file_supplies_values_and_the_real_environment_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\nCONTACT_EMAIL=from-file@example.invalid\nAPP_WEBSITE=https://file.invalid\n",
        encoding="utf-8",
    )

    settings = load_settings(environ={"APP_WEBSITE": "https://env.invalid"}, env_file=env_file)

    assert settings.contact_email == "from-file@example.invalid"
    assert settings.app_website == "https://env.invalid"


def test_a_missing_env_file_is_fine(tmp_path):
    settings = load_settings(environ=GOOD, env_file=tmp_path / "nope.env")

    assert settings.contact_email == "pipeline@example.invalid"
