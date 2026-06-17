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
