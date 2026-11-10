"""The one base class for errors raised by this pipeline."""


class PipelineError(Exception):
    """An error whose message is safe to store and print: no secrets, no contact address.

    Public-repo Actions logs are public, and the run log is stored in the database, so only
    messages of this kind are ever recorded. Raise it with a message that names things (a host,
    a status, a variable) but never shows a value that could be a password or an address.
    """
