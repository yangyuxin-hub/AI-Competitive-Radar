import time


def _ev(product, idx, claim_type, source_type, source_bias, claim, snippet):
    return {
        "evidence_id": f"S{product[:2].upper()}{idx:05d}"[:8],
        "product": product,
        "claim_type": claim_type,
        "claim": claim,
        "extracted_snippet": snippet,
        "source_type": source_type,
        "source_bias": source_bias,
        "source_url": f"https://example.com/{product}/{idx}",
        "claim_relevance": 0.9,
        "source_reliability": 0.8,
        "source_freshness": "current",
    }


def _fake_evidence(product):
    return [
        _ev(product, 1, "feature_existence", "official_page", "vendor_claim",
            "supports repository-aware code completion",
            "Official docs describe repository-aware code completion and editor integration."),
        _ev(product, 2, "feature_existence", "official_page", "vendor_claim",
            "supports inline suggestions",
            "Official docs describe inline suggestions, chat, and project context."),
        _ev(product, 3, "pricing", "pricing_page", "vendor_claim",
            "Pro plan costs $10/mo",
            "Pricing page lists a Pro plan at $10/mo with a free tier."),
        _ev(product, 4, "performance_quality", "reddit", "user_generated",
            "users report 500k line repo completion latency around 1.5 seconds",
            "Reddit users report 500k line repo completion latency around 1.5 seconds and compare it with another tool."),
        _ev(product, 5, "user_pain", "reddit", "user_generated",
            "users report indexing bugs and slow completions",
            "Reddit users report indexing bugs, slow completions, and occasional failures on large projects."),
        _ev(product, 6, "user_pain", "hn", "user_generated",
            "HN users mention context bugs in large projects",
            "Hacker News users mention context bugs in large projects and unreliable suggestions after file switches."),
    ]


def test_collector_harvests_future_completed_after_initial_timeout(monkeypatch):
    import src.collector as collector
    from src import source_ledger

    class SlowRegistry:
        live_adapters = [object()]

        def fetch_all(self, product, focus):
            time.sleep(0.1)
            evs = _fake_evidence(product)
            return evs, {
                "adapter_events": [{"adapter": "fake", "status": "ok"}],
                "coverage": {
                    "feature_existence": 2,
                    "performance_quality": 1,
                    "pricing": 1,
                    "user_pain": 2,
                },
            }

    monkeypatch.setenv("ANALYZER_MOCK", "1")
    monkeypatch.setenv("COLLECTOR_GRACE_SEC", "1")
    monkeypatch.setenv("COLLECTOR_HARVEST_ROUNDS", "2")
    monkeypatch.setattr(collector, "discover_all_urls", lambda products, allow_llm=False: {
        p: {
            "source": "test",
            "official_pages": [f"https://example.com/{p}/docs"],
            "pricing_pages": [f"https://example.com/{p}/pricing"],
        }
        for p in products
    })
    monkeypatch.setattr(collector, "reset_registry", lambda: None)
    monkeypatch.setattr(collector, "get_registry", lambda **kwargs: SlowRegistry())
    monkeypatch.setattr(collector, "runtime_settings", lambda profile: {
        "timeout_sec": 0,
        "max_evidence_per_product": 24,
        "search": False,
    })
    monkeypatch.setattr(collector, "_reclassify_official_claim_types", lambda evidence: 0)
    monkeypatch.setattr(source_ledger, "record", lambda *args, **kwargs: None)

    state = {
        "analysis_meta": {
            "target_product": "Alpha",
            "competitors": ["Beta"],
            "analysis_focus": ["code completion"],
            "runtime_profile": "balanced",
        },
        "raw_evidence": [],
        "reject_requirements": None,
    }

    out = collector.collector_node(state)

    assert len(out["raw_evidence"]) == 12
    for product_meta in out["collection_meta"]["products"].values():
        statuses = {evt.get("status") for evt in product_meta.get("adapter_events", [])}
        assert "timeout" not in statuses
    assert out["collection_meta"]["quality_audit"]["passed"] is True
