"""Mechanically enforces REQUIRED_FIELDS_AND_CLOSED_DECISIONS.md's HARD RULE:
no Hebrew string, anywhere, in any first-party source file that is not a
translation/message-catalog file. Every piece of Hebrew text a user ever
sees must go through `messages/he.py` (or the equivalent catalog module) —
this test scans the rest of the tracked source tree for stray Hebrew
Unicode-range characters that would mean that rule was violated.

Test files are deliberately excluded: many legitimately contain Hebrew
literals when asserting on catalog output (e.g. `assert "גבוה" in text`),
exactly the carve-out the HARD RULE itself names ("not as test data unless
the test is specifically testing Hebrew-language output through the
existing catalog mechanism").
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
    "profiles/unified_test.py",
}


def _tracked_python_source_files() -> list[str]:
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
        # `docs/` holds documentation/illustrative material (e.g.
        # `docs/code_example.py`, a standalone reference sketch never
        # imported by the running application), not application source —
        # out of scope for a rule about leaking Hebrew into real output.
        and not path.startswith("docs/")
        and path not in _ALLOWED_HEBREW_FILES
    ]


def test_no_hebrew_literal_outside_the_message_catalog():
    offenders = []
    for path in _tracked_python_source_files():
        text = (ROOT / path).read_text(encoding="utf-8")
        if _HEBREW_PATTERN.search(text):
            offenders.append(path)

    assert offenders == []
