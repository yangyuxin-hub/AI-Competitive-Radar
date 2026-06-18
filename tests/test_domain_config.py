import unittest
from unittest.mock import patch

from src.graph import _load_domain_config


class DomainConfigTest(unittest.TestCase):
    def test_jimeng_domain_is_configured_for_ai_video_competitors(self):
        with patch.dict("os.environ", {"DOMAIN": "jimeng"}, clear=False):
            dom = _load_domain_config()

        self.assertIsNotNone(dom)
        self.assertEqual(dom["target_product"], "即梦AI")
        self.assertEqual(dom["competitors"], ["可灵Kling", "Runway"])
        self.assertEqual(dom["analysis_focus"], ["视频生成质量与可控性"])


if __name__ == "__main__":
    unittest.main()
