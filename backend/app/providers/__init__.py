from app.providers.base import ProviderAdapter


def _adapter_cls(provider: str) -> type[ProviderAdapter]:
    if provider == "authentik":
        from app.providers.authentik import AuthentikAdapter
        return AuthentikAdapter
    if provider == "okta":
        from app.providers.okta import OktaAdapter
        return OktaAdapter
    if provider == "auth0":
        from app.providers.auth0 import Auth0Adapter
        return Auth0Adapter
    raise ValueError(f"unknown provider: {provider}")


def get_adapter(provider: str, base_url: str, credentials: str) -> ProviderAdapter:
    return _adapter_cls(provider)(base_url, credentials)


def identity_supported(provider: str) -> bool:
    try:
        return bool(_adapter_cls(provider).supports_identity)
    except ValueError:
        return False
