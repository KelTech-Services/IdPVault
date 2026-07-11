from app.providers.base import ProviderAdapter


def get_adapter(provider: str, base_url: str, credentials: str) -> ProviderAdapter:
    if provider == "authentik":
        from app.providers.authentik import AuthentikAdapter
        return AuthentikAdapter(base_url, credentials)
    if provider == "okta":
        from app.providers.okta import OktaAdapter
        return OktaAdapter(base_url, credentials)
    if provider == "auth0":
        from app.providers.auth0 import Auth0Adapter
        return Auth0Adapter(base_url, credentials)
    raise ValueError(f"unknown provider: {provider}")
