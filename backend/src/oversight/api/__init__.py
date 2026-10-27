"""HTTP: routes, SSE framing, the error format and static files.

Other modules import only what this file exports; everything else here is internal.
"""

from oversight.api.commands import Commands
from oversight.api.errors import ApiError, error_response, install_error_handlers
from oversight.api.routes import create_router
from oversight.api.static import add_static

__all__ = [
    "ApiError",
    "Commands",
    "add_static",
    "create_router",
    "error_response",
    "install_error_handlers",
]
