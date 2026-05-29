"""Analyzer 超时降级路径测试。

保护点:LLM 超时/失败时,_fallback_facts / _fallback_derivations 必须产出
**通过 quick_validate 的合法 schema**(不引用不存在的 evidence_id、claim_type 对齐、
gap 覆盖 target+competitor、priority 公式自洽),否则降级反而把脏数据塞进报告。
"""
import unittest

from src import analyzer
from src.analyzer import (
    _compact_evidence,
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


class CompactEvidenceTest(unittest.TestCase):
    def test_caps_per_claim_type(self):
        import os
        many = [
            {"evidence_id": f"S{i:07d}", "claim_type": "user_pain", "product": "Cursor",
             "claim": "c", "extracted_snippet": "x" * 500, "evidence_confidence": i / 100}
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
