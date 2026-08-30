"""gitops 的离线单测（在临时 git 仓库里验证快照/提交/undo/拒绝路径）。"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gitops import GitOps


def _init_repo(tmp: Path):
    for cmd in [
        ["git", "init"],
        ["git", "config", "user.email", "test@test"],
        ["git", "config", "user.name", "test"],
    ]:
        subprocess.run(cmd, cwd=tmp, capture_output=True)
    (tmp / ".gitignore").write_text(".chisel/\n", encoding="utf-8")
    (tmp / "a.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init", "--no-verify"], cwd=tmp, capture_output=True)
    return GitOps(str(tmp))


def test_status_clean(tmp_path):
    g = _init_repo(tmp_path)
    assert "clean" in g.status()


def test_status_shows_changes(tmp_path):
    g = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    assert "clean" not in g.status()


def test_before_write_snapshots(tmp_path):
    g = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    msg = g.before_write("a.txt")
    assert "Auto-snapshot" in msg
    ledger = g._load_ledger()
    assert len(ledger) == 1
    assert "clean" in g.status()


def test_before_write_noop_when_clean(tmp_path):
    g = _init_repo(tmp_path)
    assert g.before_write("a.txt") == ""


def test_before_write_with_task_context(tmp_path):
    """第 1 项：快照名带任务上下文。"""
    g = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    msg = g.before_write("a.txt", task="fix leap year")
    assert "fix leap year" in msg


def test_commit_and_ledger(tmp_path):
    g = _init_repo(tmp_path)
    (tmp_path / "b.txt").write_text("hello\n", encoding="utf-8")
    r = g.commit("add b")
    assert "Committed" in r
    assert len(g._load_ledger()) == 1


def test_undo_rolls_back_own_commit(tmp_path):
    g = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    g.commit("change a")
    r = g.undo(1)
    assert "Rolled back" in r
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1\n"
    assert len(g._load_ledger()) == 0


def test_undo_refuses_when_user_commit_on_top(tmp_path):
    g = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    g.commit("change a")
    (tmp_path / "user.txt").write_text("u\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "user commit", "--no-verify"], cwd=tmp_path, capture_output=True)
    r = g.undo(1)
    assert "Cannot undo" in r


def test_undo_not_repo(tmp_path):
    g = GitOps(str(tmp_path))
    assert "Not a git repository" in g.undo(1)


def test_diff(tmp_path):
    g = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    d = g.diff()
    assert "a.txt" in d and "v2" in d


def test_diff_with_context(tmp_path):
    """第 5 项：diff 支持 context 行数。"""
    g = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    d = g.diff(context=1)
    assert "a.txt" in d


def test_undo_new_file_created(tmp_path):
    g = _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")
    g.commit("add new file")
    assert (tmp_path / "new.txt").exists()
    g.undo(1)
    assert not (tmp_path / "new.txt").exists()


def test_preview_undo(tmp_path):
    """第 2 项：undo 预览。"""
    g = _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("v2\n", encoding="utf-8")
    g.commit("change a")
    p = g.preview_undo(1)
    assert "Preview" in p
    assert "a.txt" in p