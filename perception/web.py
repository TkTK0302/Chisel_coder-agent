"""web_fetch 工具：抓取网页并抽取正文/链接，供 agent 查阅文档/API。

设计来源：浏览器工具的轻量版 —— 不引入 playwright，用 requests + BeautifulSoup
抽取正文与链接。本地服务预览可配合 bash curl + read_file 完成。
"""
from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from tools import register_tool

WEB_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "Fetch a web page and extract its readable text content and links. "
                       "Use this to look up documentation, API references, or examples. "
                       "Extracts the main body text, stripping scripts, styles, and navigation. "
                       "Returns up to 8000 characters of text, along with up to 10 page links. "
                       "For web search, use the web_search tool instead.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的完整 URL"},
                "max_chars": {"type": "integer", "description": "正文返回的最大字符数，默认 8000"},
            },
            "required": ["url"],
        },
    },
}


def fetch(url: str, max_chars: int = 8000) -> str:
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
    except requests.RequestException as e:
        return f"web_fetch 失败: {type(e).__name__}: {e}"
    if resp.status_code != 200:
        return f"web_fetch 失败: HTTP {resp.status_code}（{url}）"
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        return f"web_fetch 解析失败: {e}"
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and len(links) < 10:
            links.append(href)
    out = text[: max(1000, max_chars)]
    if links:
        out += "\n\n[页面链接]\n" + "\n".join(links)
    if not text:
        out = "(页面无可见文本，可能是 JS 渲染页；可换 API 或另找文档)"
    return out


def _handle_web_fetch(ctx, args: dict) -> str:
    return fetch(args.get("url", ""), args.get("max_chars") or 8000)


register_tool(WEB_SCHEMA, _handle_web_fetch)
