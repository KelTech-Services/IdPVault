"""Restore plan matching — the rename/recreate/duplicate cases found live."""
from app.core.restore import build_plan
from app.providers.authentik import AuthentikAdapter
from app.providers.okta import OktaAdapter
from app.providers.auth0 import Auth0Adapter


def _ak():
    return AuthentikAdapter("https://ak.example", "tok")


def test_rename_matches_by_id():
    a = _ak()
    snap = {"applications": [{"pk": "u1", "slug": "app-a", "name": "Old Name"}]}
    live = {"applications": [{"pk": "u1", "slug": "app-a", "name": "New Name"}]}
    a.begin_restore(snap, live)
    plan = build_plan(snap, live, None, a)
    assert [(i["action"], i["resource_type"]) for i in plan] == [("update", "applications")]


def test_recreate_matches_by_natural_key_no_duplicate():
    a = _ak()
    snap = {"applications": [{"pk": "OLD", "slug": "app-a", "name": "App"}]}
    live = {"applications": [{"pk": "NEW", "slug": "app-a", "name": "App"}]}
    a.begin_restore(snap, live)
    plan = build_plan(snap, live, None, a)
    assert plan[0]["action"] == "identical"      # pk alone is not config


def test_binding_follows_recreated_target():
    a = _ak()
    snap = {"applications": [{"pk": "OLD", "slug": "app-a", "name": "App"}],
            "policy_bindings": [{"pk": "b1", "policy": None, "group": "g1",
                                 "user": None, "target": "OLD", "order": 0}]}
    live = {"applications": [{"pk": "NEW", "slug": "app-a", "name": "App"}],
            "policy_bindings": [{"pk": "b9", "policy": None, "group": "g1",
                                 "user": None, "target": "NEW", "order": 0}]}
    a.begin_restore(snap, live)
    plan = build_plan(snap, live, None, a)
    binding = [i for i in plan if i["resource_type"] == "policy_bindings"][0]
    assert binding["action"] == "identical"      # composite key + remap converge


def test_okta_unsupported_visible_and_derived_hidden():
    o = OktaAdapter("https://o.example", "tok")
    snap = {"authorization_servers": [{"id": "a1", "name": "AS"}],
            "app_user_schemas": [{"id": "s1", "name": "schema"}]}
    live = {"authorization_servers": [], "app_user_schemas": []}
    plan = build_plan(snap, live, None, o)
    kinds = {(i["resource_type"], i.get("restorable")) for i in plan}
    assert ("authorization_servers", False) in kinds        # visible, unsupported
    assert all(i["resource_type"] != "app_user_schemas" for i in plan)  # hidden


def test_okta_app_matches_by_label():
    o = OktaAdapter("https://o.example", "tok")
    snap = {"apps": [{"id": "OLD", "name": "zoom", "label": "Zoom", "status": "ACTIVE"}]}
    live = {"apps": [{"id": "NEW", "name": "zoom", "label": "Zoom", "status": "ACTIVE"}]}
    plan = build_plan(snap, live, None, o)
    assert plan[0]["action"] == "identical"


def test_auth0_credential_packing_and_lists_as_sets():
    a0 = Auth0Adapter("https://t.auth0.com", "cid:secret")
    assert a0._creds() == ("cid", "secret")
    ak = _ak()
    x = ak.compare_form("outposts", {"providers": [3, 1, 2]})
    y = ak.compare_form("outposts", {"providers": [2, 3, 1]})
    assert x["providers"] == y["providers"]
