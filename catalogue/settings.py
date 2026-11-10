"""Settings from environment variables, with an optional local .env file.

A real environment variable beats the .env file. A blank value counts as not set. Error messages
name the variable, never its value (a value can be an email address or a database password).
"""

import os
import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from catalogue import __version__
from catalogue.errors import PipelineError

DEFAULT_WEBSITE = "https://github.com/AbhiDuffy07/voyagehub-catalogue"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")


class SettingsError(PipelineError):
    """A required setting is missing or malformed. The message never contains a value."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    # repr=False keeps these out of logs and tracebacks.
    contact_email: str = Field(repr=False)
    app_website: str = DEFAULT_WEBSITE
    database_url: str | None = Field(default=None, repr=False)


def read_dotenv(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines. Skips blanks and # comments; strips one pair of quotes."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings(
    environ: Mapping[str, str] | None = None, env_file: Path | str | None = ".env"
) -> Settings:
    env = os.environ if environ is None else environ
    file_values: dict[str, str] = {}
    if env_file is not None and Path(env_file).is_file():
        file_values = read_dotenv(Path(env_file))
    merged = {k: v.strip() for k, v in {**file_values, **env}.items() if v.strip()}

    email = merged.get("CONTACT_EMAIL")
    if email is None:
        raise SettingsError("CONTACT_EMAIL is required (the Wikimedia User-Agent needs it)")
    if not _EMAIL.match(email):
        raise SettingsError("CONTACT_EMAIL is not a valid email address")

    return Settings(
        contact_email=email,
        app_website=merged.get("APP_WEBSITE", DEFAULT_WEBSITE),
        database_url=merged.get("CATALOGUE_DATABASE_URL"),
    )


def user_agent(settings: Settings) -> str:
    """Wikimedia's policy: a descriptive client name and version, contact details, and 'bot'."""
    return f"VoyageHub/{__version__} ({settings.app_website}; {settings.contact_email}) bot"
