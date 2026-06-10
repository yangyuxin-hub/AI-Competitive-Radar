"""V2EX Skill — Collector 高层技能（API 方式）

精简流程（3 步）:
1. LLM 生成关键词 + 推荐精细 V2EX 节点
2. 按节点拉话题，关键词过滤，抓回复
3. 转 evidence（省掉 LLM 判相关性——节点+关键词已经足够定位）

每一步终端留痕。整体限时 35s。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from . import scoring_config
from .skill import CollectorSkill

# ────────────────────────────────────────────────────────────────────────────
# 常量
# ────────────────────────────────────────────────────────────────────────────

_V2EX_TOPIC_BY_NODE = "https://www.v2ex.com/api/topics/show.json"
_V2EX_REPLIES = "https://www.v2ex.com/api/replies/show.json"

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_HEADERS = {
    "User-Agent": "AICompetitiveRadar/0.1 (+https://github.com/yangyuxin-hub/AI-Competitive-Radar; academic)",
    "Accept": "application/json",
}

MAX_REPLIES_PER_TOPIC = 3
MAX_TOPICS_TO_FETCH_REPLIES = 10

_KEYWORD_GEN_PROMPT = """\
你是竞品分析数据采集助手。根据产品名称和分析方向，输出两样东西:

1. **keywords**: 6-7 个搜索关键词，中英文混用（产品名英文，描述中文），简短 2-5 字/词
2. **v2ex_nodes**: 3-5 个最可能讨论该产品的 V2EX 节点名（英文小写）

选节点的思路（关键: 要精细，不要大节点）:
- 产品专属节点优先: 如 cursor、copilot、windsurf（如果存在）
- 产品技术栈节点: 如 python、javascript、ide、rust、golang
- AI/工具类小节点: 如 ai、llm、tool、extension
- 开发者体验类: 如 create、qna、分享
- 禁止选: programador（太大太杂）、apple、mac、jobs（无关）

返回 JSON:
{{"keywords": ["kw1", ...], "v2ex_nodes": ["node1", ...], "reasoning": "简要说明"}}
"""


# ────────────────────────────────────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────────────────────────────────────


def _evidence_id(product: str, source_url: str, claim: str) -> str:
    raw = f"{product}|{source_url}|{claim}".encode("utf-8")
    return "S" + hashlib.sha1(raw).hexdigest()[:7].upper()


def _infer_claim_type(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ("价格", "定价", "收费", "免费", "付费", "订阅", "会员", "$", "元/月", "pricing", "free", "paid")):
        return "pricing"
    if any(w in lower for w in ("吐槽", "垃圾", "难用", "卡顿", "崩溃", "bug", "问题", "慢", "差", "失望", "后悔", "不好", "不行", "拉胯")):
        return "user_pain"
    if any(w in lower for w in ("速度", "性能", "响应", "延迟", "流畅", "准确率", "补全", "benchmark", "快", "慢")):
        return "performance_quality"
    return "feature_existence"


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _product_tokens(product: str) -> set[str]:
    """产品名拆成可匹配 token:全名 + CJK 串 + 拆驼峰的拉丁词。
    例:'可灵Kling' → {'可灵kling','可灵','kling'};'StableDiffusion' → {..,'stable','diffusion'}。
    用于社区证据的产品相关性硬门(避免把无关热帖当本产品证据)。"""
    p = (product or "").strip()
    toks: set[str] = set()
    if not p:
        return toks
    toks.add(p.lower())
    for m in re.findall(r"[A-Za-z][a-z]+|[一-鿿]{2,}|[A-Za-z]{2,}", p):
        if len(m) >= 2:
            toks.add(m.lower())
    return toks


def _mentions_product(text: str, product: str) -> bool:
    """文本是否真的提到本产品(任一 token 命中)。社区帖相关性硬门。"""
    low = (text or "").lower()
    return any(tok in low for tok in _product_tokens(product))


# ────────────────────────────────────────────────────────────────────────────
# V2EX API（带重试）
# ────────────────────────────────────────────────────────────────────────────


def _fetch_topics_by_node(node: str, retries: int = 2) -> list[dict]:
    """按节点拉最新话题，失败重试"""
    url = f"{_V2EX_TOPIC_BY_NODE}?node_name={node}"
    for attempt in range(retries):
        print(f"  [V2EX] fetching node/{node}" + (f" (retry {attempt})" if attempt else "") + " ...")
        try:
            with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
                r = client.get(url)
                r.raise_for_status()
                topics = r.json()
                print(f"  [V2EX] node/{node}: {len(topics)} topics")
                return topics
        except Exception as e:
            print(f"  [V2EX] node/{node} FAILED: {type(e).__name__}: {e}")
            if attempt < retries - 1:
                time.sleep(1)
    return []


def _fetch_replies(topic_id: int) -> list[dict]:
    url = f"{_V2EX_REPLIES}?topic_id={topic_id}"
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()[:MAX_REPLIES_PER_TOPIC]
    except Exception as e:
        print(f"  [V2EX] replies/{topic_id} FAILED: {type(e).__name__}: {e}")
        return []


def _keyword_filter(topics: list[dict], keywords: list[str]) -> list[dict]:
    """本地关键词过滤：标题或内容匹配任一关键词即保留"""
    matched = []
    for t in topics:
        title = (t.get("title") or "").lower()
        content = (t.get("content") or "").lower()
        combined = title + " " + content
        if any(kw.lower() in combined for kw in keywords):
            matched.append(t)
    return matched


# ────────────────────────────────────────────────────────────────────────────
# Skill 主体
# ────────────────────────────────────────────────────────────────────────────


class V2EXSkill(CollectorSkill):
    """V2EX 采集技能（精简 3 步）。

    流程:
    1. LLM 生成关键词 + 推荐精细节点
    2. 按节点拉话题 → 关键词过滤 → 抓回复
    3. 转 evidence
    """

    _TIME_BUDGET = 35.0

    def can_execute(self, search_terms: list[str], **kwargs) -> bool:
        return True

    def execute(
        self,
        search_terms: list[str],
        product: str = "",
        focus: str = "",
        **kwargs,
    ) -> tuple[list[dict], dict]:
        t_start = time.time()
        print(f"\n{'='*60}")
        print(f"[V2EX Skill] START — product={product}, focus={focus}")
        print(f"[V2EX Skill] time budget: {self._TIME_BUDGET}s")
        print(f"{'='*60}")

        # ── Step 1: LLM 生成关键词 + 推荐节点 ──────────────────────────
        print(f"\n[V2EX Skill] Step 1: generating keywords + nodes via LLM ...")
        keywords, nodes = self._generate_keywords_and_nodes(product, focus, search_terms)
        print(f"[V2EX Skill] keywords ({len(keywords)}): {keywords}")
        print(f"[V2EX Skill] nodes ({len(nodes)}): {nodes}")

        # ── Step 2: 按节点拉话题 → 关键词过滤 → 抓回复 ─────────────────
        print(f"\n[V2EX Skill] Step 2: fetch nodes → keyword filter → fetch replies ...")
        all_topics: list[dict] = []
        seen_ids: set[int] = set()

        # 2a: 拉节点话题
        for node in nodes:
            if time.time() - t_start > self._TIME_BUDGET:
                print("[V2EX Skill] time budget exhausted at node fetch")
                break
            topics = _fetch_topics_by_node(node)
            for t in topics:
                tid = t.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    all_topics.append(t)
            time.sleep(0.3)
        print(f"[V2EX Skill] total unique topics: {len(all_topics)}")

        if not all_topics:
            print("[V2EX Skill] WARNING: 0 topics from all nodes, returning empty")
            return self._empty_result(product, keywords, nodes, t_start)

        # 2b: 关键词过滤
        matched = _keyword_filter(all_topics, keywords)
        if not matched:
            # 关键词 0 匹配 = 该批节点近期没有讨论本产品。旧逻辑此处退化为"取回复
            # 最多的热帖",而 V2EX 永远的热帖是理财/投资 → 把月月宝/年化收益当成竞品
            # 证据灌进来(可灵视频→理财信息的根因)。正确行为:宁可不出 V2EX 证据也不
            # 污染溯源链。直接返回空。
            print(f"[V2EX Skill] keyword filter=0 → 无相关讨论,放弃 top-by-replies 兜底(避免灌入无关热帖)")
            return self._empty_result(product, keywords, nodes, t_start)
        print(f"[V2EX Skill] after filter: {len(matched)} topics")

        # 2c: 抓回复
        topic_replies: dict[int, list[dict]] = {}
        for i, t in enumerate(matched[:MAX_TOPICS_TO_FETCH_REPLIES]):
            if time.time() - t_start > self._TIME_BUDGET - 3:
                print("[V2EX Skill] time budget tight, stopping reply fetch")
                break
            tid = t.get("id")
            if not tid:
                continue
            title = (t.get("title") or "")[:50]
            print(f"  [V2EX] ({i+1}/{min(len(matched), MAX_TOPICS_TO_FETCH_REPLIES)}) replies: {title}")
            replies = _fetch_replies(tid)
            if replies:
                topic_replies[tid] = replies
                print(f"    → {len(replies)} replies")
        print(f"[V2EX Skill] topics with replies: {len(topic_replies)}")

        # ── Step 3: 转换为 raw_evidence ─────────────────────────────────
        print(f"\n[V2EX Skill] Step 3: converting to raw_evidence ...")
        evidences = self._build_evidences(product, focus, matched, topic_replies, keywords)
        print(f"[V2EX Skill] generated {len(evidences)} evidence items")

        claim_type_dist: dict[str, int] = {}
        for ev in evidences:
            ct = ev.get("claim_type", "unknown")
            claim_type_dist[ct] = claim_type_dist.get(ct, 0) + 1
        print(f"[V2EX Skill] claim_type distribution: {claim_type_dist}")

        elapsed = round(time.time() - t_start, 1)
        meta = {
            "skill": "v2ex",
            "keywords_generated": keywords,
            "nodes_used": nodes,
            "topics_fetched": len(all_topics),
            "topics_matched": len(matched),
            "topics_with_replies": len(topic_replies),
            "evidence_count": len(evidences),
            "claim_type_distribution": claim_type_dist,
            "elapsed_seconds": elapsed,
        }
        print(f"\n[V2EX Skill] DONE — {len(evidences)} evidence, {elapsed}s elapsed")
        print(f"{'='*60}\n")
        return evidences, meta

    # ────────────────────────────────────────────────────────────────────────
    # 内部方法
    # ────────────────────────────────────────────────────────────────────────

    def _generate_keywords_and_nodes(
        self, product: str, focus: str, search_terms: list[str]
    ) -> tuple[list[str], list[str]]:
        from .llm import get_llm, is_mock_mode

        if is_mock_mode():
            print("  [V2EX] mock mode — using fallback")
            return self._fallback_keywords(product, focus), ["ide", "create", "qna"]

        llm = get_llm()
        try:
            result = llm.call_json(
                _KEYWORD_GEN_PROMPT,
                {"product": product, "focus": focus, "search_terms": search_terms},
                max_tokens=1024,
                label=f"v2ex_keywords_{product}",
            )
            kws = result.get("keywords", [])
            nodes = result.get("v2ex_nodes", [])
            reasoning = result.get("reasoning", "")
            if kws:
                print(f"  [V2EX] LLM reasoning: {reasoning}")
                return [str(k).strip() for k in kws if k], [str(n).strip().lower() for n in nodes if n]
        except Exception as e:
            print(f"  [V2EX] LLM keyword gen FAILED: {type(e).__name__}: {e}")

        print("  [V2EX] falling back to rule-based")
        return self._fallback_keywords(product, focus), ["ide", "create", "qna"]

    @staticmethod
    def _fallback_keywords(product: str, focus: str) -> list[str]:
        kws = [product]
        if focus:
            kws.append(f"{product} {focus}")
            kws.append(focus)
        kws.extend([f"{product} 体验", f"{product} 好用吗", f"{product} 对比"])
        return kws

    def _build_evidences(self, product, focus, topics, topic_replies, keywords):
        evidences = []
        observed = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for idx, topic in enumerate(topics):
            tid = topic.get("id")
            if not tid:
                continue
            title = topic.get("title", "")
            content = _strip_html_tags(topic.get("content") or "")
            # 相关性硬门:标题/正文都不提产品名的话题一律丢弃(连带其回复一起跳过),
            # 防止节点里的无关热帖(理财/工具分享)被当成本产品证据。
            if not _mentions_product(f"{title} {content}", product):
                continue
            url = f"https://www.v2ex.com/t/{tid}"
            author = topic.get("member", {}).get("username", "") if isinstance(topic.get("member"), dict) else ""

            full_text = f"{title} — {content[:300]}" if content else title
            claim = full_text[:200]
            snippet = f"[V2EX topic by @{author}] {full_text[:500]}"

            evidences.append({
                "evidence_id": _evidence_id(product, f"{url}#{idx}", claim),
                "product": product,
                "claim_type": _infer_claim_type(full_text),
                "source_type": "v2ex",
                "source_bias": "community_feedback",
                "source_url": url,
                "observed_at": observed,
                "source_freshness": "unknown",  # A1: V2EX API 无发布时间，标 unknown
                "claim": claim,
                "extracted_snippet": snippet,
                "source_reliability": scoring_config.reliability("v2ex_topic", 0.68),
                "claim_relevance": self._calc_relevance(full_text, product, focus, keywords),
                "evidence_confidence": 0.60,
                "metadata": {"v2ex_topic_id": tid, "author": author, "replies": topic.get("replies", 0)},
            })

            for ridx, reply in enumerate(topic_replies.get(tid, [])):
                r_text = _strip_html_tags(reply.get("content") or "")
                if len(r_text) < 20:
                    continue
                if not _mentions_product(r_text, product):
                    continue  # 相关性硬门:离题回复丢弃(即便话题相关,回复也可能跑题)
                r_author = reply.get("member", {}).get("username", "") if isinstance(reply.get("member"), dict) else ""
                r_url = f"{url}#reply{reply.get('id', '')}"
                r_claim = r_text[:200]

                evidences.append({
                    "evidence_id": _evidence_id(product, f"{r_url}#{ridx}", r_claim),
                    "product": product,
                    "claim_type": _infer_claim_type(r_text),
                    "source_type": "v2ex",
                    "source_bias": "community_feedback",
                    "source_url": r_url,
                    "observed_at": observed,
                    "source_freshness": "unknown",  # A1: V2EX API 无发布时间
                    "claim": r_claim,
                    "extracted_snippet": f"[V2EX reply by @{r_author}] {r_text[:500]}",
                    "source_reliability": scoring_config.reliability("v2ex_reply", 0.60),
                    "claim_relevance": self._calc_relevance(r_text, product, focus, keywords),
                    "evidence_confidence": 0.50,
                    "metadata": {"v2ex_topic_id": tid, "v2ex_reply_id": reply.get("id"), "author": r_author},
                })

        seen = {}
        for ev in evidences:
            seen[ev["evidence_id"]] = ev
        return list(seen.values())

    @staticmethod
    def _calc_relevance(text, product, focus, keywords):
        lower = text.lower()
        score = 0.40
        if product.lower() in lower:
            score += 0.20
        if focus and focus.lower() in lower:
            score += 0.15
        score += min(sum(1 for kw in keywords if kw.lower() in lower) * 0.05, 0.20)
        return round(min(score, 1.0), 2)

    @staticmethod
    def _empty_result(product, keywords, nodes, t_start):
        return [], {
            "skill": "v2ex",
            "keywords_generated": keywords,
            "nodes_used": nodes,
            "topics_fetched": 0, "topics_matched": 0,
            "evidence_count": 0, "elapsed_seconds": round(time.time() - t_start, 1),
            "warning": "0 topics from all nodes",
        }
