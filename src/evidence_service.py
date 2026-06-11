"""EvidenceService — 系统里唯一碰外部世界的缺口补证据模块(design-v3-draft M3)。

职责一句话:接缺口请求 → 返回证据;不下结论。失败策略:内部消化(降级/返空)。

收编(v3 根因 1:analyzer 越界采集,实测占其一半耗时):
- `analyzer_augment._gap_targeted_recollect` 定向外搜(原样迁入)
- `analyzer_augment._recollect_pricing_official` 官网定价页渲染(原样迁入)
- `analyzer_augment._run_survey` 系合成访谈采集(原样迁入)
- `evidence_gaps.recall_from_pool` 池内回捞(M1,fill 的第一优先级)

对外接口:
- `fill(evidence, meta, gaps, focus, round_idx=0)` → (rows, mode)
  缺口驱动补证据:先回捞池内(零成本)→ 不足再定向外搜(含文本级去重)。
  mode: "pool"(rows 已在池内,勿追加) / "search"(新证据,需追加) / "none"(补不到)。
- 合成访谈三件套 `_survey_should_run` / `_run_survey` / `_real_ugc_count`

Analyst(analyzer*)只留缺口声明,不再持有任何采集执行逻辑;
analyzer_augment 对本模块 re-export 保 back-compat(测试/旧 callsite 零改动)。
collector 节点的全量采集(collect(contract))在 M4 收编进来。
"""
from __future__ import annotations

import os
from concurrent.futures import as_completed
from typing import Optional

from .analyzer_common import _target_products
from .evidence_gaps import pool_recall_enabled, recall_from_pool
from .progress import CtxThreadPoolExecutor


# ────────────────────────────────────────────────────────────────────────────
# 统一入口:缺口 → 证据(回捞优先,外搜兜底)
# ────────────────────────────────────────────────────────────────────────────

def _norm_text(e: dict) -> str:
    from .analyzer_common import _norm_tokens
    return " ".join(sorted(_norm_tokens(
        (e.get("extracted_snippet") or e.get("claim") or ""))))


def fill(evidence: list[dict], meta: dict, gaps: dict, focus: str,
         round_idx: int = 0) -> tuple[list[dict], str]:
    """缺口驱动补证据。回捞优先(治假性缺口,零搜索成本),捞不到才外搜。

    外搜结果做双重去重(evidence_id + 归一化文本):SPA 页重抓时 chunk 顺序漂移
    会让同内容拿到新 ID,纯 id 去重拦不住,重复内容会虚增"新证据"并触发
    无意义的 section 重算(实测一轮虚增 32 条)。
    """
    if pool_recall_enabled():
        recalled = recall_from_pool(evidence, gaps)
        if recalled:
            return recalled, "pool"
    try:
        new_ev = _gap_targeted_recollect(meta, gaps, focus, round_idx=round_idx)
    except Exception as e:  # noqa: BLE001
        print(f"[evidence_service] gap refill 失败(忽略): {e}")
        new_ev = []
    existing_ids = {e.get("evidence_id") for e in evidence}
    existing_txt = {_norm_text(e) for e in evidence}
    new_ev = [e for e in new_ev
              if e.get("evidence_id") not in existing_ids
              and _norm_text(e) not in existing_txt]
    return (new_ev, "search") if new_ev else ([], "none")


# ────────────────────────────────────────────────────────────────────────────
# 定向补采执行(自 analyzer_augment 原样迁入,M3)
# ────────────────────────────────────────────────────────────────────────────

def _recollect_pricing_official(products: list[str], focus: str) -> list[dict]:
    """定价缺口治本:对配了 pricing_pages/official_pages 的产品,直接走官网定价页 +
    Playwright 渲染(SPA 档位价的权威出处),比 web_search 命中准、能拿到真实档位。
    未配 URL 的产品由调用方的 web_search 兜底。"""
    if not products:
        return []
    try:
        from .collector import OfficialPageAdapter
    except Exception:  # noqa: BLE001
        return []
    official = OfficialPageAdapter()
    out: list[dict] = []
    for product in products:
        if not official.can_fetch(product):
            continue
        try:
            evs = official.fetch(product, focus)
            priced = [e for e in evs if e.get("claim_type") == "pricing"]
            out.extend(priced)
            print(f"[analyzer] 官网定价补采 '{product}': {len(priced)} 条 pricing 证据")
        except Exception as e:  # noqa: BLE001
            print(f"[analyzer] 官网定价补采 '{product}' failed: {type(e).__name__}: {e}")
    return out


def _gap_targeted_recollect(meta: dict, gaps: dict, focus: str, round_idx: int = 0) -> list[dict]:
    """对缺口做定向搜索:每个空缺 (产品×功能) 一条查询 + 每个产品对缺失 claim_type 一条查询 +
    per-product 定价缺口走官网渲染。比盲目全量补采更省额度、命中更准。

    query/site 构造复用 source_planner 的语言一致构造器 + 站点锚定,杜绝旧版
    `{英文产品} {中文功能} {中文焦点}` 中英混搭 + site="" 裸搜捞同名页/学术站的问题。
    round_idx≥1(多轮升级):提高 results_per_query 并放开站点锚定(全网兜底),扩大召回。"""
    from collections import defaultdict

    from . import search, source_planner as sp

    added: list[dict] = []
    # 定价缺口走官网渲染(不依赖搜索额度,SPA 档位价最权威),两种触发:
    #   - per-product 缺口(有的产品有价、有的没);
    #   - 全表无价(pricing 整类缺失;per-product 因 any_priced=False 不触发,这里兜住——治"AI编程定价全空")。
    pricing_gap_products = gaps.get("pricing_gap_products") or []
    pricing_targets = list(pricing_gap_products)
    if "pricing" in (gaps.get("missing_claim_types") or []):
        for p in _target_products(meta):
            if p not in pricing_targets:
                pricing_targets.append(p)
    if pricing_targets:
        added.extend(_recollect_pricing_official(pricing_targets, focus))

    if not search.tavily_available():
        return added
    domain = os.environ.get("DOMAIN", "").strip()
    cat_en, cat_cn = sp._domain_category(domain)
    by_ct = sp.load_sources_config().get("by_claim_type") or {}
    max_cells = int(os.environ.get("ANALYZER_GAP_MAX_QUERIES", "10"))
    max_sites = int(os.environ.get("ANALYZER_GAP_MAX_SITES", "2"))
    # 多轮升级:第 2 轮起放开站点锚定 + 多取结果,把第一轮没补到的缺口用更宽的网捞
    widen = round_idx >= 1
    rpq = 3 if round_idx == 0 else 5
    plans: dict[str, list[dict]] = defaultdict(list)

    def _emit(product: str, ct: str, focus_kw: str) -> None:
        """按 claim_type 锚定权威源各发一条;widen/无 site 时给一条全网兜底(相关性门兜底过滤)。"""
        base_q = sp._build_query(product, focus_kw, ct, cat_en, cat_cn)
        sites = [] if widen else sp._sites_for_claim(product, ct, by_ct)[:max_sites]
        if sites:
            for site, st, bias in sites:
                plans[product].append({
                    "query": base_q, "claim_type": ct, "site": site,
                    "source_type": st, "bias": bias,
                })
        else:
            plans[product].append({
                "query": base_q, "claim_type": ct, "site": "",
                "source_type": "web_search",
                "bias": "vendor_claim" if ct in ("pricing", "feature_existence") else "third_party",
            })

    # 空白格 (产品×功能,support_status=unknown):缺的是「该产品到底有没有这个能力」=feature_existence,
    # 官网/文档是权威出处(和定价同理)。旧版搜 performance_quality(UGC质量)填不了「—」格,导致矩阵塌陷。
    # 同时补一条质量搜索:若该能力确实存在,顺带捞 UGC 评价(命中则连质量分一起补上)。
    # **用英文别名检索**:维度名是中文,官网/文档是英文,中文名搜命中低;spine 给的 name_en 命中更准。
    dim_en = gaps.get("dim_en") or {}
    for product, fname in gaps["unknown_cells"][:max_cells]:
        kw = dim_en.get(fname) or fname or focus
        _emit(product, "feature_existence", kw)
        _emit(product, "performance_quality", kw)
    # 整类缺失:每个产品对缺失 claim_type 各补一条(焦点回退到分析焦点)
    for product in _target_products(meta):
        for ct in gaps["missing_claim_types"]:
            _emit(product, ct, focus)
    # per-product 定价缺口:除官网外,再补一条 pricing 搜索(覆盖未配官网 URL 的产品)
    for product in pricing_gap_products:
        _emit(product, "pricing", focus)

    for product, plan in plans.items():
        try:
            evs, _ = search.search_plan_to_evidence(product, plan, results_per_query=rpq)
            added.extend(evs)
        except Exception as e:  # noqa: BLE001
            print(f"[analyzer] gap recollect '{product}' failed: {type(e).__name__}: {e}")
    return added


# ────────────────────────────────────────────────────────────────────────────
# 合成访谈采集(自 analyzer_augment 原样迁入,M3)
# ────────────────────────────────────────────────────────────────────────────

def _survey_enabled() -> bool:
    return os.environ.get("ANALYZER_SURVEY", "1").strip() not in ("0", "false", "False")


def _real_ugc_count(evidence: list[dict]) -> int:
    """真实用户侧证据数(非合成):reddit/hn/v2ex/UGC 搜索。"""
    return sum(
        1 for e in evidence
        if e.get("source_bias") == "user_generated"
        and not str(e.get("source_url") or "").startswith("synthetic")
    )


def _survey_should_run(evidence: list[dict], meta: dict) -> bool:
    """合成问卷只作兜底:真实 UGC 充足时不跑(省时 + 避免合成数据污染结论)。
    SURVEY_MIN_REAL_UGC(默认 8)条真实用户证据以上即跳过;不足则用合成兜底(已标注)。"""
    if not _survey_enabled():
        return False
    threshold = int(os.environ.get("SURVEY_MIN_REAL_UGC", "8"))
    real = _real_ugc_count(evidence)
    if real >= threshold:
        print(f"[analyzer] survey 跳过:已有 {real} 条真实 UGC(≥{threshold}),不用合成兜底")
        return False
    return True


def _run_survey(evidence: list[dict], meta: dict) -> tuple[list[dict], Optional[dict]]:
    """问卷/用户访谈采集 Agent(合成,已标注):对每个产品设计问卷+模拟访谈→证据。
    在 analyzer 阶段触发(不受采集超时限制、默认全档生效)。返回 (合并 evidence, research_method)。"""
    from .survey_skill import SurveySkill
    sk = SurveySkill()
    products = _target_products(meta)
    focus = (meta.get("analysis_focus") or [""])[0] if meta.get("analysis_focus") else ""
    existing = {e.get("evidence_id") for e in evidence}
    added: list[dict] = []
    questions: list[dict] = []
    personas: set[str] = set()
    with CtxThreadPoolExecutor(max_workers=max(1, len(products))) as ex:
        futs = {ex.submit(sk.execute, [], product=p, focus=focus): p for p in products}
        for fut in as_completed(futs):
            try:
                evs, m = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"[analyzer] survey '{futs[fut]}' failed: {type(e).__name__}: {e}")
                continue
            if not questions and m.get("questionnaire"):
                questions = m["questionnaire"]
            for e in evs:
                eid = e.get("evidence_id")
                if eid and eid not in existing:
                    existing.add(eid)
                    added.append(e)
                    persona = (e.get("metadata") or {}).get("persona")
                    if persona:
                        personas.add(persona)
    if not added:
        return evidence, None
    # 访谈回答原文:从合成证据里还原 persona/问题/反馈/期望,供报告「调研方法」卡逐条展示
    findings_list: list[dict] = []
    for e in added:
        md = e.get("metadata") or {}
        finding_text = (e.get("claim") or "").replace("[模拟访谈]", "").strip()
        if not finding_text:
            continue
        findings_list.append({
            "product": e.get("product") or "",
            "persona": md.get("persona") or "匿名受访者",
            "question_id": md.get("question_id") or "",
            "claim_type": e.get("claim_type") or "",
            "finding": finding_text,
            "expectation": md.get("expectation") or "",
            "evidence_id": e.get("evidence_id") or "",
        })
    research_method = {
        "method": "LLM 模拟问卷调研 + 用户访谈(合成数据,非真实用户,已脱敏)",
        "synthetic": True,
        "questions": [{"id": q.get("id"), "text": q.get("text")} for q in questions if q.get("text")][:6],
        "n_findings": len(added),
        "personas": sorted(personas)[:8],
        "findings": findings_list[:16],  # 控量:逐条访谈回答,前端可展开
    }
    return evidence + added, research_method
