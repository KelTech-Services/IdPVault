"""Terraform export engine (app/core/tfexport.py) - pure-function tests."""
from app.core import tfexport


def test_sanitize_label():
    assert tfexport.sanitize_label("My App (prod)") == "my_app_prod"
    assert tfexport.sanitize_label("9lives") == "r_9lives"
    assert tfexport.sanitize_label("") == "unnamed"
    assert tfexport.sanitize_label("___") == "unnamed"


def test_render_escaping():
    assert tfexport._render('say "hi" ${x}') == '"say \\"hi\\" $${x}"'
    assert tfexport._render(True) == "true"
    assert tfexport._render(7) == "7"
    lst = tfexport._render([1, 2])
    assert lst.startswith("[") and "1" in lst and "2" in lst
    assert tfexport._render([]) == "[]"
    multi = tfexport._render("a\nb")
    assert multi.startswith("<<-EOT") and "a\nb" in multi


def test_authentik_component_derivation():
    tf, why = tfexport.tf_resource_for(
        "authentik", "providers", {"component": "ak-provider-oauth2-form"})
    assert tf == "authentik_provider_oauth2" and why is None
    tf, why = tfexport.tf_resource_for(
        "authentik", "property_mappings",
        {"component": "ak-property-mapping-provider-scope-form"})
    assert tf == "authentik_property_mapping_provider_scope"
    tf, why = tfexport.tf_resource_for("authentik", "sources", {"component": ""})
    assert tf is None and "built-in" in why
    tf, why = tfexport.tf_resource_for("authentik", "blueprints", {})
    assert tf is None


def test_okta_discriminators():
    tf, _ = tfexport.tf_resource_for("okta", "apps", {"signOnMode": "OPENID_CONNECT"})
    assert tf == "okta_app_oauth"
    tf, _ = tfexport.tf_resource_for("okta", "apps", {"signOnMode": "SAML_2_0"})
    assert tf == "okta_app_saml"
    tf, why = tfexport.tf_resource_for("okta", "groups", {"type": "BUILT_IN"})
    assert tf is None and "built-in" in why.lower()
    tf, _ = tfexport.tf_resource_for("okta", "policies_access", {})
    assert tf == "okta_app_signon_policy"
    tf, _ = tfexport.tf_resource_for("okta", "idps", {"type": "GOOGLE"})
    assert tf == "okta_idp_social"


def test_secrets_never_emitted():
    obj = {"component": "ak-provider-oauth2-form", "pk": 5, "name": "web",
           "client_id": "abc", "client_secret": "SUPERSECRET",
           "authorization_flow": "f-uuid"}
    hcl, dropped, variables = tfexport.emit_resource(
        "authentik", "authentik_provider_oauth2", obj, "providers", "web")
    assert "SUPERSECRET" not in hcl
    assert "var.web_client_secret" in hcl
    assert any(v[0] == "web_client_secret" for v in variables)


def test_bundle_refs_and_imports():
    export = {
        "providers": [{"component": "ak-provider-oauth2-form", "pk": 7,
                       "name": "prov", "authorization_flow": "x"}],
        "applications": [{"pk": "app-uuid-1", "name": "App", "slug": "app",
                          "protocol_provider": 7}],
    }
    files, report = tfexport.export_bundle(
        "authentik", export, ["providers", "applications"], "T", "ts1")
    assert report["total"] == 2 and not report["skipped"]
    assert "authentik_provider_oauth2.prov.id" in files["applications.tf"]
    assert 'id = "app-uuid-1"' in files["import.tf"]
    assert "README.md" in files and "variables.tf" in files
