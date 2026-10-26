"""HTTP: routes, SSE framing, the error format and static files.

Other modules import only what this file exports; everything else here is internal.
"""

from oversight.api.errors import ApiError, error_response, install_error_handlers
from oversight.api.routes import create_router

__all__ = ["ApiError", "create_router", "error_response", "install_error_handlers"]
