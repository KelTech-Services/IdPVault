"""Turn a snapshot diff into Event rows — the per-object change feed."""
import json


def _name(obj: dict) -> str:
    for k in ("label", "name", "displayName", "display_name", "slug", "username", "title"):
        v = obj.get(k)
        if isinstance(v, str) and v:
            return v[:250]
        if isinstance(v, dict):  # okta apps: label at top; policies: name
            continue
    prof = obj.get("profile") or {}
    if isinstance(prof, dict) and prof.get("name"):
        return str(prof["name"])[:250]
    # Bindings have no name of their own — describe them by what they connect
    # (Authentik includes *_obj expansions with the referenced object's name).
    for k in ("policy_obj", "group_obj", "user_obj", "stage_obj", "target_obj"):
        v = obj.get(k)
        if isinstance(v, dict):
            n = v.get("name") or v.get("username")
            if n:
                return f"binding: {n}"[:250]
    return ""


def _id(obj: dict) -> str:
    for k in ("pk", "id", "client_id", "custom_domain_id", "slug", "brand_uuid"):
        if obj.get(k) is not None:
            return str(obj[k])[:110]
    return ""


_OKTA_APP_KIND = {"OPENID_CONNECT": "OIDC", "SAML_2_0": "SAML",
                  "SAML_1_1": "SAML 1.1", "AUTO_LOGIN": "Auto Login",
                  "BROWSER_PLUGIN": "SWA", "SECURE_PASSWORD_STORE": "Password Store",
                  "BOOKMARK": "Bookmark", "BASIC_AUTH": "Basic Auth",
                  "WS_FEDERATION": "WS-Fed"}
_AUTH0_CLIENT_KIND = {"regular_web": "Regular Web", "spa": "Single Page App",
                      "native": "Native", "non_interactive": "Machine to Machine"}


def _kind(provider: str, resource_type: str, obj: dict) -> str:
    """Human 'type' badge for explorer object lists (OIDC, SAML, Bookmark...).
    Empty string when there is nothing meaningful to say."""
    if provider == "okta" and resource_type == "apps":
        m = obj.get("signOnMode") or ""
        return _OKTA_APP_KIND.get(m, m.replace("_", " ").title())
    if provider == "authentik":
        if resource_type == "applications":
            v = (obj.get("provider_obj") or {}).get("verbose_name") or ""
        elif resource_type in ("providers", "stages", "policies",
                               "property_mappings", "sources"):
            v = obj.get("verbose_name") or ""
        else:
            return ""
        return v.removesuffix(" Provider") if v else ""
    if provider == "auth0":
        if resource_type == "clients":
            t = obj.get("app_type") or ""
            return _AUTH0_CLIENT_KIND.get(t, t.replace("_", " ").title())
        if resource_type == "connections":
            return obj.get("strategy") or ""
    return ""


def _changed_fields(before: dict, after: dict) -> list[str]:
    from app.core.diff import normalize
    before, after = normalize(before), normalize(after)
    keys = set(before) | set(after)
    return sorted(k for k in keys
                  if json.dumps(before.get(k), sort_keys=True, default=str)
                  != json.dumps(after.get(k), sort_keys=True, default=str))[:25]


def extract_events(tenant_id: int, snapshot_ts: str, diff: dict) -> list:
    """diff = output of diff_exports(); returns unsaved Event ORM rows."""
    from app.models.db import Event
    rows = []
    for rtype, changes in (diff or {}).items():
        for obj in changes.get("added", []):
            rows.append(Event(tenant_id=tenant_id, snapshot_ts=snapshot_ts, event_type="add",
                              resource_type=rtype, object_id=_id(obj), object_name=_name(obj),
                              detail={}))
        for obj in changes.get("removed", []):
            rows.append(Event(tenant_id=tenant_id, snapshot_ts=snapshot_ts, event_type="delete",
                              resource_type=rtype, object_id=_id(obj), object_name=_name(obj),
                              detail={}))
        for ch in changes.get("changed", []):
            before, after = ch.get("before", {}), ch.get("after", {})
            rows.append(Event(tenant_id=tenant_id, snapshot_ts=snapshot_ts, event_type="update",
                              resource_type=rtype, object_id=str(ch.get("id", ""))[:110],
                              object_name=_name(after) or _name(before),
                              detail={"fields": _changed_fields(before, after)}))
    return rows
