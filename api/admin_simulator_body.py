"""HTML/JS body for the admin scenario simulator page."""

from api.admin_api_pages import FLASH_MESSAGES
from api.admin_simulator_html import SIMULATOR_HEADER, SIMULATOR_MAIN
from api.admin_simulator_script import SIMULATOR_SCRIPT

SIMULATOR_BODY = SIMULATOR_HEADER + FLASH_MESSAGES + SIMULATOR_MAIN + SIMULATOR_SCRIPT
