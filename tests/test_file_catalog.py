"""Ensures the English file catalog describes the current repository tree."""

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "docs" / "file_catalog.md"


def repository_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = result.stdout.decode("utf-8").split("\0")
    return {
        path.replace("\\", "/")
        for path in paths
        if path and (ROOT / path).is_file() and path != ".env"
    }


def catalog_paths() -> set[str]:
    text = CATALOG.read_text(encoding="utf-8")
    return set(re.findall(r"^\| `([^`]+)` \|", text, flags=re.MULTILINE))


def test_file_catalog_is_complete_and_has_no_stale_paths():
    assert catalog_paths() == repository_files()
