"""web_search 工具：让 AI 能自己上网搜索文档/Stack Overflow。

设计来源：用 DuckDuckGo 的 HTML 接口做免费搜索，不需要 API key。
用已有的 requests + BeautifulSoup 实现，不引入新依赖。
"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from tools import register_tool

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for recent information, documentation, or solutions to technical problems. "
                       "Uses DuckDuckGo (free, no API key needed). "
                       "When you're unsure about an API, a library's behavior, or need the latest information, "
                       "search the web rather than relying on training data which may be outdated. "
                       "Returns result titles, URLs, and brief snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，如 'python os.path 用法'"},
                "num_results": {"type": "integer", "description": "返回结果数，默认 5"},
            },
            "required": ["query"],
        },
    },
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def search(query: str, num_results: int = 5) -> str:
    """用 DuckDuckGo HTML 搜索，返回结果摘要。"""
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": _UA},
            timeout=15,
        )
    except requests.RequestException as e:
        return f"web_search 失败: {type(e).__name__}: {e}"

    if resp.status_code != 200:
        return f"web_search 失败: HTTP {resp.status_code}"

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for a in soup.select(".result__a")[:num_results]:
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if href:
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                from urllib.parse import unquote
                href = unquote(m.group(1))
        snippet_el = a.find_next("a", class_="result__snippet")
        snippet = snippet_el.get_text(strip=True)[:200] if snippet_el else ""
        results.append(f"{title}\n  {href}\n  {snippet}")

    if not results:
        return "web_search 未找到结果（可换关键词再试）"

    return f"搜索结果（{query}）：\n" + "\n---\n".join(results)


def _handle_web_search(ctx, args: dict) -> str:
    return search(args.get("query", ""), args.get("num_results") or 5)


register_tool(WEB_SEARCH_SCHEMA, _handle_web_search)