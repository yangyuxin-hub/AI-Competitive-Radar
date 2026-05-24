"""Collector 节点 — 见 docs/design-v2.2.md §五

骨架阶段:只实现 MockAdapter(从 data/sample_sources.json 读)。
真实 OfficialPageAdapter / PricingPageAdapter / RedditAdapter 留到阶段 D。
"""
from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Optional

from .state import AgentState


_ROOT = Path(__file__).resolve().parent.parent


def _resolve_sample_path() -> Path:
    """优先级: SAMPLE_SOURCES_PATH 环境变量 > config/domains.yaml[DOMAIN] > 默认 sample_sources.json"""
    env_path = os.environ.get("SAMPLE_SOURCES_PATH")
    if env_path:
        return Path(env_path)

    domain = os.environ.get("DOMAIN", "").strip()
    if domain:
        try:
            import yaml  # 延迟导入,避免 yaml 不装时也能跑默认
            with (_ROOT / "config" / "domains.yaml").open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            entry = (cfg.get("domains") or {}).get(domain)
            if entry and entry.get("sample_path"):
                return _ROOT / entry["sample_path"]
        except Exception as e:
            print(f"[collector] WARN: failed to resolve DOMAIN={domain}: {e}")
    return _ROOT / "data" / "sample_sources.json"

REQUIRED_CLAIM_TYPES = {
    "feature_existence",
    "performance_quality",
    "pricing",
    "user_pain",
}


# ────────────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────────────

def generate_evidence_id(product: str, source_url: str, claim: str) -> str:
    """S + sha1[:7] = 8 字符,见 design §4.1"""
    raw = f"{product}|{source_url}|{claim}".encode("utf-8")
    return "S" + hashlib.sha1(raw).hexdigest()[:7].upper()


def dedupe_evidence(evidences: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for ev in evidences:
        merged[ev["evidence_id"]] = ev
    return list(merged.values())


def patch_by_requirements(
    existing: list[dict],
    new: list[dict],
    requirements: list[dict],
) -> list[dict]:
    """打回时:基于结构化 requirements 精准追加证据,不重复塞旧的"""
    existing_ids = {e["evidence_id"] for e in existing}
    needed_types: set[str] = set()
    for r in requirements:
        if r.get("reject_target") != "collector":
            continue
        for ct in r.get("required_claim_types", []) or []:
            needed_types.add(ct)
    patches = [
        e for e in new
        if e["evidence_id"] not in existing_ids
        and e["claim_type"] in needed_types
    ]
    return existing + patches


# ────────────────────────────────────────────────────────────────────────────
# Adapter 接口与实现
# ────────────────────────────────────────────────────────────────────────────

class SourceAdapter(ABC):
    @abstractmethod
    def fetch(self, product: str, focus: str) -> list[dict]:
        ...

    @abstractmethod
    def can_fetch(self, product: str) -> bool:
        ...


class MockAdapter(SourceAdapter):
    """从 data/sample_sources.json 读取证据 — Demo 兜底"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _resolve_sample_path()
        self._cache: Optional[list[dict]] = None

    def _load(self) -> list[dict]:
        if self._cache is not None:
            return self._cache
        with self.path.open(encoding="utf-8") as f:
            self._cache = json.load(f).get("raw_evidence", [])
        return self._cache

    def can_fetch(self, product: str) -> bool:
        return self.path.exists()

    def fetch(self, product: str, focus: str) -> list[dict]:
        return [e for e in self._load() if e.get("product") == product]


class CacheAdapter(SourceAdapter):
    """骨架阶段的 stub:无持久化,can_fetch 永远返回 False。
    阶段 D 实装(merge 写入 + freshness 重新计算)。
    """

    def can_fetch(self, product: str) -> bool:
        return False

    def fetch(self, product: str, focus: str) -> list[dict]:
        return []

    def save(self, product: str, evidences: list[dict]) -> None:
        pass


# ────────────────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────────────────

class AdapterRegistry:
    """三层兜底:live → cache → mock。骨架阶段只挂 cache + mock(live 为空)"""

    def __init__(self) -> None:
        self.live_adapters: list[SourceAdapter] = []  # 阶段 D 填充
        self.cache = CacheAdapter()
        self.mock = MockAdapter()

    def fetch_all(self, product: str, focus: str) -> tuple[list[dict], dict]:
        all_evidences: list[dict] = []
        adapter_events: list[dict] = []

        # 第一层:实时(骨架阶段为空)
        for adapter in self.live_adapters:
            if not adapter.can_fetch(product):
                continue
            try:
                evs = adapter.fetch(product, focus)
                all_evidences.extend(evs)
                self.cache.save(product, evs)
                adapter_events.append({
                    "adapter": type(adapter).__name__,
                    "status": "success",
                    "count": len(evs),
                })
            except Exception as e:
                adapter_events.append({
                    "adapter": type(adapter).__name__,
                    "status": "failed",
                    "reason": str(e),
                    "fallback": "cache",
                })

        # 第二层:缓存
        missing = REQUIRED_CLAIM_TYPES - {e["claim_type"] for e in all_evidences}
        if missing and self.cache.can_fetch(product):
            cached = self.cache.fetch(product, focus)
            all_evidences.extend(e for e in cached if e["claim_type"] in missing)
            adapter_events.append({"adapter": "CacheAdapter", "status": "patched"})

        # 第三层:Mock
        still_missing = REQUIRED_CLAIM_TYPES - {e["claim_type"] for e in all_evidences}
        if still_missing and self.mock.can_fetch(product):
            mock_evs = self.mock.fetch(product, focus)
            all_evidences.extend(e for e in mock_evs if e["claim_type"] in still_missing)
            adapter_events.append({
                "adapter": "MockAdapter",
                "status": "fallback",
                "filled_types": sorted(still_missing),
            })

        all_evidences = dedupe_evidence(all_evidences)
        coverage = {
            ct: sum(1 for e in all_evidences if e["claim_type"] == ct)
            for ct in REQUIRED_CLAIM_TYPES
        }
        return all_evidences, {
            "adapter_events": adapter_events,
            "coverage": coverage,
        }


_registry: Optional[AdapterRegistry] = None


def get_registry() -> AdapterRegistry:
    global _registry
    if _registry is None:
        _registry = AdapterRegistry()
    return _registry


# ────────────────────────────────────────────────────────────────────────────
# Node
# ────────────────────────────────────────────────────────────────────────────

def collector_node(state: AgentState) -> AgentState:
    """v2.2.1: 多产品并发 + wall-clock timeout 兜底"""
    meta = state["analysis_meta"]
    products = [meta["target_product"]] + list(meta["competitors"])
    focus = meta["analysis_focus"][0] if meta.get("analysis_focus") else ""

    fetched: list[dict] = []
    collection_meta: dict = {"products": {}}
    registry = get_registry()

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(registry.fetch_all, p, focus): p for p in products}
        done, not_done = wait(futures.keys(), timeout=25)

        for fut in done:
            product = futures[fut]
            try:
                evs, meta_info = fut.result()
            except Exception as e:
                evs, meta_info = [], {
                    "adapter_events": [{"status": "fatal", "reason": str(e)}],
                    "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
                }
            fetched.extend(evs)
            collection_meta["products"][product] = meta_info

        for fut in not_done:
            product = futures[fut]
            fut.cancel()
            collection_meta["products"][product] = {
                "adapter_events": [
                    {"status": "timeout", "reason": "wall-clock 25s exceeded"}
                ],
                "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
            }

    # 打回时:按 requirements 精准追加
    if state.get("reject_requirements"):
        merged = patch_by_requirements(
            existing=state.get("raw_evidence") or [],
            new=fetched,
            requirements=state["reject_requirements"],
        )
    else:
        merged = fetched

    merged = dedupe_evidence(merged)

    return {
        **state,
        "raw_evidence": merged,
        "collection_meta": collection_meta,
        "reject_requirements": None,
        "reject_target": None,
    }
