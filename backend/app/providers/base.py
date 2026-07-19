"""Provider adapter contract. One subclass per IdP.

export() returns {resource_type: [raw API objects]} — the full config surface
of the tenant, suitable for encryption, diffing, and (eventually) restore.
"""
from abc import ABC, abstractmethod


class ProviderAdapter(ABC):
    name: str = "base"
    # Config restore ordering: resource types in dependency order (parents first);
    # types not listed restore last. never_restore = types never written back but
    # still VISIBLE in plans when they differ. derived_types = server-generated
    # side-effects of other objects (regenerated automatically on recreate) —
    # excluded from plans entirely; backed up and browsable only.
    restore_order: list[str] = []
    never_restore: set[str] = set()
    derived_types: set[str] = set()
    # Whether export_identities/apply_identities are implemented for this provider.
    supports_identity: bool = False

    def unrestorable_reason(self, resource_type: str, obj: dict) -> str | None:
        """Adapters return a human reason when an object CANNOT be restored via
        the provider API (e.g. a binding whose target no longer exists). The
        planner then excludes it calmly instead of attempting and failing."""
        return None

    def begin_restore(self, snap_export: dict, live_export: dict) -> None:
        """Hook called once before a restore plan is executed — adapters may build
        remap state (e.g. old-id -> live-id by natural key). Default: no-op."""

    def compare_form(self, resource_type: str, obj: dict) -> dict:
        """Canonical form used for snapshot-vs-live comparison. Adapters override to
        remap internal cross-references (old ids -> live ids) so an object whose
        referenced target was recreated compares as identical. Default: unchanged."""
        return obj

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

    def push_object(self, resource_type: str, obj: dict, live: dict | None = None) -> tuple[str, str]:
        raise NotImplementedError(f"{self.name}: restore (apply) not implemented yet")

    def natural_key(self, resource_type: str, obj: dict) -> str:
        """Stable identity to match snapshot objects to live objects on restore.
        Server ids can change on recreate, so providers override with a natural key
        (name / identifier / ...); default falls back to the object's id."""
        for k in ("pk", "id", "client_id", "custom_domain_id", "slug", "brand_uuid"):
            if obj.get(k) is not None:
                return str(obj[k])
        return ""

    def export_identities(self) -> dict[str, list[dict]]:
        """Identity data: users, group memberships, and app assignments with
        provenance (group-inherited vs direct). Buckets a provider doesn't use
        stay empty. NOT part of the config export — separate cadence/storage."""
        raise NotImplementedError(f"{self.name}: identity backup not implemented")

    def apply_identities(self, snap: dict, only_keys=None) -> dict:
        raise NotImplementedError(f"{self.name}: identity restore apply not implemented")
