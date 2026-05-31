"""Tests for find_and_grep multi-file search."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools


def test_find_and_grep_searches_multiple_logs():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        log_dir = root / ".pulse" / "pcap_logs"
        log_dir.mkdir(parents=True)
        (log_dir / "verbose_a.txt").write_text("noise\nxmlObj:alpha\n", encoding="utf-8")
        (log_dir / "verbose_b.txt").write_text("other\nPassword,Username\n", encoding="utf-8")
        with patch("core.runtime_paths.search_roots", return_value=(root,)):
            with patch("core.runtime_paths.app_root", return_value=root):
                res = tools.find_and_grep("xmlObj|Password", path_glob="verbose_*.txt", max_files=5)
                assert res["success"], res.get("error")
                assert res["files_with_matches"] >= 1
                assert res["total_matches"] >= 1
