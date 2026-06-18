import unittest
from pathlib import Path
import tempfile, textwrap
from src import feature_weights as fw


class FeatureWeightsTest(unittest.TestCase):
    def setUp(self):
        fw.reload()  # 默认 config/feature_weights.yaml

    def test_version(self):
        self.assertEqual(fw.version(), "demo-v1")

    def test_domains_sum_to_one(self):
        doms = fw.domains_for_focus("视频生成质量与可控性")
        self.assertEqual(len(doms), 5)
        self.assertAlmostEqual(sum(d["weight"] for d in doms), 1.0, places=6)

    def test_ai_coding_focus_uses_developer_workflow_weights(self):
        doms = fw.domains_for_focus("代码补全体验")
        self.assertEqual(
            [d["name"] for d in doms],
            ["代码理解", "代码生成", "代码修改与重构", "Agent 自动执行", "调试与测试", "上下文管理", "工程集成", "模型配置", "安全权限", "协作交付"],
        )
        self.assertAlmostEqual(sum(d["weight"] for d in doms), 1.0, places=6)
        self.assertEqual(doms[0]["weight"], 0.20)
        self.assertEqual(doms[3]["weight"], 0.20)

    def test_unknown_focus_falls_back_to_default(self):
        doms = fw.domains_for_focus("不存在的焦点")
        self.assertEqual([d["id"] for d in doms],
                         [d["id"] for d in fw.domains_for_focus("视频生成质量与可控性")])

    def test_missing_file_falls_back_to_nonempty_default(self):
        fw.reload(Path(tempfile.gettempdir()) / "no_such_feature_weights.yaml")
        doms = fw.domains_for_focus("任意")
        self.assertGreaterEqual(len(doms), 2)
        self.assertEqual(fw.version(), "unversioned")
        fw.reload()
