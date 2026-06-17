"""PM 视角的定价策略分析。

这一层回答两个不同问题:
1. 定价模型:产品为什么这样收费,如何把免费用户转成付费用户。
2. 价格与性价比:给定场景下,用户花钱获得了什么价值。

输入只依赖 pricing_engine / engine_comparison,不抓取、不调用 LLM。
"""
from __future__ import annotations


def _present_mechanisms(engine: dict) -> set[str]:
    return {
        layer.get("mechanism")
        for layer in ((engine.get("pricing_structure") or {}).get("layers") or [])
        if layer.get("present")
    }


def _conversion_levers(engine: dict) -> list[str]:
    present = _present_mechanisms(engine)
    promotions = engine.get("promotions") or []
    levers = []
    if "freemium" in present:
        levers.append("免费额度/免费档降低试用门槛")
    if "subscription" in present:
        levers.append("会员订阅锁定月度预算与核心权益")
    if "credits" in present or "one_time_packs" in present:
        levers.append("积分/灵感值消耗把高频使用转成持续增购")
    if "usage" in present:
        levers.append("按量计费承接 API 或重度工作流")
    if any(p.get("kind") == "referral_reward" for p in promotions):
        levers.append("邀请好友送额度用社交裂变降低获客成本")
    if not levers:
        levers.append("计费形态未充分建模,需人工复核")
    return levers


def _business_logic(engine: dict) -> str:
    present = _present_mechanisms(engine)
    if {"freemium", "subscription", "credits"} <= present:
        return "用免费额度拉新,用会员权益转化,再用积分消耗承接重度创作需求。"
    if {"freemium", "subscription"} <= present:
        return "用免费档获客,用订阅档按用户规模或权益深度变现。"
    if "usage" in present:
        return "围绕实际消耗计费,更适合 API/企业工作流按量扩张。"
    return "证据不足,暂不判断完整商业逻辑。"


def _entitlement_design(engine: dict) -> dict:
    present = _present_mechanisms(engine)
    tiers = engine.get("tiers") or []
    has_unit_cost = any(t.get("unit_costs") for t in tiers)
    promotions = engine.get("promotions") or []
    promotional_rewards = []
    for promo in promotions:
        name = promo.get("name") or promo.get("kind") or "未命名活动"
        reward = promo.get("reward") or {}
        amount = reward.get("amount")
        unit = reward.get("unit")
        suffix = f"({amount:g}{unit})" if isinstance(amount, (int, float)) and unit else ""
        promotional_rewards.append(f"{name}{suffix}")
    return {
        "free_layer": (
            ["用免费额度/免费档承接尝鲜用户", "需核验水印、排队、每日额度是否构成转化触发点"]
            if "freemium" in present else []
        ),
        "subscription_entitlements": (
            ["按会员档位打包月度额度", "用更高档位降低单位额度成本"]
            if "subscription" in present else []
        ),
        "premium_capabilities": [
            "高阶模型", "高清输出", "首尾帧/运镜控制", "商用权益", "优先队列"
        ],
        "topup_rules": (
            ["积分/灵感值用完后的增购与消耗规则是复购关键"]
            if ("credits" in present or "one_time_packs" in present) else []
        ),
        "promotional_rewards": promotional_rewards,
        "conversion_trigger": (
            "免费额度不足、去水印/高清/高阶控制需求、积分耗尽"
            if has_unit_cost else "证据不足,需补齐权益和消耗率后判断"
        ),
    }


def _monetization_hypothesis(engine: dict) -> dict:
    present = _present_mechanisms(engine)
    if {"freemium", "subscription", "credits"} <= present:
        return {
            "target_paid_user": "持续生成图片/视频的创作者",
            "upsell_path": "免费试用 -> 会员订阅 -> 高额度档位/积分增购",
            "retention_mechanism": "月度额度和会员权益形成持续使用理由",
            "revenue_expansion": "高频生成通过积分消耗带来二次变现",
        }
    if {"freemium", "subscription"} <= present:
        return {
            "target_paid_user": "需要稳定权益或团队席位的用户",
            "upsell_path": "免费档 -> 个人/团队订阅",
            "retention_mechanism": "订阅权益和协作能力绑定工作流",
            "revenue_expansion": "按席位或高档权益扩张",
        }
    return {
        "target_paid_user": "unknown",
        "upsell_path": "unknown",
        "retention_mechanism": "unknown",
        "revenue_expansion": "unknown",
    }


def _product_strategy(product: dict) -> dict:
    engine = product.get("pricing_engine") or {}
    return {
        "product": product.get("name") or engine.get("product"),
        "archetype": engine.get("archetype", "Unknown"),
        "comparison_axis": engine.get("comparison_axis", "unknown"),
        "conversion_levers": _conversion_levers(engine),
        "entitlement_design": _entitlement_design(engine),
        "monetization_hypothesis": _monetization_hypothesis(engine),
        "business_logic": _business_logic(engine),
    }


def _scenario_baskets(comparison: dict) -> list[dict]:
    credit_winner = comparison.get("credit_price_winner") or {}
    caveat = "需结合生成质量、失败率和高阶功能判断"
    if comparison.get("gaps"):
        caveat = "当前单位产出口径不一致,不能只看月费或积分单价"
    winner = credit_winner.get("product")
    stable_best = (
        f"{winner}在额度/积分单价口径上更低,但不能等同于同规格视频成本优势"
        if winner else "需补齐同口径消耗率后判断"
    )
    return [
        {
            "scenario": "轻度尝鲜",
            "monthly_budget": "0-70 CNY",
            "expected_outputs": "少量图片/短视频试做",
            "required_capabilities": ["免费额度", "低水印/无水印", "基础模型可用性"],
            "decision_basis": "看免费额度、入门会员价、是否带水印和排队限制",
            "best_for": "低门槛试用优先,价格差距很小时看免费层体验",
        },
        {
            "scenario": "稳定短视频产出",
            "monthly_budget": "100-300 CNY",
            "expected_outputs": "每月 30-100 条标准短视频",
            "required_capabilities": ["标准视频消耗率", "月度额度", "生成成功率", "排队/并发"],
            "decision_basis": "看主力会员每月额度、积分单价、标准视频消耗率",
            "best_for": stable_best,
        },
        {
            "scenario": "高质量商业视频",
            "monthly_budget": "300+ CNY",
            "expected_outputs": "少量高质量可交付视频",
            "required_capabilities": ["高阶模型", "高清输出", "首尾帧", "运镜控制", "商用权益"],
            "decision_basis": "看高阶模型、首尾帧、运镜控制、高清输出、商用权益",
            "best_for": caveat,
        },
        {
            "scenario": "团队/工作室",
            "monthly_budget": "按项目/团队预算",
            "expected_outputs": "批量生成与多人协作",
            "required_capabilities": ["批量额度", "优先队列", "账号管理", "发票/合同", "稳定性"],
            "decision_basis": "看批量额度、优先队列、多人协作、发票/账号管理和稳定性",
            "best_for": "若缺少团队权益证据,不建议仅凭个人会员价格下结论",
        },
    ]


def _expanded_caveats(comparison: dict) -> list[str]:
    out = []
    for gap in comparison.get("gaps") or []:
        note = gap.get("note")
        if note:
            out.append(note)
        if gap.get("kind") == "unit_cost_axis_mismatch":
            caps = gap.get("capabilities") or []
            out.extend([
                f"单位不同:{' / '.join(caps)} 不能直接换算",
                "规格不同:需统一到同一模型、分辨率、时长和输入方式",
                "质量不同:还需纳入生成成功率、画面质量和失败扣费规则",
                "权益不同:水印、商用、排队、并发和高阶控制会改变实际价值",
            ])
    # stable order, de-dupe
    seen = set()
    deduped = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _normalized_cost_table(comparison: dict) -> list[dict]:
    rows = []
    for row in comparison.get("products") or []:
        rows.append({
            "product": row.get("product"),
            "entry_paid_monthly": row.get("entry_paid_monthly"),
            "best_price_per_credit": row.get("best_price_per_credit"),
            "best_unit_costs": row.get("best_unit_costs"),
        })
    return rows


def build_pricing_strategy_analysis(products: list[dict], comparison: dict) -> dict:
    """构建“商业模式”和“性价比”两页分析结构。"""
    products_analysis = [_product_strategy(p) for p in products if p.get("pricing_engine")]
    return {
        "pricing_model_analysis": {
            "products": products_analysis,
            "summary": "定价应拆成免费获客、会员转化、积分/用量增购三层来看,而不是只比较月费数字。",
        },
        "value_for_money_analysis": {
            "normalized_cost_table": _normalized_cost_table(comparison),
            "scenario_baskets": _scenario_baskets(comparison),
            "caveats": _expanded_caveats(comparison),
        },
    }
