"""BM25 关键词检索：SQLite FTS5 为主路 + CJK bigram 兜底。

设计来源（借鉴 MiMo memory 的 FTS5+BM25 思路，自写）：
  - 英文/代码标识符：FTS5 MATCH（unicode61 分词天然支持）。
  - 中文：FTS5 unicode61 不分词，改用"重叠 bigram 扫描 chunk 文本"打分兜底
    （代码库小，全量扫描可接受；bigram 对中文召回稳定）。
"""
from __future__ import annotations

import re

from rag.indexer import Indexer
from rag.models import SearchHit, rrf_merge

_CJK = re.compile(r"[一-鿿]+")
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

        # 2) 中文 → 重叠 bigram 扫描兜底
        bigrams = _bigrams(query)
        if bigrams:
            scores: list[tuple[int, SearchHit]] = []
            for rowid, path, sl, el, text in conn.execute(
                "SELECT id, path, start_line, end_line, text FROM chunks"
            ).fetchall():
                cnt = sum(text.count(bg) for bg in bigrams)
                if cnt:
                    scores.append((cnt, SearchHit(path, sl, el, text[:800], source="cjk")))
            scores.sort(key=lambda x: -x[0])
            if scores:
                ranked_lists.append([h for _, h in scores[:60]])

        return rrf_merge(ranked_lists, top_k)


_STOPWORDS = {
    "the", "and", "for", "with", "from", "import", "def", "class", "return",
    "this", "that", "into", "are", "was", "is", "not", "you", "your",
}


def _bigrams(query: str) -> list[str]:
    result = []
    for run in _CJK.findall(query):
        result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return result


def _line_of(conn, rowid: int) -> int:
    row = conn.execute("SELECT start_line FROM chunks WHERE id=?", (rowid,)).fetchone()
    return row[0] if row else 1
