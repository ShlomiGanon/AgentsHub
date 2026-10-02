"""Browser-side API controls used by the login-gated administration pages.

The forms in this module deliberately call the public JSON endpoints from the
browser.  The admin session only grants access to the pages; it does not grant
API permissions.  Every API request therefore carries the selected registered
identity in ``X-Identity`` and receives the normal authentication,
authorization, validation, and routing behaviour.
"""

from api.admin_api_console import API_CLIENT_SCRIPT, API_CONSOLE_STYLE, FLASH_MESSAGES
from api.admin_api_page_bodies import EVENTS_BODY, PROFILES_BODY, PROTOCOLS_BODY

__all__ = [
    "API_CLIENT_SCRIPT",
    "API_CONSOLE_STYLE",
    "EVENTS_BODY",
    "FLASH_MESSAGES",
    "PROFILES_BODY",
    "PROTOCOLS_BODY",
]
