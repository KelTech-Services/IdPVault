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
                          "provider": 7}],  # raw API field name; engine
                                            # must rename to protocol_provider
    }
    files, report = tfexport.export_bundle(
        "authentik", export, ["providers", "applications"], "T", "ts1")
    assert report["total"] == 2 and not report["skipped"]
    assert "authentik_provider_oauth2.prov.id" in files["applications.tf"]
    # applications are slug-id in the provider: import by slug, not uuid
    assert 'id = "app"' in files["import.tf"]
    assert 'id = "7"' in files["import.tf"]  # int-pk types import by pk
    assert "README.md" in files and "variables.tf" in files


def test_slug_id_refs_use_uuid():
    export = {
        "flows": [{"pk": "flow-uuid-9", "name": "auth", "slug": "auth-flow",
                   "title": "Auth", "designation": "authentication"}],
        "providers": [{"component": "ak-provider-oauth2-form", "pk": 3,
                       "name": "p", "authorization_flow": "flow-uuid-9"}],
    }
    files, _ = tfexport.export_bundle(
        "authentik", export, ["flows", "providers"], "T", "ts")
    # reference to a slug-id resource must use .uuid (its .id is the slug)
    assert "authentik_flow.auth.uuid" in files["providers.tf"]
    assert 'id = "auth-flow"' in files["import.tf"]


def test_okta_group_profile_flattening():
    obj = {"id": "00g1mhw6k55XP0scT358", "type": "OKTA_GROUP",
           "objectClass": ["okta:user_group"],
           "profile": {"name": "Slack Users", "description": "Slack access",
                       "privileged": "FALSE"}}
    out = tfexport.export_object("okta", "groups", obj)
    assert out["ok"] and out["label"] == "slack_users"
    assert out["name"] == "Slack Users"
    assert 'name = "Slack Users"' in out["hcl"]
    assert 'description = "Slack access"' in out["hcl"]
    assert "custom_profile_attributes = jsonencode(" in out["hcl"]
    assert "privileged" in out["hcl"]
    assert not out["variables"]  # nothing required is missing
    assert 'id = "00g1mhw6k55XP0scT358"' in out["import_block"]


def test_okta_custom_saml_app():
    obj = {"id": "0oa1", "name": "template_saml_2_0", "label": "GSS Dynatrace",
           "signOnMode": "SAML_2_0", "status": "ACTIVE",
           "visibility": {"hide": {"web": True, "iOS": True},
                          "autoSubmitToolbar": False},
           "credentials": {"userNameTemplate": {
               "template": "${source.login}", "type": "BUILT_IN"}},
           "settings": {"signOn": {
               "ssoAcsUrl": "https://example.com/login",
               "recipient": "https://example.com/login",
               "audience": "https://example.com/",
               "subjectNameIdFormat":
                   "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
               "responseSigned": True, "signatureAlgorithm": "RSA_SHA256",
               "attributeStatements": [
                   {"type": "EXPRESSION", "name": "email",
                    "values": ["user.email"]},
                   {"type": "GROUP", "name": "role",
                    "filterType": "REGEX", "filterValue": ".*"}]}}}
    out = tfexport.export_object("okta", "apps", obj)
    assert out["ok"] and out["tf_type"] == "okta_app_saml"
    assert out["name"] == "GSS Dynatrace"      # label, never the catalog name
    assert out["label"] == "gss_dynatrace"
    hcl = out["hcl"]
    assert 'sso_url = "https://example.com/login"' in hcl      # ssoAcsUrl rename
    assert "preconfigured_app" not in hcl                      # custom app
    assert "hide_web = true" in hcl and "hide_ios = true" in hcl
    assert 'user_name_template = "$${source.login}"' in hcl    # escaped ${
    assert 'user_name_template_type = "BUILT_IN"' in hcl
    assert hcl.count("attribute_statements {") == 2            # nested blocks
    assert 'filter_type = "REGEX"' in hcl and 'filter_value = ".*"' in hcl
    assert "name" not in out["dropped"] and "visibility" not in out["dropped"]


def test_okta_oin_app_and_settings_json():
    obj = {"id": "0oa2", "name": "jira_datacenter", "label": "GSS Jira Test",
           "signOnMode": "SAML_2_0", "status": "ACTIVE",
           "settings": {"app": {"baseURL": "https://jira.example.com",
                                "helpUrl": None}}}
    out = tfexport.export_object("okta", "apps", obj)
    assert out["ok"]
    assert 'preconfigured_app = "jira_datacenter"' in out["hcl"]
    assert "app_settings_json = jsonencode(" in out["hcl"]
    assert "baseURL" in out["hcl"]


def test_okta_oauth_app_type_and_groups_claim():
    obj = {"id": "0oa3", "name": "oidc_client", "label": "COD Harbor",
           "signOnMode": "OPENID_CONNECT", "status": "ACTIVE",
           "credentials": {"oauthClient": {"client_id": "abc123"}},
           "settings": {"oauthClient": {
               "application_type": "web",
               "grant_types": ["authorization_code"],
               "redirect_uris": ["https://example.com/cb"],
               "response_types": ["code"],
               "groupsClaim": {"type": "FILTER", "filterType": "STARTS_WITH",
                               "value": "harbor_cod", "name": "groups"}}}}
    out = tfexport.export_object("okta", "apps", obj)
    assert out["ok"] and out["tf_type"] == "okta_app_oauth"
    hcl = out["hcl"]
    assert 'type = "web"' in hcl                    # application_type rename
    assert "groups_claim {" in hcl
    assert 'filter_type = "STARTS_WITH"' in hcl and 'value = "harbor_cod"' in hcl
    assert not out["variables"] or "type" not in " ".join(out["variables"])


def test_okta_system_app_skipped():
    out = tfexport.export_object(
        "okta", "apps", {"id": "x", "name": "saasure",
                         "label": "Okta Admin Console",
                         "signOnMode": "OPENID_CONNECT"})
    assert not out["ok"] and "system app" in out["reason"]


def test_auth0_client_blocks():
    obj = {"client_id": "c1", "name": "My API Client", "app_type": "non_interactive",
           "jwt_configuration": {"alg": "RS256", "lifetime_in_seconds": 36000},
           "refresh_token": {"rotation_type": "rotating", "expiration_type":
                             "expiring"}}
    out = tfexport.export_object("auth0", "clients", obj)
    assert out["ok"]
    hcl = out["hcl"]
    assert "jwt_configuration {" in hcl and 'alg = "RS256"' in hcl
    assert "refresh_token {" in hcl and 'rotation_type = "rotating"' in hcl
    assert "jwt_configuration" not in out["dropped"]


def test_authentik_oauth2_redirect_uris_and_outpost_providers():
    prov = {"component": "ak-provider-oauth2-form", "pk": 5, "name": "web",
            "authorization_flow": "f",
            "redirect_uris": [{"matching_mode": "strict",
                               "url": "https://example.com/cb"}]}
    hcl, dropped, _ = tfexport.emit_resource(
        "authentik", "authentik_provider_oauth2", prov, "providers", "web")
    assert "allowed_redirect_uris" in hcl and "https://example.com/cb" in hcl
    assert "redirect_uris" not in dropped
    op = {"pk": "o1", "name": "outpost", "type": "proxy", "providers": [5, 7]}
    hcl, dropped, _ = tfexport.emit_resource(
        "authentik", "authentik_outpost", op, "outposts", "outpost")
    assert "protocol_providers" in hcl and "providers" not in dropped


def test_okta_profile_mapping():
    obj = {"id": "prm1", "source": {"id": "src1", "name": "user"},
           "target": {"id": "tgt1", "name": "appuser"},
           "properties": {"firstName": {"expression": "user.firstName",
                                        "pushStatus": "PUSH"}}}
    out = tfexport.export_object("okta", "profile_mappings", obj)
    assert out["ok"]
    hcl = out["hcl"]
    assert 'source_id = "src1"' in hcl and 'target_id = "tgt1"' in hcl
    assert "mappings {" in hcl
    assert 'id = "firstName"' in hcl               # block-level required id
    assert 'expression = "user.firstName"' in hcl
    assert 'push_status = "PUSH"' in hcl
    assert "source" not in out["dropped"] and "properties" not in out["dropped"]


def test_okta_policy_groups_and_auth0_cross_origin():
    pol = {"id": "p1", "name": "MFA policy", "type": "MFA_ENROLL",
           "status": "ACTIVE",
           "conditions": {"people": {"groups": {"include": ["g1", "g2"]}}}}
    out = tfexport.export_object("okta", "policies_mfa", pol)
    assert out["ok"] and "groups_included" in out["hcl"] and "g1" in out["hcl"]
    cl = {"client_id": "c9", "name": "Web App", "app_type": "regular_web",
          "cross_origin_authentication": True}
    out = tfexport.export_object("auth0", "clients", cl)
    assert out["ok"] and "cross_origin_auth = true" in out["hcl"]
    assert "cross_origin_authentication" not in out["dropped"]
