"""Provider adapter contract. One subclass per IdP.

export() returns {resource_type: [raw API objects]} — the full config surface
of the tenant, suitable for encryption, diffing, and (eventually) restore.
"""
from abc import ABC, abstractmethod


class ProviderAdapter(ABC):
    name: str = "base"
    # Config restore ordering: resource types in dependency order (parents first);
    # types not listed restore last. never_restore = types never written back.
    restore_order: list[str] = []
    never_restore: set[str] = set()

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

    def count_changes_since(self, iso_ts: str) -> int | None:
        """Number of admin/config events in the IdP since iso_ts (UTC).
        None = provider doesn't support it (yet)."""
        return None

    def push_object(self, resource_type: str, obj: dict) -> tuple[str, str]:
        raise NotImplementedError(f"{self.name}: restore (apply) not implemented yet")

    def export_identities(self) -> dict[str, list[dict]]:
        """Identity data: users, group memberships, and app assignments with
        provenance (group-inherited vs direct). Buckets a provider doesn't use
        stay empty. NOT part of the config export — separate cadence/storage."""
        raise NotImplementedError(f"{self.name}: identity backup not implemented")

    def apply_identities(self, snap: dict, only_keys=None) -> dict:
        raise NotImplementedError(f"{self.name}: identity restore apply not implemented")
