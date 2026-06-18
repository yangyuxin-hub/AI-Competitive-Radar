import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import collector
from src.collector_adapters import AdapterRegistry, CacheAdapter


class PricingRefillTest(unittest.TestCase):
    def test_targeted_refill_fetches_pricing_page_body(self):
        search_hit = {
            "evidence_id": "SSEARCH1",
            "product": "image2",
            "claim_type": "pricing",
            "source_type": "pricing_page",
            "source_url": "https://image2-ai.com/pricing",
            "claim": "Image2 pricing page",
            "extracted_snippet": "Check pricing plans.",
        }
        page_hit = {
            "evidence_id": "SPAGE001",
            "product": "image2",
            "claim_type": "pricing",
            "source_type": "official_page",
            "source_url": "https://image2-ai.com/pricing",
            "claim": "Starter $10.49 / month",
            "extracted_snippet": "Starter · $14.99 $10.49 / month",
        }
        gaps = [{
            "product": "image2",
            "claim_type": "pricing",
            "gap_type": "pricing_no_number",
            "fix": {
                "query_hint": "image2 pricing per month plans",
                "source_hint": ["pricing_page"],
                "bias": "vendor_claim",
            },
        }]

        with patch("src.search.search_available", return_value=True), \
             patch("src.search.search_plan_to_evidence", return_value=([search_hit], [])), \
             patch.object(collector.OfficialPageAdapter, "_fetch_one", return_value=[page_hit]):
            out = collector._targeted_refill(gaps, "pricing", max_per_gap=3)

        self.assertEqual(out[0]["evidence_id"], "SPAGE001")
        self.assertEqual(out[0]["collection_source"], "self_heal:official_pricing")
        self.assertIn("SSEARCH1", {e["evidence_id"] for e in out})

    def test_cache_patches_official_prices_when_live_pricing_is_weak(self):
        class WeakPricingAdapter:
            def can_fetch(self, product):
                return True

            def fetch(self, product, focus):
                return [{
                    "evidence_id": "SWEAK001",
                    "product": product,
                    "claim_type": "pricing",
                    "source_type": "web_search",
                    "source_bias": "vendor_claim",
                    "source_url": "https://example.com/pricing",
                    "observed_at": "2026-06-18",
                    "claim": "Pricing plans are available.",
                    "extracted_snippet": "Check the pricing page for plan details.",
                    "source_reliability": 0.7,
                    "claim_relevance": 0.7,
                    "evidence_confidence": 0.7,
                }]

        with tempfile.TemporaryDirectory() as td:
            cache = CacheAdapter(cache_dir=Path(td), enabled=True)
            cache.save("image2", [{
                "evidence_id": "SOFFIC1",
                "product": "image2",
                "claim_type": "pricing",
                "source_type": "official_page",
                "source_bias": "vendor_claim",
                "source_url": "https://image2-ai.com/pricing",
                "observed_at": "2026-06-18",
                "claim": "Starter $10.49 / month",
                "extracted_snippet": "Starter · $14.99 $10.49 / month",
                "source_reliability": 0.9,
                "claim_relevance": 0.9,
                "evidence_confidence": 0.9,
            }])

            reg = AdapterRegistry(runtime_profile="fast")
            reg.live_adapters = [WeakPricingAdapter()]
            reg.cache = cache
            reg.required_claim_types = {"pricing"}
            reg.planned_claim_types = {"pricing"}

            evs, meta = reg.fetch_all("image2", "")

        ids = {e["evidence_id"] for e in evs}
        self.assertIn("SWEAK001", ids)
        self.assertIn("SOFFIC1", ids)
        self.assertEqual(
            meta["adapter_events"][-1]["status"],
            "pricing_substance_patched",
        )


if __name__ == "__main__":
    unittest.main()
