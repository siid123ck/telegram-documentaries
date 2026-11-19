"""Typed application errors."""


class ConfigError(Exception):
    """Raised when the application configuration is missing or invalid.

    The message is safe to print to stderr: it names environment variables and
    remediation steps but never includes secret values.
    """
