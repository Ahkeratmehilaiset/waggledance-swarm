"""FastAPI dependency providers.

All Depends(...) injections resolve through the functions defined here.
The Container is stored on app.state.container at startup.
"""

from fastapi import Request


def get_container(request: Request):
    """Retrieve the DI container from the application state."""
    return request.app.state.container


def get_chat_service(request: Request):
    """Provide the ChatService instance from the container."""
    return get_container(request).chat_service


def get_readiness_service(request: Request):
    """Provide the ReadinessService instance from the container."""
    return get_container(request).readiness_service


def get_memory_service(request: Request):
    """Provide the MemoryService instance from the container."""
    return get_container(request).memory_service


def get_autonomy_service(request: Request):
    """Provide the AutonomyService instance from the container."""
    return get_container(request).autonomy_service


def require_auth(request: Request):
    """Placeholder -- actual auth is enforced by BearerAuthMiddleware.

    This dependency exists so that routes can explicitly declare their
    auth requirement in the function signature for documentation
    purposes.  The middleware handles the actual token validation.
    """
    pass
