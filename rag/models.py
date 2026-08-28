"""检索模块共享的数据模型与融合算法。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    file: str
    start_line: int
    end_line: int
    text: str


@dataclass
class SearchHit:
    file: str
    start_line: int
    end_line: int
    snippet: str
    source: str  # bm25 / cjk / vector

    def key(self) -> tuple:
        return (self.file, self.start_line)


def rrf_merge(ranked_lists: list[list[SearchHit]], top_k: int, k: int = 60) -> list[SearchHit]:
    """Reciprocal Rank Fusion：把多路检索结果按排名融合。

    每路给 (rank) 分 1/(k+rank)，同一 (file,start_line) 的路分累加，按总分取 top_k。
    对路数少、分数不可比的两路（BM25 与向量）友好。
    """
    scores: dict[tuple, float] = {}
    items: dict[tuple, SearchHit] = {}
    for lst in ranked_lists:
        for rank, hit in enumerate(lst):
            key = hit.key()
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            items[key] = hit
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    return [items[key] for key, _ in ordered[:top_k]]
