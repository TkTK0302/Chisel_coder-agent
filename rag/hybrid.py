"""混合检索门面：BM25（必开）+ 向量（可选），RRF 融合。

rag_search 工具入口：懒建索引 → mtime 对账 → 两路检索 → RRF 融合 → 返回片段。
"""
from __future__ import annotations

from pathlib import Path

from rag.bm25 import BM25Search
from rag.indexer import Indexer
from rag.models import SearchHit, rrf_merge
from rag.vector import VectorIndex
from tools import register_tool

RAG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "rag_search",
        "description": "在项目代码库中检索与查询相关的代码片段。离线用 BM25 关键词检索；"
                       "配置了 embedding 端点时自动叠加向量语义检索（RRF 融合）。"
                       "用于快速定位相关代码，比逐个 grep 更高效。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索描述，如“计算闰年的函数”或“todo 的添加逻辑”"},
                "top_k": {"type": "integer", "description": "返回片段数，默认 5"},
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
        self._built = False
        self._last_files: dict[str, float] | None = None

    def ensure_built(self) -> None:
        if not self._built:
            self.indexer.reconcile()
            self._built = True

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        self.ensure_built()
        self.indexer.reconcile()

        # 文件集变化时重建向量索引（懒：仅在启用了向量且首次/文件变更时）
        if self.vector.enabled():
            files_now = self.indexer.files()
            if self.vector.index is None or files_now != self._last_files:
                self.vector.build(self.indexer.all_chunks())
                self._last_files = files_now

        bm_hits = self.bm25.search(query, top_k * 3)
        vec_hits = self.vector.search(query, top_k * 3)
        return rrf_merge([bm_hits, vec_hits], top_k)


def _format_hits(hits: list[SearchHit]) -> str:
    if not hits:
        return "rag_search 未找到相关代码片段（可尝试换更具体的关键词，或改用 bash grep）。"
    parts = [f"检索到 {len(hits)} 段相关代码："]
    for h in hits:
        snippet = h.snippet if len(h.snippet) <= 400 else h.snippet[:400] + "..."
        parts.append(f"\n--- {h.file}:{h.start_line}-{h.end_line} [{h.source}] ---\n{snippet}")
    return "\n".join(parts)


def _handle_rag_search(ctx, args: dict) -> str:
    try:
        idx = ctx.ensure_rag()
        hits = idx.search(args.get("query", ""), args.get("top_k") or 5)
    except Exception as e:
        return f"rag_search 失败: {type(e).__name__}: {e}"
    return _format_hits(hits)


register_tool(RAG_SCHEMA, _handle_rag_search)
