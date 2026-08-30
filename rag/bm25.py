"""BM25 关键词检索：FTS5 为主路 + jieba 中文分词（改进 3）。

改进 3：用 jieba 分词替代 CJK bigram 暴力扫描。
  - 之前：bigram 扫描（"闰年计算" → ["闰年", "年计", "计算"]）
  - 现在：jieba 分词（"闰年计算" → ["闰年", "计算"]）
  分词更准确，不产生无意义的 bigram（如"年计"）。
"""
from __future__ import annotations

import re

import jieba

from rag.indexer import Indexer
from rag.models import SearchHit, rrf_merge

_ASCII_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


class BM25Search:
    def __init__(self, indexer: Indexer):
        self.indexer = indexer

    def search(self, query: str, top_k: int = 15) -> list[SearchHit]:
        conn = self.indexer._connect()
        ranked_lists: list[list[SearchHit]] = []

        # 1) ASCII 关键词 → FTS5 MATCH
        ascii_terms = [t for t in _ASCII_TOKEN.findall(query) if t.lower() not in _STOPWORDS]
        if ascii_terms:
            fts_query = " OR ".join(f'"{t}"' for t in ascii_terms)
            rows = conn.execute(
                "SELECT rowid, path, text, bm25(chunks_fts) AS r "
                "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY r LIMIT 60",
                (fts_query,),
            ).fetchall()
            hits = []
            for rowid, path, text, _rank in rows:
                start = _line_of(conn, rowid)
                hits.append(SearchHit(path, start, start, text[:800], source="bm25"))
            if hits:
                ranked_lists.append(hits)

        # 2) 中文 → jieba 分词（改进 3）
        cjk_words = _cjk_words(query)
        if cjk_words:
            scores: list[tuple[int, SearchHit]] = []
            for rowid, path, sl, el, text in conn.execute(
                "SELECT id, path, start_line, end_line, text FROM chunks"
            ).fetchall():
                cnt = sum(text.count(w) for w in cjk_words)
                if cnt:
                    scores.append((cnt, SearchHit(path, sl, el, text[:800], source="chinese")))
            scores.sort(key=lambda x: -x[0])
            if scores:
                ranked_lists.append([h for _, h in scores[:60]])

        return rrf_merge(ranked_lists, top_k)


_STOPWORDS = {
    "the", "and", "for", "with", "from", "import", "def", "class", "return",
    "this", "that", "into", "are", "was", "is", "not", "you", "your",
    "a", "an", "in", "on", "to", "of", "it", "as", "or", "be",
}


def _cjk_words(query: str) -> list[str]:
    """用 jieba 分词提取中文关键词。"""
    words = jieba.lcut(query)
    # 过滤掉非中文词（英文词由 FTS5 处理）和单字
    return [w for w in words if len(w) >= 2 and any("一" <= ch <= "鿿" for ch in w)]


def _line_of(conn, rowid: int) -> int:
    row = conn.execute("SELECT start_line FROM chunks WHERE id=?", (rowid,)).fetchone()
    return row[0] if row else 1