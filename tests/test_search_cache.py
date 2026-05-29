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


if __name__ == "__main__":
    unittest.main()
