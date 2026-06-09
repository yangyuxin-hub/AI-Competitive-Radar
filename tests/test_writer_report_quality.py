from src import writer


EVIDENCE = [
    {
        "evidence_id": "SOFFIC01",
        "source_type": "official_page",
        "source_bias": "vendor_claim",
        "claim_type": "feature_existence",
    },
    {
        "evidence_id": "SREDDIT1",
        "source_type": "reddit",
        "source_bias": "user_generated",
        "claim_type": "performance_quality",
    },
    {
        "evidence_id": "SPRICE01",
        "source_type": "pricing_page",
        "source_bias": "vendor_claim",
        "claim_type": "pricing",
    },
]


def _feature_tree():
    return {
        "category": "人声生成还原度",
        "features": [
            {
                "feature_id": "F001",
                "name": "人声生成还原度",
                "gap": {
                    "winner": "Suno",
                    "gap_type": "performance",
                    "reason": "Suno 用户侧体验略优",
                    "evidence_ids": ["SREDDIT1"],
                },
                "products": {
                    "Suno": {
                        "support_status": "supported",
                        "support_evidence_ids": ["SOFFIC01"],
                        "quality_score": {
                            "score": 4,
                            "scale": 5,
                            "basis": "用户反馈人声更自然",
                            "evidence_ids": ["SREDDIT1"],
                        },
                    },
                    "ElevenLabs Music": {
                        "support_status": "supported",
                        "support_evidence_ids": ["SOFFIC01"],
                        "quality_score": {
                            "score": 0,
                            "scale": 5,
                            "basis": "仅确认具备,无质量证据",
                            "evidence_ids": [],
                        },
                    },
                },
            }
        ],
    }


def test_score_overview_separates_status_score_and_grade():
    md = writer._render_score_overview(_feature_tree(), ["Suno", "ElevenLabs Music"], EVIDENCE)

    assert "能力状态" in md
    assert "已验证体验评分" in md
    assert "证据等级" in md
    assert "Suno: supported" in md
    assert "ElevenLabs Music: 未评分" in md
    assert "已验证体验均分" in md
    assert "综合均分" not in md


def test_feature_gap_evidence_is_readable_but_keeps_chip():
    md = writer._render_feature_gaps(_feature_tree(), EVIDENCE)

    assert "[SREDDIT1]" in md
    assert "Reddit" in md
    assert "体验质量" in md


def test_recommendation_pricing_contradiction_is_flagged():
    schema = {
        "feature_tree": _feature_tree(),
        "user_persona": {"pain_points": []},
    }
    recs = [
        {
            "rec_id": "R003",
            "action": "新增9.9美元/月轻量档,低于Udio 8美元的Standard档位",
            "rationale": "Suno 10美元略高于Udio 8美元,低价档用于防守价格敏感用户。",
            "source_pricing": True,
            "evidence_ids": ["SPRICE01"],
            "priority_score": {"priority": "P1", "final_score": 3.8},
        }
    ]

    md = writer._render_recommendations(recs, schema)

    assert "竞品机会" in md
    assert "定价逻辑需复核" in md
