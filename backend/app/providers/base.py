"""Provider adapter contract. One subclass per IdP.

export() returns {resource_type: [raw API objects]} — the full config surface
of the tenant, suitable for encryption, diffing, and (eventually) restore.
"""
from abc import ABC, abstractmethod


class ProviderAdapter(ABC):
    name: str = "base"

    def __init__(self, base_url: str, credentials: str):
        self.base_url = base_url.rstrip("/")
        self.credentials = credentials

    @abstractmethod
    def validate_credentials(self) -> bool:
        """Cheap authenticated call to confirm the token works."""

    @abstractmethod
    def export(self) -> dict[str, list[dict]]:
        """Pull every configuration object, grouped by resource type."""

    def restore_object(self, resource_type: str, obj: dict, dry_run: bool = True) -> dict:
        raise NotImplementedError(f"{self.name}: restore not implemented yet")
