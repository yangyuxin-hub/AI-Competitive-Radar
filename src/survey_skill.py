"""问卷/用户访谈采集 Skill —— 对应课题「信息采集 Agent(问卷设计/问卷调研/用户访谈)」。

三步:
1. 问卷设计:LLM 围绕 analysis_focus 设计 4-6 道针对性问题,每题标 claim_type;
2. 模拟访谈:LLM 扮演 3-5 个多样化用户画像作答,产出访谈发现(findings);
3. 证据化:每条发现 → 一条 raw_evidence,**透明标注为合成数据**(source_type=
   simulated_interview、source_bias=synthetic、synthetic=True、可信度从低),
   绝不冒充真实用户反馈(合规)。问卷本身随 skill_meta 返回供溯源。

设计取舍:真实问卷/访谈无法在 demo 内对真人执行,故用 LLM 合成**可清晰区分**的访谈记录,
既补齐课题点名的采集能力,又满足"数据脱敏/不造假"——合成身份不含任何真实个人信息。
SURVEY_SKILL=0 可关闭。
"""
from __future__ import annotations

import hashlib
import os
from datetime import date

from . import scoring_config
from .skill import CollectorSkill


def _evidence_id(product: str, source_url: str, claim: str) -> str:
    raw = f"{product}|{source_url}|{claim}".encode("utf-8")
    return "S" + hashlib.sha1(raw).hexdigest()[:7].upper()


_ALLOWED_CT = {"user_pain", "performance_quality", "feature_existence"}


class SurveySkill(CollectorSkill):
    """问卷设计 + 模拟用户访谈,产出带来源标注的合成访谈证据。"""

    def can_execute(self, search_terms: list[str], **kwargs) -> bool:
        from .llm import is_mock_mode
        if is_mock_mode():
            return False  # mock 闭环不跑(避免真实 LLM 调用)
        if os.environ.get("SURVEY_SKILL", "1").strip() in ("0", "false", "False"):
            return False
        return bool(os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY"))

    def execute(self, search_terms: list[str], product: str = "", focus: str = "",
                **kwargs) -> tuple[list[dict], dict]:
        from .llm import get_llm
        llm = get_llm()
        timeout = float(os.environ.get("SURVEY_TIMEOUT", "60"))
        print(f"\n[Survey Skill] START — product={product}, focus={focus}")

        # 问卷设计 + 模拟访谈合并成一次调用(提速,~35s)
        sys = (
            "你是用户研究员。请针对给定产品与分析焦点,完成两件事并一次性输出:\n"
            "1) 设计一份 4-6 题的用户调研问卷(聚焦该焦点维度的真实体验/痛点/性能,问题具体可作答);\n"
            "2) 扮演 3-5 个**多样化**用户画像(如独立开发者/企业团队/学生/重度付费用户),针对问卷给出"
            "**具体、有细节、有分歧**的访谈反馈(有人满意有人抱怨,带具体场景/数字)。\n"
            '只输出 JSON: {"questions":[{"id":"Q1","text":"问题","claim_type":"user_pain|'
            'performance_quality|feature_existence"}],'
            '"findings":[{"persona":"画像简述","question_id":"Q1","claim_type":"user_pain|'
            'performance_quality|feature_existence","finding":"具体反馈(一句话)","expectation":"用户期望(可空)"}]}。\n'
            "findings 8-14 条,必须具体可信,不要营销话术;严禁编造身份证/手机号等真实个人信息。"
        )
        try:
            out = llm.call_json(sys, {"product": product, "analysis_focus": focus},
                                label=f"survey_{product}", timeout=timeout)
            questions = [q for q in (out.get("questions") or []) if q.get("text")][:6]
            findings = [f for f in (out.get("findings") or []) if f.get("finding")]
        except Exception as e:  # noqa: BLE001
            print(f"[Survey Skill] 调研失败,跳过: {type(e).__name__}: {e}")
            return [], {"adapter": "survey", "status": "failed", "reason": str(e)}
        if not findings:
            return [], {"adapter": "survey", "status": "empty", "questionnaire": questions}
        print(f"[Survey Skill] 问卷 {len(questions)} 题 + 访谈 {len(findings)} 条")

        # ── Step 3: 证据化(透明标注合成) ────────────────────────────────
        observed = date.today().isoformat()
        evidences: list[dict] = []
        for idx, f in enumerate(findings):
            ct = f.get("claim_type")
            if ct not in _ALLOWED_CT:
                ct = "user_pain"
            persona = str(f.get("persona") or "匿名受访者")
            finding = str(f.get("finding") or "").strip()
            if not finding:
                continue
            expectation = str(f.get("expectation") or "").strip()
            qid = f.get("question_id") or "?"
            # 合成来源用 synthetic:// 方案,可定位到具体问卷题目(溯源)
            source_url = f"synthetic://survey/{product}/{qid}#{idx}"
            claim = f"[模拟访谈] {finding}"
            snippet = (f"受访画像: {persona}｜反馈: {finding}"
                       + (f"｜期望: {expectation}" if expectation else ""))[:260]
            evidences.append({
                "evidence_id": _evidence_id(product, source_url, claim),
                "product": product,
                "claim_type": ct,
                "source_type": "simulated_interview",
                "source_bias": "synthetic",
                "source_url": source_url,
                "observed_at": observed,
                "source_freshness": "unknown",  # A1: 合成数据无真实发布时间
                "claim": claim,
                "extracted_snippet": snippet,
                # 合成数据可信度低于真实用户/第三方,避免在可信度评分里被高估
                "source_reliability": scoring_config.reliability("simulated_interview", 0.40),
                "claim_relevance": 0.7,
                "evidence_confidence": 0.45,
                "synthetic": True,
                "metadata": {
                    "synthetic": True,
                    "method": "LLM 模拟用户访谈(非真实用户,已脱敏)",
                    "persona": persona,
                    "question_id": qid,
                    "expectation": expectation,
                },
            })

        meta = {
            "adapter": "survey",
            "status": "success",
            "synthetic": True,
            "disclaimer": "本采集源为 LLM 合成的模拟问卷/访谈数据,用于演示采集能力;"
                          "非真实用户反馈,已标注 source_bias=synthetic,可信度计权偏低。",
            "questionnaire": questions,
            "n_questions": len(questions),
            "n_findings": len(evidences),
        }
        print(f"[Survey Skill] 产出 {len(evidences)} 条合成访谈证据(已标注 synthetic)")
        return evidences, meta
