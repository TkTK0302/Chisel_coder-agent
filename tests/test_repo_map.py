"""perception/repo_map 的离线单测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from perception.repo_map import get_repo_map


def test_repo_map_contains_python_symbols(tmp_path):
    (tmp_path / "calc.py").write_text("def add(x, y): return x + y\n", encoding="utf-8")
    (tmp_path / "util.py").write_text("class Helper:\n    pass\n", encoding="utf-8")
    rm = get_repo_map(str(tmp_path))
    assert "def add" in rm
    assert "class Helper" in rm


def test_repo_map_skips_pycache(tmp_path):
    (tmp_path / "calc.py").write_text("def add(x, y): return x + y\n", encoding="utf-8")
    p = tmp_path / "__pycache__" / "junk.py"
    p.parent.mkdir()
    p.write_text("def hidden(): pass\n", encoding="utf-8")
    rm = get_repo_map(str(tmp_path))
    assert "hidden" not in rm


def test_repo_map_empty_on_no_py(tmp_path):
    rm = get_repo_map(str(tmp_path))
    assert rm == ""


def test_repo_map_lists_source_files(tmp_path):
    """非 Python 源文件也列出符号。"""
    (tmp_path / "calc.js").write_text("function add(x, y) { return x + y; }\n", encoding="utf-8")
    rm = get_repo_map(str(tmp_path))
    assert "calc.js" in rm