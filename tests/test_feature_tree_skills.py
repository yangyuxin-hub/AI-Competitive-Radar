import unittest

from src import feature_tree_skills as skills


class FeatureTreeSkillsTest(unittest.TestCase):
    def setUp(self):
        skills.reload()

    def test_selects_ai_coding_skill_from_products_and_focus(self):
        skill = skills.select_skill({
            "target_product": "Cursor",
            "competitors": ["GitHub Copilot", "Cline"],
            "analysis_focus": ["代码补全体验"],
        })
        self.assertIsNotNone(skill)
        self.assertEqual(skill["skill_id"], "ai_coding")

    def test_does_not_misfire_on_generic_dev_keyword(self):
        # "后端开发" 含"开发"曾误命中 ai_coding → BaaS 拿到代码补全功能树(实测退化)。
        # 现要求 ≥3 分且已剔除"开发",BaaS/设计/通用"开发"类焦点不应命中编程 skill。
        for meta in (
            {"target_product": "Supabase", "competitors": ["Firebase", "Appwrite"],
             "analysis_focus": ["后端开发体验与定价"]},
            {"target_product": "Figma", "competitors": ["Canva"],
             "analysis_focus": ["界面设计协作体验"]},
        ):
            self.assertIsNone(skills.select_skill(meta), meta["analysis_focus"])

    def test_fires_for_coding_focus_without_known_product(self):
        # 未知产品但焦点明确是代码补全(双词命中=4≥3),仍应命中,保住召回。
        skill = skills.select_skill({
            "target_product": "ToolA", "competitors": ["ToolB"],
            "analysis_focus": ["代码补全体验"],
        })
        self.assertIsNotNone(skill)
        self.assertEqual(skill["skill_id"], "ai_coding")

    def test_ai_coding_spine_comes_from_skill_config(self):
        spine = skills.feature_spine_for_meta({
            "target_product": "Cursor",
            "competitors": ["GitHub Copilot", "Cline"],
            "analysis_focus": ["代码补全体验"],
        })
        self.assertIsNotNone(spine)
        self.assertEqual(len(spine), 10)
        self.assertEqual(spine[0]["name"], "代码理解")
        self.assertIn("项目索引", spine[0]["representative_features"])
        self.assertEqual(spine[0]["source_skill"], "ai_coding")

    def test_ai_coding_domains_weights_sum_to_one(self):
        cfg = skills.domains_for_meta({
            "target_product": "Cursor",
            "competitors": ["GitHub Copilot", "Cline"],
            "analysis_focus": ["代码补全体验"],
        })
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["skill_id"], "ai_coding")
        self.assertAlmostEqual(sum(d["weight"] for d in cfg["domains"]), 1.0, places=5)
        self.assertEqual([d["name"] for d in cfg["domains"][:4]], [
            "代码理解", "代码生成", "代码修改与重构", "Agent 自动执行"
        ])

    def test_unknown_domain_returns_none(self):
        self.assertIsNone(skills.feature_spine_for_meta({
            "target_product": "Notion",
            "competitors": ["Coda"],
            "analysis_focus": ["知识库协作"],
        }))

    def test_long_tail_keys_flag_only_low_weight_noncore_modules(self):
        meta = {
            "target_product": "Cursor",
            "competitors": ["Windsurf"],
            "analysis_focus": ["代码补全体验"],
        }
        lt = skills.long_tail_feature_keys(meta)
        # F007-F010(ecosystem/platform/enterprise/collaboration,权重 ≤0.02)→ 长尾
        self.assertEqual({k for k in lt if k.startswith("F")}, {"F007", "F008", "F009", "F010"})
        # 核心 6 项(role=core)绝不被标长尾,以免被排除出补采
        self.assertFalse({"F001", "F002", "F003", "F004", "F005", "F006"} & lt)
        # name 键也返回,便于调用方按功能名命中
        self.assertIn("工程集成", lt)

    def test_long_tail_keys_empty_when_no_skill(self):
        self.assertEqual(skills.long_tail_feature_keys({
            "target_product": "Notion", "competitors": ["Coda"],
            "analysis_focus": ["知识库协作"],
        }), set())
