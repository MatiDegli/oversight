"""HTTP: routes, SSE framing, the error format and static files.

Other modules import only what this file exports; everything else here is internal.
"""

from oversight.api.body_limit import BodyLimit
from oversight.api.commands import Commands
from oversight.api.errors import ApiError, error_response, install_error_handlers
from oversight.api.routes import create_router
from oversight.api.security import ApiGuard, AuthorityPolicy
from oversight.api.settings import add_settings_routes
from oversight.api.static import add_static

__all__ = [
    "ApiError",
    "ApiGuard",
    "AuthorityPolicy",
    "BodyLimit",
    "Commands",
    "add_settings_routes",
    "add_static",
    "create_router",
    "error_response",
    "install_error_handlers",
]
