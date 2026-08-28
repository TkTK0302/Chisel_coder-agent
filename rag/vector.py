"""向量检索：faiss + OpenAI 兼容 embedding 端点（无配置则自动禁用）。

设计决策：
  - DeepSeek/Moonshot 目前没有 embedding API，因此向量路默认关闭；
    配置 EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL（如本机
    Ollama bge-m3，或 SiliconFlow 等 OpenAI 兼容端点）后自动启用。
  - 索引时把全部 chunk 批量 embed 建 faiss（IndexFlatIP + L2 归一化 = 余弦）；
    查询时 embed query 再检索。代码库小，整库向量在内存中足够。
"""
from __future__ import annotations

import os

from core.config import get
from rag.models import Chunk, SearchHit

FAISS_BATCH = 64


class VectorIndex:
    def __init__(self, workspace: str, client, enabled: bool = True):
        self.workspace = workspace
        self.enabled_flag = enabled and bool(get("EMBEDDING_BASE_URL") and get("EMBEDDING_MODEL"))
        self.index = None
        self.chunks: list[Chunk] = []
        self.client = client

    def enabled(self) -> bool:
        return self.enabled_flag

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import openai

        emb = openai.OpenAI(
            base_url=get("EMBEDDING_BASE_URL"),
            api_key=get("EMBEDDING_API_KEY") or "ollama",
        )
        out = []
        for i in range(0, len(texts), FAISS_BATCH):
            resp = emb.embeddings.create(model=get("EMBEDDING_MODEL"), input=texts[i:i + FAISS_BATCH])
            out.extend(d.embedding for d in resp.data)
        return out

    def build(self, chunks: list[Chunk]) -> None:
        if not self.enabled_flag:
            return
        import faiss
        import numpy as np

        vecs = np.array(self._embed([c.text for c in chunks]), dtype="float32")
        faiss.normalize_L2(vecs)
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        self.index = index
        self.chunks = chunks

    def search(self, query: str, top_k: int = 15) -> list[SearchHit]:
        if not self.enabled_flag or self.index is None or not self.chunks:
            return []
        import faiss
        import numpy as np

        qv = np.array(self._embed([query]), dtype="float32")
        faiss.normalize_L2(qv)
        n = min(top_k * 3, len(self.chunks))
        _d, idx = self.index.search(qv, n)
        return [
            SearchHit(self.chunks[i].file, self.chunks[i].start_line, self.chunks[i].end_line,
                      self.chunks[i].text[:800], source="vector")
            for i in idx[0] if i >= 0
        ]
