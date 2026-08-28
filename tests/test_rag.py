"""rag 的离线单测（BM25 必开 + 向量禁用路径 + RRF 融合）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.hybrid import HybridIndex
from rag.models import SearchHit, rrf_merge
from rag.vector import VectorIndex


def _make_project(tmp):
    (tmp / "calc.py").write_text(
        "def add(x, y):\n"
        "    return x + y\n"
        "\n"
        "def fibonacci(n):\n"
        "    if n <= 1:\n"
        "        return n\n"
        "    return fibonacci(n-1) + fibonacci(n-2)\n",
        encoding="utf-8",
    )
    (tmp / "chinese.py").write_text(
        "def is_leap(year):\n"
        "    \"\"\"判断某年是否是闰年。\"\"\"\n"
        "    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n",
        encoding="utf-8",
    )


def _idx(tmp):
    return HybridIndex(str(tmp), client=None, enabled=False)


def test_bm25_finds_ascii_identifier(tmp_path):
    _make_project(tmp_path)
    idx = _idx(tmp_path)
    hits = idx.search("fibonacci 递归", top_k=3)
    assert hits
    assert any("fibonacci" in h.snippet for h in hits)


def test_bm25_cjk_bigram_finds_chinese(tmp_path):
    """FTS5 unicode61 不分词中文，CJK bigram 兜底要命中含中文的函数。"""
    _make_project(tmp_path)
    idx = _idx(tmp_path)
    hits = idx.search("闰年", top_k=3)
    assert hits
    assert any("is_leap" in h.snippet for h in hits)


def test_bm25_cjk_phrase(tmp_path):
    _make_project(tmp_path)
    idx = _idx(tmp_path)
    hits = idx.search("判断某年", top_k=3)
    assert hits
    assert any("is_leap" in h.snippet for h in hits)


def test_reconcile_reindexes_changed_file(tmp_path):
    _make_project(tmp_path)
    idx = _idx(tmp_path)
    idx.search("fibonacci", top_k=2)  # 先建索引
    # 修改 calc.py：新加一个符号
    (tmp_path / "calc.py").write_text(
        "def quick_sort(arr):\n"
        "    return arr\n",
        encoding="utf-8",
    )
    hits = idx.search("quick_sort", top_k=2)
    assert hits
    assert any("quick_sort" in h.snippet for h in hits)


def test_vector_disabled_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    v = VectorIndex(str(tmp_path), client=None, enabled=True)
    assert v.enabled() is False


def test_vector_disabled_when_flag_off(tmp_path):
    v = VectorIndex(str(tmp_path), client=None, enabled=False)
    assert v.enabled() is False
    assert v.search("x") == []


def test_vector_enabled_with_config(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3")
    monkeypatch.setenv("EMBEDDING_API_KEY", "ollama")
    v = VectorIndex(str(tmp_path), client=None, enabled=True)
    assert v.enabled() is True


def test_rrf_merge():
    a = [SearchHit("a.py", 1, 2, "s", "bm25"), SearchHit("b.py", 5, 6, "s", "bm25")]
    b = [SearchHit("b.py", 5, 6, "s", "vector"), SearchHit("c.py", 9, 9, "s", "vector")]
    merged = rrf_merge([a, b], 3)
    # b.py 两路都命中，应排第一
    assert merged[0].key() == ("b.py", 5)
    assert len(merged) == 3
