"""Collector 节点编排 — 三层 DAG 顶层。

collector_node + 验收门自愈(acceptance_gate_and_heal / _targeted_refill)。
从 collector_common / collector_adapters re-export 全部公共名,保证 `from src.collector import X`
的历史 back-compat 与既有 callsite/测试零变化。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional
from .state import AgentState
# ── re-export:基座 helper/常量/进度(下划线名 star 不带,显式列出)─────────────
from .collector_common import (  # noqa: F401
    FRESHNESS_TTL_DAYS,
    MAX_EVIDENCE_PER_PRODUCT,
    REQUIRED_CLAIM_TYPES,
    RUNTIME_PROFILES,
    _emit_progress,
    _reclassify_official_claim_types,
    cap_evidence_per_product,
    classify_claim_types_llm,
    dedupe_evidence,
    discover_all_urls,
    discover_urls,
    dump_evidence_debug,
    generate_evidence_id,
    infer_claim_type,
    patch_by_requirements,
    reset_debug_file,
    runtime_settings,
    set_progress_callback,
)
# ── re-export:适配器与注册表 ──────────────────────────────────────────────
from .collector_adapters import (  # noqa: F401
    AdapterRegistry,
    CacheAdapter,
    MockAdapter,
    OfficialPageAdapter,
    SearchAdapter,
    SourceAdapter,
    get_registry,
    reset_registry,
)


# ────────────────────────────────────────────────────────────────────────────
# 自愈循环：验收门 Gap → 定向补采（§3.6 D）
# ────────────────────────────────────────────────────────────────────────────

def _targeted_refill(gaps: list[dict], focus: str, max_per_gap: int = 4) -> list[dict]:
    """质量/数量 Gap → 定向检索补采。复用 search.search_plan_to_evidence(自带相关性门+缓存)。
    每个 gap 按其 fix 处方(query_hint/source_hint/bias)定向搜,只补缺口,不全量重抓。"""
    from . import search
    if not search.search_available():
        print("  [self-heal] 无可用搜索供应商,跳过补采")
        return []
    patches: list[dict] = []
    seen: set = set()
    for g in gaps:
        p = g.get("product")
        ct = g.get("claim_type") or "feature_existence"
        fix = g.get("fix") or {}
        sites = fix.get("source_hint") or [""]
        qhint = fix.get("query_hint") or f"{p} {ct}"
        bias = fix.get("bias") or ("user_generated" if ct in ("user_pain", "performance_quality") else "vendor_claim")
        plan = []
        for s in sites:
            # source_hint 里的 official_page/pricing_page 是「去官网」信号 → site 留空走官网锚定
            st = "pricing_page" if ct == "pricing" else (
                "official_page" if g.get("gap_type") == "no_official" else "web_search")
            site = "" if s in ("official_page", "pricing_page") else s
            plan.append({"query": qhint, "claim_type": ct, "site": site,
                         "source_type": st, "bias": bias})
        try:
            evs, _ev = search.search_plan_to_evidence(p, plan, results_per_query=max_per_gap)
        except Exception as e:  # noqa: BLE001
            print(f"  [self-heal] refill {p}/{ct} 失败: {type(e).__name__}: {e}")
            continue
        for e in evs:
            if e.get("evidence_id") not in seen:
                seen.add(e.get("evidence_id"))
                e["collection_source"] = "self_heal"
                patches.append(e)
    return patches


def acceptance_gate_and_heal(merged: list[dict], meta: dict, focus: str,
                             cap_limit: int, max_rounds: int = 1) -> tuple[list[dict], dict]:
    """采集验收门 + 自愈循环：审查(数量+质量门)→有 Gap 则定向补采→再审查，至多 max_rounds 轮。
    达标或补不到即停；仍未闭合的 Gap 留在 audit['gaps'] 供 Reviewer 安全网/诚实标注。"""
    from .quality import annotate_and_audit, score_quality
    audit = annotate_and_audit(merged, meta)
    for rnd in range(max_rounds):
        if audit["passed"]:
            break
        gaps = audit["gaps"]
        print(f"  [self-heal] round {rnd+1}: {len(gaps)} 个 Gap → 定向补采 "
              f"{[g['gap_type']+':'+str(g.get('product')) for g in gaps]}")
        patch = _targeted_refill(gaps, focus)
        if not patch:
            print("  [self-heal] 无新证据补入，停止")
            break
        for e in patch:
            e["quality_score"] = score_quality(e)
        before = len(gaps)
        merged = cap_evidence_per_product(dedupe_evidence(merged + patch), limit=cap_limit)
        audit = annotate_and_audit(merged, meta)
        print(f"  [self-heal] round {rnd+1} 后: Gap {before} → {len(audit['gaps'])}, +{len(patch)} 证据")
    return merged, audit


# ────────────────────────────────────────────────────────────────────────────
# Node
# ────────────────────────────────────────────────────────────────────────────

_collector_run_count = 0


def collector_node(state: AgentState) -> AgentState:
    """v2.2.1: 多产品并发 + wall-clock timeout 兜底 + URL Discovery"""
    global _collector_run_count
    _collector_run_count += 1
    run_id = _collector_run_count
    print(f"\n[collector_node] ====== RUN #{run_id} ======")

    meta = state["analysis_meta"]
    products = [meta["target_product"]] + list(meta["competitors"])
    focus = meta["analysis_focus"][0] if meta.get("analysis_focus") else ""
    runtime_profile = meta.get("runtime_profile") or "deep"
    settings = runtime_settings(runtime_profile)

    # Step 0: URL Discovery — 让 LLM 自主为每个产品找官网/定价页 URL。
    # 官网是最权威证据源(功能/定价),不能只靠 products.yaml 硬编码 → LLM 发现**默认开启**,
    # 任意新产品都让 LLM(带 web 搜索候选)找官网;mock/无 key/显式 URL_DISCOVERY_LLM=0 才退回配置或跳过。
    from .llm import is_mock_mode as _is_mock
    print(f"[collector] discovering URLs for {products} ...")
    _url_env = os.environ.get("URL_DISCOVERY_LLM", "").strip()
    allow_url_llm = (
        _url_env not in ("0", "false", "False")
        and not _is_mock()
        and bool(os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY"))
    )
    discovered = discover_all_urls(products, allow_llm=allow_url_llm)
    for p, info in discovered.items():
        src = info.get("source", "?")
        op = info.get("official_pages", [])
        pp = info.get("pricing_pages", [])
        print(f"  {p}: source={src}, official={len(op)}, pricing={len(pp)}")
        if op:
            for url in op:
                print(f"    official: {url}")
        if pp:
            for url in pp:
                print(f"    pricing:  {url}")

    # 把发现的官网域名注入 source_planner → 未配置产品的 feature/pricing 检索也锚定官网(与已配置拉平)
    try:
        from urllib.parse import urlparse
        from . import source_planner as _sp
        _disc_domains: dict[str, list[str]] = {}
        for p, info in discovered.items():
            doms: list[str] = []
            for u in (info.get("official_pages") or []) + (info.get("pricing_pages") or []):
                h = (urlparse(u).hostname or "").lower()
                h = h[4:] if h.startswith("www.") else h
                if h and h not in doms:
                    doms.append(h)
            if doms:
                _disc_domains[p] = doms
        _sp.set_discovered_domains(_disc_domains)
        if _disc_domains:
            print(f"[collector] 注入官网域名锚定: {_disc_domains}")
    except Exception as e:  # noqa: BLE001
        print(f"[collector] 注入官网域名失败(忽略): {type(e).__name__}: {e}")

    fetched: list[dict] = []
    collection_meta: dict = {
        "products": {},
        "discovered_urls": discovered,
        "runtime_profile": runtime_profile,
    }
    reset_registry()  # 每次运行重新创建，避免旧 discovered_urls 残留
    registry = get_registry(discovered_urls=discovered, runtime_profile=runtime_profile)
    print(f"[collector_node] registry.live_adapters={len(registry.live_adapters)}, products={products}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(registry.fetch_all, p, focus): p for p in products}
        timeout_sec = int(settings["timeout_sec"])
        done, not_done = wait(futures.keys(), timeout=timeout_sec)

        for fut in done:
            product = futures[fut]
            try:
                evs, meta_info = fut.result()
                print(f"  [collector_node] {product} future returned {len(evs)} evidence")
            except Exception as e:
                evs, meta_info = [], {
                    "adapter_events": [{"status": "fatal", "reason": str(e)}],
                    "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
                }
                print(f"  [collector_node] {product} future EXCEPTION: {type(e).__name__}: {e}")
            fetched.extend(evs)
            collection_meta["products"][product] = meta_info

        print(f"  [collector_node] done={len(done)}, not_done={len(not_done)}, fetched={len(fetched)}")

        # P0 修复:not_done 的 future 往往只差一两秒就完成(fetch_all 内部各自带超时,
        # 线程不会被 kill,ThreadPoolExecutor 退出时本就会阻塞等它们跑完)。原逻辑直接
        # cancel 丢弃 = 把每个产品已抓到的 50+ 条证据白白扔掉 → 整轮 0 产出,再靠
        # analyzer gap-refill 重抓一遍(延迟翻倍)。这里给一个宽限期收割已完成的结果,
        # 只把真正还卡死的标记为 timeout。grace 由 COLLECTOR_GRACE_SEC 调(默认 20s)。
        if not_done:
            grace = int(os.environ.get("COLLECTOR_GRACE_SEC", "20"))
            print(f"  [collector_node] {len(not_done)} 个产品超时未收割,宽限 {grace}s 收割已完成结果...")
            harvested, still_running = wait(not_done, timeout=grace)
            for fut in harvested:
                product = futures[fut]
                try:
                    evs, meta_info = fut.result()
                    print(f"  [collector_node] {product} 宽限收割 {len(evs)} evidence (超时后完成,免去重抓)")
                except Exception as e:  # noqa: BLE001
                    evs, meta_info = [], {
                        "adapter_events": [{"status": "fatal", "reason": str(e)}],
                        "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
                    }
                    print(f"  [collector_node] {product} 宽限收割异常: {type(e).__name__}: {e}")
                fetched.extend(evs)
                collection_meta["products"][product] = meta_info
            not_done = still_running

        for fut in not_done:
            product = futures[fut]
            fut.cancel()
            collection_meta["products"][product] = {
                "adapter_events": [
                    {"status": "timeout", "reason": f"wall-clock {timeout_sec}s exceeded"}
                ],
                "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES},
            }

    # 打回时:按 requirements 精准追加
    print(f"\n[collector_node] fetched={len(fetched)}, reject_requirements={state.get('reject_requirements')}")
    if state.get("reject_requirements"):
        merged = patch_by_requirements(
            existing=state.get("raw_evidence") or [],
            new=fetched,
            requirements=state["reject_requirements"],
        )
        print(f"[collector_node] after patch: {len(merged)}")
    else:
        merged = fetched

    merged = dedupe_evidence(merged)
    print(f"[collector_node] after dedupe: {len(merged)}")

    if os.environ.get("DUMP_FULL_EVIDENCE", "").strip() in ("1", "true", "True"):
        dump_evidence_debug(merged, run_id=run_id)

    # 质量加权截顶前先给每条打 quality_score(cap 按质量分优先保留高质证据)
    from .quality import score_quality as _score_quality
    for _e in merged:
        _e["quality_score"] = _score_quality(_e)
    cap_limit = int(settings["max_evidence_per_product"])
    merged = cap_evidence_per_product(merged, limit=cap_limit)

    # 官网证据 claim_type 批量 LLM 精分类(抓取已完成,不影响超时)
    _reclassify_official_claim_types(merged)
    dump_evidence_debug(merged, run_id=run_id)

    # ── 采集验收门 + 自愈循环（§3.5/§3.6）放在 official_check 之前 ──────────────
    # 数量门(覆盖/官网/总量) + 质量门(定价含金量/偏置平衡) → 有 Gap 则定向补采(≤1 轮),
    # 达标才把证据交给 Analyzer。Reviewer 退化为安全网。SELF_HEAL_ROUNDS=0 可关。
    heal_rounds = int(os.environ.get("SELF_HEAL_ROUNDS", "1")) if settings.get("search") else 0
    merged, quality_audit = acceptance_gate_and_heal(merged, meta, focus, cap_limit, max_rounds=heal_rounds)
    collection_meta["quality_audit"] = quality_audit
    print(f"[collector][验收门] avg_quality={quality_audit['avg_quality']}, "
          f"数量Gap={len(quality_audit['quantity_gaps'])}, 质量Gap={len(quality_audit['quality_gaps'])}, "
          f"{'✅达标' if quality_audit['passed'] else '⚠️仍有未闭合Gap(交安全网)'}")
    for g in quality_audit["gaps"]:
        print(f"  [Gap] {g['gap_type']} · {g['product']}/{g.get('claim_type')}: {g['reason']}")

    # 高质量源台账:把本轮高质量证据的 domain 学进 ledger,供下次同产品/品类复用(§8.6)。失败静默。
    try:
        from . import source_ledger
        _cat = meta.get("domain") or meta.get("domain_key") or os.environ.get("DOMAIN") or "default"
        source_ledger.record(merged, category=_cat, products=products)
    except Exception as _le:  # noqa: BLE001
        print(f"  [ledger] 记录失败(忽略): {type(_le).__name__}: {_le}")

    # ── 官网获取 check:官网是最权威源(功能/定价大量靠它),逐产品核对到底抓到没有,没抓到明确报红 ──
    official_check: dict = {}
    for product in products:
        n_off = sum(1 for e in merged
                    if e.get("product") == product and e.get("source_type") == "official_page")
        disc = discovered.get(product) or {}
        had_urls = bool(disc.get("official_pages") or disc.get("pricing_pages"))
        ok = n_off > 0
        official_check[product] = {
            "official_evidence": n_off, "had_urls": had_urls,
            "url_source": disc.get("source", "?"), "ok": ok,
        }
        flag = "[OK]" if ok else "[WARN]"
        print(f"[collector][官网check] {flag} {product}: 官网证据 {n_off} 条 "
              f"(URL来源={disc.get('source','?')}, 有URL={had_urls})")
        if not ok and had_urls:
            print(f"  [WARN] {product} 找到了官网 URL 却 0 条官网证据 -> 抓取/渲染失败,需排查")
        elif not ok:
            print(f"  [WARN] {product} 没找到官网 URL -> URL 发现失败(LLM/搜索没命中)")
    collection_meta["official_check"] = official_check
    n_missing = sum(1 for v in official_check.values() if not v["ok"])
    if n_missing:
        print(f"[collector][官网check] [WARN] {n_missing}/{len(products)} 个产品无官网证据,影响功能/定价权威性")

    return {
        **state,
        "raw_evidence": merged,
        "collection_meta": collection_meta,
        "reject_requirements": None,
        "reject_target": None,
    }
