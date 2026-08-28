"""tools.py 的离线单测：edit_file 多策略 + 危险命令回归 + 工具分发。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools


def test_dangerous_patterns_regression():
    for cmd in [
        "rm -rf /tmp/x",
        "rm -r /tmp/x",
        "git push origin main",
        "git reset --hard HEAD",
        "git push --force",
        "DROP TABLE users",
        "DELETE FROM users",
        "dd if=/dev/zero of=/dev/sda",
        "rd /s /q C:\\x",
        "del /s /q C:\\x",
        "Remove-Item -Recurse C:\\x",
    ]:
        assert tools.is_dangerous(cmd), f"应判定危险: {cmd}"


def test_dev_null_not_dangerous():
    assert not tools.is_dangerous("python run.py 2>/dev/null")


def test_bash_safe_commands_not_dangerous():
    assert not tools.is_dangerous("ls -la")
    assert not tools.is_dangerous("python test.py")


def _ws(tmp):
    return str(tmp)


def _write(tmp, path, content):
    p = Path(tmp) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_edit_exact(tmp_path):
    _write(tmp_path, "a.py", "def f():\n    return 1\n")
    r = tools.execute_tool("edit_file", {
        "path": "a.py",
        "original_lines": "    return 1",
        "updated_lines": "    return 2",
    }, str(tmp_path))
    assert "精确匹配" in r
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "def f():\n    return 2\n"


def test_edit_trim_blank_lines(tmp_path):
    """模型多打/漏打首尾空行时，去空行后仍能匹配。"""
    _write(tmp_path, "a.py", "def f():\n    return 1\n")
    r = tools.execute_tool("edit_file", {
        "path": "a.py",
        "original_lines": "\n\ndef f():\n    return 1\n\n",
        "updated_lines": "def f():\n    return 99",
    }, str(tmp_path))
    assert "去空行" in r
    assert "return 99" in (tmp_path / "a.py").read_text(encoding="utf-8")


def test_edit_elision(tmp_path):
    """original 用 ... 省略中间代码，能匹配并整体替换。"""
    src = "def foo():\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n"
    _write(tmp_path, "a.py", src)
    r = tools.execute_tool("edit_file", {
        "path": "a.py",
        "original_lines": "def foo():\n    ...\n    return a + b + c\n",
        "updated_lines": "def foo():\n    a = 10\n    b = 20\n    return a + b\n",
    }, str(tmp_path))
    assert "省略" in r
    out = (tmp_path / "a.py").read_text(encoding="utf-8")
    assert "a = 10" in out and "c = 3" not in out


def test_edit_indent_tolerant(tmp_path):
    """模型缩进漂移（少一个缩进层级）时仍能匹配。"""
    _write(tmp_path, "a.py", "class X:\n    def f(self):\n        return 1\n")
    r = tools.execute_tool("edit_file", {
        "path": "a.py",
        "original_lines": "def f(self):\nreturn 1",
        "updated_lines": "def f(self):\n    return 2",
    }, str(tmp_path))
    assert "缩进容错" in r
    assert "return 2" in (tmp_path / "a.py").read_text(encoding="utf-8")


def test_edit_failure_diagnostic(tmp_path):
    _write(tmp_path, "a.py", "def f():\n    return 1\n")
    r = tools.execute_tool("edit_file", {
        "path": "a.py",
        "original_lines": "def f():\n    return 999",
        "updated_lines": "def f():\n    return 2",
    }, str(tmp_path))
    assert "edit_file 失败" in r
    assert "read_file" in r


def test_edit_append_empty_original(tmp_path):
    _write(tmp_path, "a.py", "x = 1\n")
    r = tools.execute_tool("edit_file", {
        "path": "a.py",
        "original_lines": "",
        "updated_lines": "y = 2\n",
    }, str(tmp_path))
    assert "追加" in r
    assert "y = 2" in (tmp_path / "a.py").read_text(encoding="utf-8")


def test_unknown_tool():
    r = tools.execute_tool("nope", {}, ".")
    assert "未知工具" in r


def test_path_traversal_blocked(tmp_path):
    _write(tmp_path, "a.py", "x=1\n")
    r = tools.execute_tool("read_file", {"path": "../secret.txt"}, str(tmp_path))
    assert "禁止访问" in r or "工具执行出错" in r


def test_tool_error_feedback(tmp_path):
    """read_file 不存在文件时返回友好提示而非抛异常。"""
    r = tools.execute_tool("read_file", {"path": "nope.py"}, str(tmp_path))
    assert "文件不存在" in r
