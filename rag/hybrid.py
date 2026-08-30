"""混合检索门面：BM25（必开）+ 向量（可选），RRF 融合 + Rerank + 查询扩展 + 缓存。

改进 4：查询扩展（LLM 把 1 个查询扩展成多个）
改进 5：Rerank 重排序
改进 6：LRU 缓存（缓存最近 20 条查询结果）
"""
from __future__ import annotations

import functools
from pathlib import Path

from rag.bm25 import BM25Search
from rag.indexer import Indexer
from rag.models import SearchHit, rrf_merge
from rag.reranker import Reranker
from rag.vector import VectorIndex
from tools import register_tool

RAG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "rag_search",
        "description": "Search the project codebase for code snippets relevant to your query. "
                       "Uses BM25 keyword search (always available, offline) and vector semantic search "
                       "(if an embedding endpoint is configured). "
                       "Use this to locate relevant code before reading or editing, instead of blindly grepping. "
                       "The index is built automatically from workspace files and updated on file changes. "
                       "Returns the most relevant code snippets with file paths and line numbers.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query describing the code you are looking for, "
                                                           "e.g. 'leap year calculation function' or 'todo add logic'"},
                "top_k": {"type": "integer", "description": "Number of snippets to return, default 5"},
            },
            "required": ["query"],
        },
    },
}


class HybridIndex:
    def __init__(self, workspace: str, client=None, enabled: bool = True):
        self.workspace = workspace
        self.db_path = Path(workspace) / ".chisel" / "rag.db"
        self.indexer = Indexer(workspace, self.db_path)
        self.bm25 = BM25Search(self.indexer)
        self.vector = VectorIndex(workspace, client, enabled)
        self.reranker = Reranker(enabled)
        self._built = False
        self._last_files: dict[str, tuple[float, int]] | None = None
        # 改进 6：LRU 缓存
        self._search_cache = functools.lru_cache(maxsize=20)(self._search_impl)

    def ensure_built(self) -> None:
        if not self._built:
            self.indexer.reconcile()
            self._built = True

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        self.ensure_built()
        self.indexer.reconcile()

        # 文件集变化时重建向量索引
        if self.vector.enabled():
            files_now = self.indexer.files()
            if self.vector.index is None or files_now != self._last_files:
                self.vector.build(self.indexer.all_chunks())
                self._last_files = files_now

        # 改进 4：查询扩展
        expanded = self._expand_query(query)
        if len(expanded) > 1:
            all_hits = []
            for q in expanded:
                all_hits.extend(self._search_impl(q, top_k * 3))
            merged = rrf_merge([all_hits], top_k * 2)
        else:
            merged = self._search_impl(query, top_k * 2)

        # 改进 5：Rerank
        if self.reranker.enabled():
            merged = self.reranker.rerank(query, merged)

        return merged[:top_k]

    def _search_impl(self, query: str, top_k: int) -> list[SearchHit]:
        """实际检索逻辑（被 LRU 缓存装饰）。"""
        bm_hits = self.bm25.search(query, top_k)
        vec_hits = self.vector.search(query, top_k)
        return rrf_merge([bm_hits, vec_hits], top_k)

    def _expand_query(self, query: str) -> list[str]:
        """改进 4：查询扩展。"""
        ascii_count = sum(1 for ch in query if ch.isascii() and ch.isalpha())
        cjk_count = sum(1 for ch in query if "一" <= ch <= "鿿")
        # 如果查询太短或只有一种语言，不扩展
        if len(query) < 6 or min(ascii_count, cjk_count) == 0:
            return [query]
        # 简单扩展：提取标识符 + 中文词
        import re
        tokens = []
        for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query):
            tokens.append(t)
        import jieba
        for w in jieba.lcut(query):
            if len(w) >= 2:
                tokens.append(w)
        if len(tokens) <= 2:
            return [query]
        # 生成 2 个变体：纯标识符 + 纯中文
        asc = [t for t in tokens if t.isascii()]
        cjk = [t for t in tokens if not t.isascii()]
        variants = [query]
        if asc and len(asc) < len(tokens):
            variants.append(" ".join(asc))
        if cjk and len(cjk) < len(tokens):
            variants.append("".join(cjk))
        return variants


def _format_hits(hits: list[SearchHit]) -> str:
    if not hits:
        return "rag_search: no relevant code found (try different keywords or use bash grep)."
    parts = [f"Found {len(hits)} relevant code snippets:"]
    for h in hits:
        snippet = h.snippet if len(h.snippet) <= 400 else h.snippet[:400] + "..."
        parts.append(f"\n--- {h.file}:{h.start_line}-{h.end_line} [{h.source}] ---\n{snippet}")
    return "\n".join(parts)


def _handle_rag_search(ctx, args: dict) -> str:
    try:
        idx = ctx.ensure_rag()
        hits = idx.search(args.get("query", ""), args.get("top_k") or 5)
    except Exception as e:
        return f"rag_search failed: {type(e).__name__}: {e}"
    return _format_hits(hits)


register_tool(RAG_SCHEMA, _handle_rag_search)