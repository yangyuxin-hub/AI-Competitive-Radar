"""Tavily 磁盘缓存测试:roundtrip / TTL 过期 / 命中时不发 HTTP。"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from src import search


class TavilyCacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_dir = search._CACHE_DIR
        search._CACHE_DIR = Path(self._tmp)
        os.environ.pop("TAVILY_CACHE", None)
        os.environ.pop("TAVILY_CACHE_TTL_HOURS", None)

    def tearDown(self):
        search._CACHE_DIR = self._orig_dir

    def test_roundtrip(self):
        search._cache_set("q1", "", 5, [{"url": "u", "content": "c"}])
        got = search._cache_get("q1", "", 5)
        self.assertEqual(got, [{"url": "u", "content": "c"}])

    def test_key_includes_site_and_max(self):
        search._cache_set("q1", "reddit.com", 5, [{"x": 1}])
        self.assertIsNone(search._cache_get("q1", "", 5))  # site 不同 → miss
        self.assertIsNone(search._cache_get("q1", "reddit.com", 3))  # max 不同 → miss

    def test_ttl_expiry(self):
        path = search._cache_path("q2", "", 5)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": time.time() - 10 * 3600, "results": [{"a": 1}]}))
        os.environ["TAVILY_CACHE_TTL_HOURS"] = "1"  # 1h TTL,记录 10h 前 → 过期
        try:
            self.assertIsNone(search._cache_get("q2", "", 5))
        finally:
            del os.environ["TAVILY_CACHE_TTL_HOURS"]

    def test_disabled_returns_none(self):
        search._cache_set("q3", "", 5, [{"a": 1}])
        os.environ["TAVILY_CACHE"] = "0"
        try:
            self.assertIsNone(search._cache_get("q3", "", 5))
        finally:
            del os.environ["TAVILY_CACHE"]

    def test_search_uses_cache_without_http(self):
        # 预置缓存 + 设 key,tavily_search 应直接命中缓存,不触网
        os.environ["TAVILY_API_KEY"] = "test-key"
        try:
            search._cache_set("hot query", "", 5, [{"url": "cached", "content": "x"}])
            out = search.tavily_search("hot query", "", 5)
            self.assertEqual(out, [{"url": "cached", "content": "x"}])
        finally:
            del os.environ["TAVILY_API_KEY"]


class ProviderChainTest(unittest.TestCase):
    def setUp(self):
        for k in ("BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "SEARCH_PROVIDER", "DISABLE_DDG_SEARCH"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k in ("BRAVE_API_KEY", "TAVILY_API_KEY", "SEARCH_PROVIDER", "DISABLE_DDG_SEARCH"):
            os.environ.pop(k, None)

    def test_brave_first_then_tavily_then_ddg(self):
        os.environ["BRAVE_API_KEY"] = "b"
        os.environ["TAVILY_API_KEY"] = "t"
        chain = search._provider_chain()
        self.assertEqual(chain[0], "brave")
        self.assertIn("tavily", chain)
        self.assertEqual(chain[-1], "ddg")  # 免费兜底永远最后

    def test_explicit_order(self):
        os.environ["SEARCH_PROVIDER"] = "ddg,brave"
        self.assertEqual(search._provider_chain(), ["ddg", "brave"])

    def test_available_true_when_only_ddg(self):
        # 无任何 key,只要 ddgs 装了(本仓库已装)就可用
        self.assertTrue(search.search_available())

    def test_disable_ddg_removes_free_fallback(self):
        os.environ["DISABLE_DDG_SEARCH"] = "1"
        self.assertEqual(search._provider_chain(), [])
        self.assertFalse(search.search_available())


class WebSearchFallbackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_dir = search._CACHE_DIR
        search._CACHE_DIR = Path(self._tmp)
        self._orig_providers = dict(search._PROVIDERS)
        os.environ["SEARCH_PROVIDER"] = "brave,ddg"

    def tearDown(self):
        search._CACHE_DIR = self._orig_dir
        search._PROVIDERS.clear()
        search._PROVIDERS.update(self._orig_providers)
        os.environ.pop("SEARCH_PROVIDER", None)

    def test_falls_through_to_next_on_error(self):
        def _boom(q, s, n):
            raise RuntimeError("432 quota")

        def _ok(q, s, n):
            return [{"title": "t", "url": "u", "content": "c", "score": None}]

        search._PROVIDERS["brave"] = _boom
        search._PROVIDERS["ddg"] = _ok
        out = search.web_search("q", "", 3)
        self.assertEqual(out, [{"title": "t", "url": "u", "content": "c", "score": None}])

    def test_all_fail_returns_empty_not_cached(self):
        search._PROVIDERS["brave"] = lambda q, s, n: (_ for _ in ()).throw(RuntimeError("x"))
        search._PROVIDERS["ddg"] = lambda q, s, n: []
        self.assertEqual(search.web_search("q2", "", 3), [])
        # 空结果不缓存 → 文件不存在,下次可重试
        self.assertFalse(search._cache_path("q2", "", 3).exists())


if __name__ == "__main__":
    unittest.main()
