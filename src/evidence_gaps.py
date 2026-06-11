"""统一缺口判定口径 + 池内回捞 — design-v3-draft M1。

此前"缺口判定"长在两处,口径不保证一致(v3 根因 1):
- collector 验收门: `quality.audit_coverage`(+三个质量门) → list[raw gap dict]
- analyzer gap refill: `analyzer_augment._coverage_gaps`(facts 感知) → 专用 dict

本模块是缺口判定的**唯一入口**:
- `find_gaps(evidence, meta, facts=None)` → list[stage_report.Gap]
  = 两套旧口径的**并集**,统一归一化为 Gap(owner_node/task_key 确定性派生),按 task_key+gap_type 去重。
- `recall_from_pool(evidence, gaps)` → 外搜前先**回捞池内**被 top-K(8/5 条)/片段截断(180/140 字符)
  挡在 prompt 视野外的证据(零搜索成本)。治假性缺口:R6 报"证据不足"的一部分证据
  明明在 raw_evidence 里,只是没喂给 LLM。

阈值/检测逻辑仍留在原模块(quality.py / analyzer_augment.py),本模块只做组合+归一——
M3 EvidenceService 收编采集执行逻辑时,本模块随之迁入并吸收阈值表(渐进搬家,不重写)。
"""
from __future__ import annotations

import os
from typing import Optional

from .stage_report import Gap, _to_gap

# 回捞过的证据在 _compact_evidence 里的加长片段(默认截断会把价格数字/功能细节切掉)
RECALL_SNIPPET_LEN = int(os.environ.get("ANALYZER_RECALL_SNIPPET_LEN", "400"))


def pool_recall_enabled() -> bool:
    """回退开关(最优即默认:开)。ANALYZER_POOL_RECALL=0 退回纯外搜旧行为。"""
    return os.environ.get("ANALYZER_POOL_RECALL", "1").strip() not in ("0", "false", "False")


# ────────────────────────────────────────────────────────────────────────────
# 统一口径:两套旧判定 → list[Gap]
# ────────────────────────────────────────────────────────────────────────────

def _facts_gaps_to_raw(gaps: dict) -> list[dict]:
    """analyzer_augment._coverage_gaps 的专用 dict → 原始 gap dict 列表(供 _to_gap 归一)。"""
    raw: list[dict] = []
    dim_en = gaps.get("dim_en") or {}
    by_product: dict[str, list[str]] = {}
    for p, dim in gaps.get("unknown_cells") or []:
        by_product.setdefault(p, []).append(dim)
    for p, dims in by_product.items():
        raw.append({
            "product": p, "claim_type": "feature_existence",
            "gap_type": "unknown_cells",
            "reason": f"{p} 有 {len(dims)} 个功能格 support_status=unknown",
            "fix": {"strategy": "recollect", "dims": dims,
                    "query_hint": " / ".join(dim_en.get(d) or d for d in dims[:5])},
        })
    stale = set(gaps.get("stale_gap_claim_types") or [])
    for ct in gaps.get("missing_claim_types") or []:
        raw.append({
            "product": None, "claim_type": ct,
            "gap_type": "stale_claim_type" if ct in stale else "missing_claim_type",
            "reason": f"必需证据类型 {ct} " + ("全部过期" if ct in stale else "缺失"),
            "fix": {"strategy": "recollect"},
        })
    for p in gaps.get("pricing_gap_products") or []:
        raw.append({
            "product": p, "claim_type": "pricing",
            "gap_type": "pricing_extraction_missing",
            "reason": f"{p} 在 pricing_model 无档位而别家有价",
            "fix": {"strategy": "recollect", "ladder": "L3_render_official"},
        })
    return raw


def find_gaps(evidence: list[dict], meta: dict, facts: Optional[dict] = None,
              audit: Optional[dict] = None) -> list[Gap]:
    """缺口判定唯一入口:证据级(collector 口径)∪ facts 级(analyzer 口径,facts 提供时)。

    audit 可传入已算好的 quality.annotate_and_audit 结果,避免验收门里重复打分。
    返回按 (task_key, gap_type) 去重的 Gap 列表(owner_node/fixable/task_key 已派生)。
    """
    if audit is None:
        from .quality import annotate_and_audit
        audit = annotate_and_audit(evidence, meta)
    raw: list[dict] = list(audit.get("gaps") or [])
    if facts is not None:
        from .analyzer_augment import _coverage_gaps
        raw += _facts_gaps_to_raw(_coverage_gaps(facts, meta, evidence))
    out: list[Gap] = []
    seen: set[tuple] = set()
    for r in raw:
        g = _to_gap(r)
        key = (g["task_key"], g["gap_type"])
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
    return out


# ────────────────────────────────────────────────────────────────────────────
# 池内回捞:外搜前先把"明明在池里、只是没进 prompt 视野"的证据捞回来
# ────────────────────────────────────────────────────────────────────────────

def _texts(e: dict) -> str:
    return f"{e.get('claim') or ''} {e.get('extracted_snippet') or ''}".lower()


def recall_from_pool(evidence: list[dict], gaps: dict) -> list[dict]:
    """按 analyzer 缺口(legacy dict 形状,与 _gap_targeted_recollect 同输入)回捞池内证据。

    命中即在原行打 `_recalled=True` 标记(就地),由 analyzer_common._compact_evidence 识别:
    排序加权进 top-K + 片段放宽截断。每行只回捞一次(已标记的不再返回),保证循环收敛。

    回捞范围(只捞确实可能被视野挡掉的):
    - unknown_cells(产品×功能格):该产品 feature/quality 证据中文本提到该维度名(中/英)的行
    - pricing_gap_products:该产品池内 pricing 证据(默认 180 字符截断常把档位价切掉)
    - missing_claim_types 不回捞:整类缺失=池内本来就没有,只能外搜
      (stale 触发的视为时效缺口,回捞旧证据无意义,同样外搜)
    """
    recalled: list[dict] = []
    dim_en = gaps.get("dim_en") or {}

    cells_by_product: dict[str, list[str]] = {}
    for p, dim in gaps.get("unknown_cells") or []:
        cells_by_product.setdefault(p, []).append(dim)

    pricing_products = set(gaps.get("pricing_gap_products") or [])
    # 整类 pricing 缺失但池里有 pricing_page 抓取物 → 同样值得放宽视野(全表无价多为截断/抽取问题)
    if "pricing" in (gaps.get("missing_claim_types") or []) and \
            "pricing" not in (gaps.get("stale_gap_claim_types") or []):
        pricing_products |= {e.get("product") for e in evidence
                            if e.get("claim_type") == "pricing" and e.get("product")}

    for e in evidence:
        if e.get("_recalled"):
            continue  # 已回捞过,不重复(保证多轮循环收敛)
        p = e.get("product")
        ct = e.get("claim_type")
        hit = False
        if p in pricing_products and ct == "pricing":
            hit = True
        elif p in cells_by_product and ct in ("feature_existence", "performance_quality"):
            text = _texts(e)
            for dim in cells_by_product[p]:
                en = (dim_en.get(dim) or "").lower()
                if (dim and dim.lower() in text) or (en and en in text):
                    hit = True
                    break
        if hit:
            e["_recalled"] = True
            recalled.append(e)
    return recalled
