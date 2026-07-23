"""OS-backed credentials for Agent47 providers and OAuth tokens."""

from .keyring import CredentialStore, KeyringUnavailableError
from .providers import ProviderSpec, provider_spec, provider_specs, validate_provider_key

__all__ = [
    "CredentialStore",
    "KeyringUnavailableError",
    "ProviderSpec",
    "provider_spec",
    "provider_specs",
    "validate_provider_key",
]
