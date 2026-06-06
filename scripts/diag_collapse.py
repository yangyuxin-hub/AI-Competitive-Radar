# -*- coding: utf-8 -*-
"""功能矩阵塌陷诊断:跑 collector+analyzer,对每个 unknown 格子判定
「错配(证据其实有,RAG 能救)」还是「缺失(压根没采到,RAG 白搭)」。
env: DIAG_TARGET / DIAG_COMPETITORS / DIAG_FOCUS / DIAG_DOMAIN / DIAG_TAG。一次性脚本。"""
import sys, io, os, json, pathlib, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT); sys.path.insert(0, str(ROOT))

TARGET = os.environ.get("DIAG_TARGET", "Figma")
COMPS = [c.strip() for c in os.environ.get("DIAG_COMPETITORS", "Sketch,Canva").split(",") if c.strip()]
FOCUS = os.environ.get("DIAG_FOCUS", "界面设计协作体验")
TAG = os.environ.get("DIAG_TAG", "diag")
USER_INPUT = f"分析 {TARGET} 与 {', '.join(COMPS)} 在 {FOCUS} 上的差距"

# 维度中文 → 英文/中文同义扩展(尽量宽,给"错配"假设留足机会,避免低估)
ZH2EN = {
    "协同": "collaborat real-time realtime multiplayer co-edit shared", "协作": "collaborat team shared comment",
    "补全": "complet autocomplet suggestion tab inline", "代码": "code coding",
    "集成": "integrat plugin api connect extension marketplace", "插件": "plugin extension",
    "安全": "secur compliance soc privacy encrypt", "权限": "permission access role sso admin member",
    "版本": "version history branch revision", "原型": "prototyp interact clickable",
    "交互": "interact animat transition", "矢量": "vector pen path shape boolean", "绘制": "draw shape pen",
    "模板": "template preset starter", "组件": "component design-system token library style variant",
    "设计系统": "design system component token library", "评论": "comment feedback review", "批注": "annotat comment markup",
    "离线": "offline desktop local", "跨端": "cross-platform mobile desktop web sync",
    "团队": "team member seat workspace org", "成员": "member seat user",
    "性能": "performance speed fast latency slow lag responsive", "速度": "speed fast latency",
    "自动化": "automat workflow rule trigger", "智能": "ai intelligen agent", "ai": "ai agent automat",
    "任务": "task issue todo assign", "项目": "project board kanban sprint milestone",
    "报表": "report dashboard chart analytics", "视图": "view board calendar timeline gantt table",
    "数据库": "database table record field relation", "文档": "doc page wiki note knowledge",
    "知识": "knowledge wiki doc", "配额": "quota limit usage credit cap", "用量": "usage quota credit",
    "定价": "pric plan cost free subscription billing", "价格": "pric plan cost",
    "上手": "onboard learn easy beginner", "生态": "ecosystem community marketplace",
    "存储": "storage file upload gb", "导出": "export download", "交付": "handoff inspect spec dev",
}
_ZH_STOP = {"能力", "功能", "支持", "体验", "管理", "使用", "服务"}


def _dim_terms(name: str) -> set:
    """从维度名抽匹配词:命中的中文键 + 其英文扩展 + 中文 2-gram。"""
    terms: set = set()
    low = name.lower()
    for zh, en in ZH2EN.items():
        if zh in name or zh in low:
            terms.update(en.split())
            terms.add(zh)
    # 中文 2-gram 兜底
    chars = re.sub(r"[^一-龥]", "", name)
    for i in range(len(chars) - 1):
        g = chars[i:i + 2]
        if g not in _ZH_STOP:
            terms.add(g)
    return {t for t in terms if len(t) >= 2}


_FT = {"feature_existence", "performance_quality", "user_pain"}


def diagnose(facts: dict, evidence: list, products: list) -> dict:
    # 每产品的 feature 类证据文本
    blob = {p: "" for p in products}
    cnt = {p: 0 for p in products}
    for e in evidence:
        p = e.get("product")
        if p in blob and e.get("claim_type") in _FT:
            blob[p] += " " + (e.get("extracted_snippet") or "") + " " + (e.get("claim") or "")
            cnt[p] += 1
    blob = {p: v.lower() for p, v in blob.items()}

    feats = (facts.get("feature_tree") or {}).get("features") or []
    cells = {"total": 0, "unknown": 0, "错配": 0, "缺失-维度级": 0, "缺失-产品级": 0}
    detail = []
    for f in feats:
        name = f.get("name", "")
        terms = _dim_terms(name)
        for p in products:
            cells["total"] += 1
            st = ((f.get("products") or {}).get(p) or {}).get("support_status")
            if st != "unknown":
                continue
            cells["unknown"] += 1
            if cnt[p] <= 1:
                kind = "缺失-产品级"
            elif any(t.lower() in blob[p] for t in terms):
                kind = "错配"
            else:
                kind = "缺失-维度级"
            cells[kind] += 1
            detail.append({"product": p, "dim": name, "kind": kind,
                           "prod_feat_ev": cnt[p], "terms_hit": [t for t in terms if t.lower() in blob[p]][:5]})
    return {"cells": cells, "detail": detail, "feat_ev_count": cnt}


def main():
    from src.state import build_initial_state
    from src.collector import collector_node
    from src.analyzer import analyzer_node
    print("=" * 70); print(f"诊断 · {TAG} · {USER_INPUT}"); print("=" * 70)
    state = build_initial_state(USER_INPUT, TARGET, COMPS, [FOCUS],
                                runtime_profile="balanced")
    state = collector_node(state)
    state = analyzer_node(state)
    evidence = state.get("raw_evidence") or []
    facts = state.get("schema_draft") or {}
    products = [TARGET, *COMPS]
    res = diagnose(facts, evidence, products)

    c = res["cells"]
    print(f"\n[{TAG}] 功能格子总数 {c['total']} | unknown {c['unknown']}")
    print(f"  错配(证据其实有→RAG 能救):   {c['错配']}")
    print(f"  缺失-维度级(该维度没采到):     {c['缺失-维度级']}")
    print(f"  缺失-产品级(该产品几乎没证据): {c['缺失-产品级']}")
    print(f"  每产品 feature 证据数: {res['feat_ev_count']}")
    print("\n  unknown 明细:")
    for d in res["detail"]:
        print(f"    [{d['kind']:11}] {d['product']:14} | {d['dim']:18} (feat_ev={d['prod_feat_ev']}, 命中={d['terms_hit']})")

    out = ROOT / "out" / f"diag_{TAG}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "facts.json").write_text(json.dumps(facts.get("feature_tree") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "diag.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已落盘: {out}")


if __name__ == "__main__":
    main()
