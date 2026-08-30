"""重排序（Rerank）：用交叉编码器对 RRF 融合结果重新打分（改进 5）。

原理：RRF 只看排名，不管实际匹配质量。Reranker 逐条计算
查询和文档的语义相关性分数（0~1），按分数重新排序。

推荐模型：bge-reranker-v2-m3（Ollama 可运行，免费）
配置方式：
  RERANK_MODEL=bge-reranker-v2-m3
  RERANK_BASE_URL=http://localhost:11434/v1

不配置则自动退化为 RRF 排序（不改变结果顺序）。
"""
from __future__ import annotations

from core.config import get
from rag.models import SearchHit


class Reranker:
    def __init__(self, enabled: bool = True):
        self.enabled_flag = enabled and bool(get("RERANK_BASE_URL") and get("RERANK_MODEL"))
        self._model = None

    def enabled(self) -> bool:
        return self.enabled_flag

    def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        """对搜索结果重新排序。如果 Reranker 不可用，返回原顺序。"""
        if not self.enabled_flag or not hits:
            return hits

        texts = [h.snippet for h in hits]
        try:
            scores = self._score(query, texts)
            # 按分数降序排列
            paired = sorted(zip(scores, hits), key=lambda x: -x[0])
            return [h for _, h in paired]
        except Exception:
            return hits

    def _score(self, query: str, texts: list[str]) -> list[float]:
        """调用 reranker API 获取相关性分数。"""
        import openai

        client = openai.OpenAI(
            base_url=get("RERANK_BASE_URL"),
            api_key=get("RERANK_API_KEY") or "ollama",
        )
        model = get("RERANK_MODEL")
        # 有些 reranker API 支持批量传入
        try:
            resp = client.embeddings.create(
                model=model,
                input=texts,
                query=query,
            )
            # 某些 API 返回 scores 字段
            if hasattr(resp, 'data') and len(resp.data) > 0 and hasattr(resp.data[0], 'score'):
                return [d.score for d in resp.data]
        except Exception:
            pass

        # 降级：逐条评分
        scores = []
        for text in texts:
            try:
                resp = client.embeddings.create(
                    model=model,
                    input=[text, query],
                )
                # 计算余弦相似度替代（如果 API 不支持直接打分）
                import numpy as np
                v1 = np.array(resp.data[0].embedding)
                v2 = np.array(resp.data[1].embedding)
                v1 = v1 / (np.linalg.norm(v1) + 1e-10)
                v2 = v2 / (np.linalg.norm(v2) + 1e-10)
                scores.append(float(np.dot(v1, v2)))
            except Exception:
                scores.append(0.0)
        return scores