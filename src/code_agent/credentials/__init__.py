"""OS-backed credentials for Agent47 providers and OAuth tokens."""

from .keyring import CredentialStore, KeyringUnavailableError, keyring_setup_hint
from .providers import ProviderMetadata, provider_spec, provider_specs, validate_provider_key

__all__ = [
    "CredentialStore",
    "KeyringUnavailableError",
    "ProviderMetadata",
    "keyring_setup_hint",
    "provider_spec",
    "provider_specs",
    "validate_provider_key",
]
