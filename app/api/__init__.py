try:
    from app.api.client_api import SignageServerApiClient, signage_api
except ImportError:
    from client.app.api.client_api import SignageServerApiClient, signage_api

__all__ = ["SignageServerApiClient", "signage_api"]
