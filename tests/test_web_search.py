"""perception/web_search 的离线单测（mock 网络请求不发送真实请求）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_search_returns_formatted_results(monkeypatch):
    from perception import web_search

    class FakeResp:
        status_code = 200
        text = (
            '<html><body>'
            '<a class="result__a" href="uddg=https%3A%2F%2Fdocs.python.org%2F3%2F">Python docs</a>'
            '<a class="result__snippet">Official Python documentation</a>'
            '</body></html>'
        )

    calls = []
    def fake_post(url, data=None, headers=None, timeout=15):
        calls.append((url, data))
        return FakeResp()

    monkeypatch.setattr(web_search.requests, "post", fake_post)
    r = web_search.search("python", num_results=3)
    assert "Python docs" in r
    assert "docs.python.org" in r
    assert calls


def test_search_http_error(monkeypatch):
    from perception import web_search

    class ErrResp:
        status_code = 429
        text = ""

    monkeypatch.setattr(web_search.requests, "post", lambda *a, **k: ErrResp())
    r = web_search.search("python")
    assert "429" in r


def test_search_network_error(monkeypatch):
    from perception import web_search
    import requests as real_requests

    def boom(*a, **k):
        raise real_requests.ConnectionError("no network")

    monkeypatch.setattr(web_search.requests, "post", boom)
    r = web_search.search("python")
    assert "web_search 失败" in r