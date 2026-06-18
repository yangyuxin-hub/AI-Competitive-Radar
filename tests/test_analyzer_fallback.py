"""Analyzer 超时降级路径测试。

保护点:LLM 超时/失败时,_fallback_facts / _fallback_derivations 必须产出
**通过 quick_validate 的合法 schema**(不引用不存在的 evidence_id、claim_type 对齐、
gap 覆盖 target+competitor、priority 公式自洽),否则降级反而把脏数据塞进报告。
"""
import unittest

from src import analyzer
from src.analyzer import (
    _compact_evidence,
    _fallback_decision_summary,
    _fallback_derivations,
    _fallback_facts,
    _step2_derivations,
    quick_validate_derivations,
    quick_validate_facts,
    sanitize_derivations,
)


META = {
    "target_product": "Cursor",
    "competitors": ["Windsurf"],
    "analysis_focus": ["代码补全体验"],
}

EVIDENCE = [
    {"evidence_id": "SFEAT001", "claim_type": "feature_existence", "product": "Cursor",
     "claim": "Cursor 支持 Tab 补全", "extracted_snippet": "Tab 补全", "evidence_confidence": 0.9},
    {"evidence_id": "SFEAT002", "claim_type": "feature_existence", "product": "Windsurf",
     "claim": "Windsurf 支持 Tab 补全", "extracted_snippet": "Tab 补全", "evidence_confidence": 0.8},
    {"evidence_id": "SPERF001", "claim_type": "performance_quality", "product": "Cursor",
     "claim": "Cursor 补全较快", "extracted_snippet": "补全较快", "evidence_confidence": 0.7},
    {"evidence_id": "SPRICE01", "claim_type": "pricing", "product": "Cursor",
     "claim": "Pro $20/mo", "extracted_snippet": "$20", "evidence_confidence": 0.95},
    {"evidence_id": "SPAIN001", "claim_type": "user_pain", "product": "Cursor",
     "claim": "补全偶尔卡顿", "extracted_snippet": "卡顿", "evidence_confidence": 0.6},
]


class FallbackFactsTest(unittest.TestCase):
    def test_fallback_facts_passes_quick_validate(self):
        facts = _fallback_facts(EVIDENCE, META, reason="TimeoutError: read timed out")
        issues = quick_validate_facts(facts, EVIDENCE, META)
        self.assertEqual(issues, [], f"fallback facts 不应产生校验问题: {issues}")

    def test_fallback_facts_covers_target_and_competitor(self):
        facts = _fallback_facts(EVIDENCE, META, reason="x")
        products = facts["feature_tree"]["features"][0]["products"]
        self.assertIn("Cursor", products)
        self.assertIn("Windsurf", products)

    def test_fallback_facts_only_references_existing_ids(self):
        valid = {e["evidence_id"] for e in EVIDENCE}
        facts = _fallback_facts(EVIDENCE, META, reason="x")
        for _path, eids, _allowed in analyzer.collect_all_evidence_refs(facts):
            for eid in eids:
                self.assertIn(eid, valid, f"{_path} 引用了不存在的 {eid}")


class GapReasonPrecisionTest(unittest.TestCase):
    """R6 收敛:gap.reason(确定性合成)不得把 1-5 粗判复述成精确基准对比,
    1 分差距不得夸成'领先'。精确分仍保留在结构化字段与§四评分表。"""
    _META = {"target_product": "Cursor"}

    def _block(self, cur, comp):
        b = {"Cursor": {"support_status": "supported",
                        "quality_score": {"score": cur, "scale": 5, "evidence_ids": ["SAAA1111"]},
                        "support_evidence_ids": ["SBBB2222"]}}
        if comp is not None:
            b["Windsurf"] = {"support_status": "supported",
                             "quality_score": {"score": comp, "scale": 5, "evidence_ids": ["SCCC3333"]}}
        else:
            b["Windsurf"] = {"support_status": "unknown"}
        return b

    def test_one_point_spread_is_hedged_not_lead(self):
        g = analyzer._compute_gap("基础内联补全", self._block(4, 3), self._META)
        self.assertNotIn("/5", g["reason"])          # 不复述精确分
        self.assertNotIn("领先", g["reason"])          # 1 分差距不夸成领先
        self.assertIn("略优", g["reason"])
        self.assertIn("有限", g["reason"])

    def test_two_point_spread_allows_clear_lead(self):
        g = analyzer._compute_gap("跨文件", self._block(5, 3), self._META)
        self.assertNotIn("/5", g["reason"])
        self.assertIn("明显更优", g["reason"])

    def test_single_rated_no_precise_score_in_prose(self):
        g = analyzer._compute_gap("大代码库", self._block(3, None), self._META)
        self.assertNotIn("/5", g["reason"])
        self.assertIn("证据不足", g["reason"])


class OvergeneralizationSoftenTest(unittest.TestCase):
    """R6 收敛(确定性兜底):样本不足时把'大量/普遍/多数用户'降级为'部分用户',
    不赌小模型遵守 prompt 纪律;真高频(high+≥3 样本 / ≥3 证据)保留。"""

    def test_text_level_substitutions(self):
        from src.analyzer import _soften_text
        self.assertEqual(_soften_text("大量用户反馈卡顿")[0], "部分用户反馈卡顿")
        self.assertEqual(_soften_text("用户普遍反馈响应慢")[0], "部分用户反馈响应慢")
        self.assertEqual(_soften_text("多数用户抱怨贵")[0], "部分用户抱怨贵")
        self.assertEqual(_soften_text("部分用户反馈")[1], False)  # 已合规不改

    def test_quantifier_with_intervening_modifier(self):
        from src.analyzer import _soften_text
        # "大量现有Copilot用户" —— 量词与"用户"间夹了修饰词,仍需收敛
        self.assertEqual(_soften_text("大量现有Copilot用户存在不满")[0], "部分现有Copilot用户存在不满")
        self.assertEqual(_soften_text("广泛存在的主流方式")[0], "部分场景存在的主流方式")

    def test_legit_noun_not_touched(self):
        from src.analyzer import _soften_text
        # "大量代码/样板"是合法用法(非用户群体),不得误杀
        self.assertEqual(_soften_text("大量重复样板代码编写")[1], False)

    def test_feature_basis_gated_by_quality_evidence(self):
        schema = {"feature_tree": {"features": [
            {"products": {"Cursor": {"quality_score": {
                "basis": "多数用户反馈对话集成流畅", "evidence_ids": ["S1", "S2"]}}}},      # 2 证据→收敛
            {"products": {"Copilot": {"quality_score": {
                "basis": "大量用户反馈补全流畅", "evidence_ids": ["S1", "S2", "S3"]}}}},     # 3 证据→保留
        ]}}
        analyzer.soften_overgeneralization(schema)
        feats = schema["feature_tree"]["features"]
        self.assertIn("部分用户", feats[0]["products"]["Cursor"]["quality_score"]["basis"])
        self.assertIn("大量用户", feats[1]["products"]["Copilot"]["quality_score"]["basis"])

    def test_landscape_and_recommendations_covered(self):
        schema = {
            "competitor_landscape": {"alternative": [
                {"reason": "仍广泛存在的主流编码方式", "evidence_ids": ["S1"]}]},
            "recommendations": [
                {"action": "抢夺用户", "rationale": "大量现有Copilot用户存在不满",
                 "evidence_ids": ["S1"]}],
        }
        n = analyzer.soften_overgeneralization(schema)
        self.assertGreaterEqual(n, 2)
        self.assertIn("部分场景", schema["competitor_landscape"]["alternative"][0]["reason"])
        self.assertIn("部分现有Copilot用户", schema["recommendations"][0]["rationale"])

    def test_recommendation_quantifiers_softened_even_with_many_refs(self):
        schema = {"recommendations": [{
            "rationale": (
                "大量专业影视用户吐槽长内容连贯创作不足，"
                "高清输出需求在专业用户群体中多次被提及，"
                "即梦领先优势有3条以上高可信证据支撑"
            ),
            "action": "抢占大量普通创作爱好者",
            "evidence_ids": ["S1", "S2", "S3", "S4"],
        }]}

        n = analyzer.soften_overgeneralization(schema)

        text = schema["recommendations"][0]["rationale"]
        self.assertGreaterEqual(n, 2)
        self.assertNotIn("大量", text)
        self.assertNotIn("多次", text)
        self.assertNotIn("3条以上", text)
        self.assertIn("部分专业影视用户", text)
        self.assertIn("有证据提及", text)
        self.assertIn("现有证据支撑", text)

    def test_small_sample_pain_softened(self):
        schema = {"user_persona": {"pain_points": [
            {"description": "大量用户反馈补全卡顿",
             "frequency": {"level": "high", "sample_size": 1, "count": "1 条"}}]}}
        n = analyzer.soften_overgeneralization(schema)
        self.assertEqual(n, 1)
        self.assertIn("部分用户", schema["user_persona"]["pain_points"][0]["description"])
        self.assertNotIn("大量", schema["user_persona"]["pain_points"][0]["description"])

    def test_genuinely_frequent_pain_kept(self):
        schema = {"user_persona": {"pain_points": [
            {"description": "用户普遍抱怨索引慢",
             "frequency": {"level": "high", "sample_size": 5, "count": "5 条中 4 条"}}]}}
        n = analyzer.soften_overgeneralization(schema)
        self.assertEqual(n, 0)  # high + ≥3 样本 → 真普遍,保留
        self.assertIn("普遍", schema["user_persona"]["pain_points"][0]["description"])

    def test_swot_softened_by_evidence_count(self):
        schema = {"swot": {"weaknesses": [
            {"point": "大量用户反馈大代码库适配差", "evidence_ids": ["S1"]},
            {"point": "众多用户称延迟高", "evidence_ids": ["S1", "S2", "S3"]}]}}
        analyzer.soften_overgeneralization(schema)
        self.assertIn("部分用户", schema["swot"]["weaknesses"][0]["point"])   # 1 证据 → 收敛
        self.assertIn("众多用户", schema["swot"]["weaknesses"][1]["point"])   # 3 证据 → 保留


class NormalizeUserSegmentsTest(unittest.TestCase):
    """P0 bug:LLM 常把画像名错塞进 segment_id(name 留空),writer 渲染成 '名字 ?'。
    确定性 normalize 必须把错位名回填、segment_id 重排 U001.. 、丢弃纯空壳。"""

    def test_misplaced_name_in_segment_id_is_recovered(self):
        from src.analyzer import normalize_user_segments
        persona = {"user_segments": [
            {"segment_id": "U001", "name": "海外创意从业者", "description": "d1",
             "evidence_ids": ["S1"]},
            {"segment_id": "国内中文场景电商创作者", "description": "d2",   # 名字错塞进 id
             "evidence_ids": ["S2"]},
        ]}
        n = normalize_user_segments(persona)
        self.assertGreater(n, 0)
        segs = persona["user_segments"]
        self.assertEqual(segs[1]["name"], "国内中文场景电商创作者")  # 回填
        self.assertEqual([s["segment_id"] for s in segs], ["U001", "U002"])  # 重排
        # 没有任何 name 渲染成 '?' 的隐患
        self.assertTrue(all((s.get("name") or "").strip() for s in segs))

    def test_empty_shell_segment_dropped(self):
        from src.analyzer import normalize_user_segments
        persona = {"user_segments": [
            {"segment_id": "U001", "name": "真实分群", "description": "d"},
            {"segment_id": "U002"},   # 无 name 无 description → 空壳
            {"name": "  ", "description": ""},  # 全空白 → 空壳
        ]}
        normalize_user_segments(persona)
        segs = persona["user_segments"]
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["name"], "真实分群")

    def test_idempotent(self):
        from src.analyzer import normalize_user_segments
        persona = {"user_segments": [
            {"segment_id": "海报创作者", "description": "d2", "evidence_ids": ["S2"]},
        ]}
        normalize_user_segments(persona)
        n2 = normalize_user_segments(persona)
        self.assertEqual(n2, 0)  # 第二遍零改动
        self.assertEqual(persona["user_segments"][0]["segment_id"], "U001")
        self.assertEqual(persona["user_segments"][0]["name"], "海报创作者")

    def test_empty_persona_safe(self):
        from src.analyzer import normalize_user_segments
        self.assertEqual(normalize_user_segments({}), 0)
        self.assertEqual(normalize_user_segments({"user_segments": []}), 0)


class FallbackDecisionSummaryGroundingTest(unittest.TestCase):
    """P0:why_success / how_monetize 之前 refs 恒空(无 chip,显得没证据),
    且文案泛化('形态为全能型')。增强后须从 feature winners / pricing tiers 挂上 refs,
    并把 target 胜出的能力点写进结论。"""

    def _schema(self):
        return {
            "feature_tree": {
                "tree": {"domains": [{"modules": [{"points": [
                    {"id": "F001", "name": "艺术质感",
                     "products": {"Midjourney": {
                         "support_status": "supported", "depth_score": 5,
                         "support_evidence_ids": ["SWIN0001"],
                         "quality_score": {"evidence_ids": ["SWIN0002"]}}}},
                ]}]}]},
                "analysis": {
                    "archetypes": {"Midjourney": "全能型"},
                    "moat_candidates": [],
                    "winners": [
                        {"feature_id": "F001", "name": "艺术质感",
                         "winner": "Midjourney", "confidence": "high"},
                    ],
                },
            },
            "pricing_model": {"products": [{
                "name": "Midjourney",
                "pricing_engine": {"archetype": "Subscription"},
                "tiers": [{"tier_name": "Basic", "evidence_ids": ["SPRC0001"]}],
            }]},
            "recommendations": [],
        }

    def test_why_success_grounded_in_winning_feature(self):
        ds = _fallback_decision_summary(self._schema(), target="Midjourney")
        why = ds["why_success"]
        self.assertIn("艺术质感", why["answer"])       # 写进胜出能力点
        self.assertTrue(why["refs"])                    # 有 chip
        self.assertTrue(set(why["refs"]) & {"SWIN0001", "SWIN0002"})
        self.assertEqual(why["confidence"], "high")     # 跟随 winner 置信度

    def test_how_monetize_carries_pricing_refs(self):
        ds = _fallback_decision_summary(self._schema(), target="Midjourney")
        hm = ds["how_monetize"]
        self.assertIn("Subscription", hm["answer"])
        self.assertIn("SPRC0001", hm["refs"])

    def test_no_winner_falls_back_to_archetype_low(self):
        schema = self._schema()
        schema["feature_tree"]["analysis"]["winners"] = []  # target 无胜出
        ds = _fallback_decision_summary(schema, target="Midjourney")
        self.assertIn("全能型", ds["why_success"]["answer"])
        self.assertEqual(ds["why_success"]["confidence"], "low")


class FallbackDerivationsTest(unittest.TestCase):
    def test_fallback_derivations_priority_formula_consistent(self):
        facts = _fallback_facts(EVIDENCE, META, reason="x")
        der = _fallback_derivations(facts, EVIDENCE, META, reason="timeout")
        issues = quick_validate_derivations(der, facts, EVIDENCE)
        self.assertEqual(issues, [], f"fallback derivations 不应产生校验问题: {issues}")

    def test_fallback_derivations_has_recommendation(self):
        facts = _fallback_facts(EVIDENCE, META, reason="x")
        der = _fallback_derivations(facts, EVIDENCE, META, reason="timeout")
        self.assertTrue(der["recommendations"])
        self.assertIn("priority", der["recommendations"][0]["priority_score"])


class Step2TimeoutPathTest(unittest.TestCase):
    """LLM 抛异常时,_step2_derivations 必须吞掉异常并返回 fallback,而非崩溃。"""

    def test_step2_falls_back_on_llm_exception(self):
        facts = _fallback_facts(EVIDENCE, META, reason="x")

        class _BoomLLM:
            def call_json(self, *a, **k):
                raise TimeoutError("read timed out")

        orig_mock = analyzer.is_mock_mode
        orig_get = analyzer.get_llm
        analyzer.is_mock_mode = lambda: False
        analyzer.get_llm = lambda: _BoomLLM()
        try:
            der = _step2_derivations(facts, EVIDENCE, META)
        finally:
            analyzer.is_mock_mode = orig_mock
            analyzer.get_llm = orig_get

        # 没崩溃,且拿到 fallback 形态的结果
        self.assertIn("recommendations", der)
        self.assertEqual(quick_validate_derivations(der, facts, EVIDENCE), [])


class SanitizeDerivationsTest(unittest.TestCase):
    """确定性 repair 必须把脏 derivations 修成能过 quick_validate(替代 LLM 重跑)。"""

    def test_drops_bad_refs_and_fixes_formula(self):
        facts = _fallback_facts(EVIDENCE, META, reason="x")
        valid_fid = facts["feature_tree"]["features"][0]["feature_id"]
        weights = {"a": 0.5, "b": 0.5}
        der = {
            "swot": {
                "target": "Cursor",
                "weaknesses": [{"point": "x", "evidence_ids": ["SPAIN001", "SBOGUS9"]}],
                "strengths": [], "opportunities": [], "threats": [],
            },
            "recommendations": [
                {
                    "rec_id": "R001", "action": "do",
                    "source_feature_ids": [valid_fid, "FBOGUS"],
                    "source_pain_ids": ["PBOGUS"],
                    "evidence_ids": ["SPAIN001", "SBOGUS9"],
                    "priority_score": {"a": 4, "b": 2, "weights": weights, "final_score": 9.99},
                }
            ],
        }
        out, dropped = sanitize_derivations(der, facts, evidence=EVIDENCE)
        self.assertGreaterEqual(dropped, 3)  # SBOGUS9 ×2 + FBOGUS + PBOGUS
        # 公式重算 = 4*0.5 + 2*0.5 = 3.0
        self.assertEqual(out["recommendations"][0]["priority_score"]["final_score"], 3.0)
        # 修复后应无校验问题
        self.assertEqual(quick_validate_derivations(out, facts, EVIDENCE), [])


class EnsurePriorityScoresTest(unittest.TestCase):
    def test_backfills_missing_priority_and_keeps_existing(self):
        from src.analyzer import _ensure_priority_scores

        der = {"recommendations": [
            {"rec_id": "R001", "evidence_ids": ["SX"]},  # 缺 priority_score
            {"rec_id": "R002", "priority_score": {  # 已有合法的,不应被改
                "pain_frequency": 5, "business_impact": 5, "implementation_feasibility": 5,
                "evidence_confidence": 5, "weights": {"x": 1}, "final_score": 5.0, "priority": "P0"}},
            {"rec_id": "R003"},
        ]}
        n = _ensure_priority_scores(der)
        self.assertEqual(n, 2)  # R001, R003 补
        recs = der["recommendations"]
        self.assertEqual(recs[0]["priority_score"]["priority"], "P0")  # idx0
        self.assertEqual(recs[1]["priority_score"]["final_score"], 5.0)  # 未动
        # 补的 final_score 与公式自洽
        ps = recs[0]["priority_score"]
        expected = round(sum(ps[k] * ps["weights"][k] for k in ps["weights"]), 2)
        self.assertEqual(ps["final_score"], expected)


class CompactEvidenceTest(unittest.TestCase):
    def test_caps_per_claim_type(self):
        import os
        # 内容各不相同,避免被 _compact_evidence 的近似去重塌成 1 条(本测验 cap,非 dedup)
        many = [
            {"evidence_id": f"S{i:07d}", "claim_type": "user_pain", "product": "Cursor",
             "claim": f"痛点 {i}: " + " ".join(f"tok{i}_{j}" for j in range(20)),
             "extracted_snippet": f"用户 {i} 反馈 " + " ".join(f"w{i}d{j}" for j in range(40)),
             "evidence_confidence": i / 100}
            for i in range(20)
        ]
        os.environ["ANALYZER_MAX_EVIDENCE_PER_TYPE"] = "8"
        os.environ["ANALYZER_SNIPPET_LEN"] = "180"
        try:
            out = _compact_evidence(many)
        finally:
            del os.environ["ANALYZER_MAX_EVIDENCE_PER_TYPE"]
            del os.environ["ANALYZER_SNIPPET_LEN"]
        self.assertEqual(len(out), 8)
        self.assertTrue(all(len(o["extracted_snippet"]) <= 180 for o in out))
        # 取的是 confidence 最高的 8 条
        self.assertIn("S0000019", {o["evidence_id"] for o in out})


if __name__ == "__main__":
    unittest.main()
