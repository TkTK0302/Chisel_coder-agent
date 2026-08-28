"""perception/ast_index 的离线单测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from perception.ast_index import ASTIndex


def _make_project(tmp):
    (tmp / "a.py").write_text(
        "def add(x, y):\n"
        "    return x + y\n"
        "\n"
        "def calc():\n"
        "    return add(1, 2)\n",
        encoding="utf-8",
    )
    (tmp / "b.py").write_text(
        "from a import add\n"
        "print(add(3, 4))\n",
        encoding="utf-8",
    )
    (tmp / "notes.txt").write_text("add 函数在这里出现过\n", encoding="utf-8")


def test_find_definition(tmp_path):
    _make_project(tmp_path)
    idx = ASTIndex(str(tmp_path))
    r = idx.find_definition("add")
    assert "a.py:1" in r
    assert "def add" in r


def test_find_references(tmp_path):
    _make_project(tmp_path)
    idx = ASTIndex(str(tmp_path))
    r = idx.find_references("add")
    assert "add(1, 2)" in r  # a.py 里的调用点（return add(1, 2)）
    assert "b.py" in r  # 另一个文件的引用
    assert "定义处" in r


def test_list_symbols(tmp_path):
    _make_project(tmp_path)
    idx = ASTIndex(str(tmp_path))
    r = idx.list_symbols()
    assert "def add" in r
    assert "def calc" in r


def test_list_symbols_filter(tmp_path):
    _make_project(tmp_path)
    idx = ASTIndex(str(tmp_path))
    r = idx.list_symbols(pattern="calc")
    assert "calc" in r
    assert "add(" not in r


def test_list_symbols_path_limit(tmp_path):
    _make_project(tmp_path)
    idx = ASTIndex(str(tmp_path))
    r = idx.list_symbols(path="a.py")
    assert "a.py" in r
    assert "b.py" not in r


def test_grep_fallback_non_py(tmp_path):
    _make_project(tmp_path)
    idx = ASTIndex(str(tmp_path))
    r = idx.find_definition("add")
    # Python 定义优先命中
    assert "a.py:1" in r


def test_symbols_skip_pycache(tmp_path):
    _make_project(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.py").write_text("def junk(): pass\n", encoding="utf-8")
    idx = ASTIndex(str(tmp_path))
    r = idx.list_symbols()
    assert "junk" not in r
