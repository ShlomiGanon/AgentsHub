"""Fail if first-party source (except message catalogs) contains Hebrew.

Every piece of Hebrew text a user ever sees must go through
`messages/he.py` (or the equivalent catalog module). This test scans the
rest of the tracked source tree for stray Hebrew Unicode-range characters.

Test files are deliberately excluded: many legitimately contain Hebrew
literals when asserting on catalog output, which is allowed only as test
data for Hebrew-language catalog output.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The Hebrew Unicode block (letters, niqqud, punctuation) — enough to catch
# any real Hebrew string without false-positives on ordinary ASCII source.
_HEBREW_PATTERN = re.compile("[֐-׿]")

_ALLOWED_HEBREW_FILES = {
    "messages/en.py",  # imported for parity assertions elsewhere; holds none, but harmless to allow
    "messages/he.py",
}


def _tracked_python_source_files() -> list[str]:
    """Tracked python source files."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = [p.replace("\\", "/") for p in result.stdout.decode("utf-8").split("\0") if p]
    return [
        path for path in paths
        if (ROOT / path).is_file()
        and not path.startswith("tests/")
        and path not in _ALLOWED_HEBREW_FILES
    ]


def test_no_hebrew_literal_outside_the_message_catalog():
    """No hebrew literal outside the message catalog."""
    offenders = []
    for path in _tracked_python_source_files():
        text = (ROOT / path).read_text(encoding="utf-8")
        if _HEBREW_PATTERN.search(text):
            offenders.append(path)

    assert offenders == []
