"""HTML/CSS/JS template for the Behind-the-Scenes live dashboard."""

from api.admin_bts_markup import BTS_MARKUP
from api.admin_bts_script import BTS_SCRIPT
from api.admin_bts_style import BTS_HEAD_AND_STYLE

HTML_PAGE_TEMPLATE = BTS_HEAD_AND_STYLE + BTS_MARKUP + BTS_SCRIPT
