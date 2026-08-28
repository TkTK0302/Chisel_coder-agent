"""perception/web 的离线单测（mock requests，不发真实网络请求）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import perception.web as web


class FakeResp:
    status_code = 200
    text = (
        "<html><body><h1>Python 文档</h1>"
        "<p>这里介绍了 <code>os.path</code> 用法。</p>"
        "<a href='https://docs.python.org/3/library/os.html'>官网</a>"
        "<script>alert(1)</script>"
        "<style>.x{}</style></body></html>"
    )


def test_fetch_extracts_text_and_links(monkeypatch):
    calls = {}

    def fake_get(url, timeout=15, headers=None):
        calls["url"] = url
        return FakeResp()

    monkeypatch.setattr(web.requests, "get", fake_get)
    r = web.fetch("https://example.com/doc", max_chars=2000)
    assert "Python 文档" in r
    assert "os.path" in r
    assert "docs.python.org" in r  # 链接被收集
    assert calls["url"] == "https://example.com/doc"


def test_fetch_strips_script_style():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(FakeResp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    assert "alert" not in text
    assert "os.path" in text


def test_fetch_http_error(monkeypatch):
    class ErrResp:
        status_code = 404
        text = ""

    monkeypatch.setattr(web.requests, "get", lambda *a, **k: ErrResp())
    r = web.fetch("https://example.com/nope")
    assert "404" in r


def test_fetch_network_error(monkeypatch):
    import requests as real_requests

    def boom(*a, **k):
        raise real_requests.ConnectionError("boom")

    monkeypatch.setattr(web.requests, "get", boom)
    r = web.fetch("https://example.com")
    assert "web_fetch 失败" in r
