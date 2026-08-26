try:
    from app.auth.client_auth import ClientAuthManager, client_auth
except ImportError:
    from client.app.auth.client_auth import ClientAuthManager, client_auth

__all__ = ["ClientAuthManager", "client_auth"]
