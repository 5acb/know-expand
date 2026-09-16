import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from know_expand.observe import _is_path_inside_cwd, _list_files, _spawn_pipeline


def test_is_path_inside_cwd():
    cwd = Path.cwd().resolve()
    # Paths inside CWD
    assert _is_path_inside_cwd(cwd / "know_expand") is True
    assert _is_path_inside_cwd("know_expand/stages") is True
    assert _is_path_inside_cwd(".") is True

    # Paths outside CWD
    assert _is_path_inside_cwd("/etc") is False
    assert _is_path_inside_cwd("/tmp") is False
    assert _is_path_inside_cwd("../..") is False


def test_list_files_prevents_directory_traversal():
    cwd = Path.cwd().resolve()
    # If target directory is outside CWD, it should fallback to CWD
    res = _list_files("/etc")
    assert res["cwd"] == str(cwd)

    res2 = _list_files("../..")
    assert res2["cwd"] == str(cwd)


def test_spawn_pipeline_prevents_arbitrary_paths(tmp_path):
    # Passing an input outside CWD should raise ValueError
    with pytest.raises(ValueError, match="Access denied"):
        _spawn_pipeline(tmp_path, {"input": "/etc/passwd", "resume": False})

    # Passing a valid path inside CWD or URL should not raise ValueError
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(pid=123)
        # URL input
        pid = _spawn_pipeline(tmp_path, {"input": "https://example.com/file.pdf", "resume": False})
        assert pid == 123
        
        # Valid path inside CWD
        dummy_file = Path("dummy_test.pdf")
        dummy_file.touch()
        try:
            pid = _spawn_pipeline(tmp_path, {"input": "dummy_test.pdf", "resume": False})
            assert pid == 123
        finally:
            if dummy_file.exists():
                dummy_file.unlink()
