"""Hacker News Skill — Collector 高层技能

流程:
1. 调用 LLM 为每个 product × focus 生成 6-7 个 HN 搜索关键词
2. 通过 HN Algolia API 搜索每个关键词
3. 抓取匹配 story 的评论（取 top N）
4. 整理为 raw_evidence 格式返回

每一步终端留痕，方便排查。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional
from .collector_common import compute_freshness

import httpx

from . import scoring_config
from .skill import CollectorSkill

# ────────────────────────────────────────────────────────────────────────────
# 常量
# ────────────────────────────────────────────────────────────────────────────

_HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
_HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"

# 每个关键词最多取多少 story
MAX_STORIES_PER_KEYWORD = 5
# 每个 story 最多取多少条 top-level 评论
MAX_COMMENTS_PER_STORY = 5
# httpx 超时
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
_HEADERS = {
    "User-Agent": "AICompetitiveRadar/0.1 (+https://github.com/yangyuxin-hub/AI-Competitive-Radar; academic)",
    "Accept": "application/json",
}

# claim_type 推断关键词
_CLAIM_TYPE_KEYWORDS = {
    "pricing": {"price", "pricing", "cost", "free", "paid", "subscription",
                "plan", "$", "per month", "per year", "billing", "tier",
                "enterprise", "pro", "premium", "discount"},
    "performance_quality": {"fast", "slow", "speed", "performance", "latency",
                            "benchmark", "lag", "responsive", "smooth", "buggy",
                            "crash", "stable", "reliable", "accuracy", "quality"},
    "user_pain": {"hate", "terrible", "awful", "broken", "frustrat", "annoying",
                  "sucks", "worst", "complain", "issue", "problem", "pain",
                  "disappoint", "regret", "switch", "abandon", "unusable"},
    "feature_existence": {"feature", "support", "integrate", "plugin", "extension",
                          "api", "capability", "functionality", "mode", "option",
                          "autocomplete", "completion", "chat", "edit", "generate",
                          "refactor", "debug", "test", "deploy"},
}

_KEYWORD_GEN_PROMPT = """\
你是竞品分析关键词生成器。输入可能是中文，但你生成的所有关键词必须是英文——\
Hacker News 是英文社区，中文关键词搜不到任何结果。

规则:
1. 返回 6-7 个英文关键词/短语
2. 混合使用: 产品名(英文)、产品名+功能、行业通用英文术语、竞品对比短语
3. 关键词应该能在 HN 帖子标题或评论中自然出现
4. 全部小写，简洁
5. 如果输入的分析方向是中文，先翻译成英文再造关键词

返回 JSON 格式:
{{"keywords": ["keyword1", "keyword2", ...], "reasoning": "简要说明选择逻辑"}}
"""


# ────────────────────────────────────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────────────────────────────────────


def _evidence_id(product: str, source_url: str, claim: str) -> str:
    """S + sha1[:7] = 8 字符，与 collector.py 保持一致"""
    raw = f"{product}|{source_url}|{claim}".encode("utf-8")
    return "S" + hashlib.sha1(raw).hexdigest()[:7].upper()


def _infer_claim_type(text: str) -> str:
    """根据文本内容推断 claim_type"""
    lower = text.lower()
    scores: dict[str, int] = {}
    for ctype, keywords in _CLAIM_TYPE_KEYWORDS.items():
        scores[ctype] = sum(1 for kw in keywords if kw in lower)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "feature_existence"


def _strip_html(text: str) -> str:
    """简单去除 HTML 标签"""
    return re.sub(r"<[^>]+>", " ", text).strip()


def _is_english_keyword(kw: str) -> bool:
    """检查关键词是否为英文（不含 CJK 字符）"""
    return not re.search(r"[一-鿿㐀-䶿]", kw)


# ────────────────────────────────────────────────────────────────────────────
# HN API 调用
# ────────────────────────────────────────────────────────────────────────────


def _hn_search(keyword: str, tags: str = "story", hits_per_page: int = 10) -> list[dict]:
    """调用 HN Algolia 搜索 API，返回 story 列表"""
    params = {
        "query": keyword,
        "tags": tags,
        "hitsPerPage": hits_per_page,
    }
    print(f"  [HN] searching: '{keyword}' (tags={tags}, limit={hits_per_page})")
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
            r = client.get(_HN_SEARCH_URL, params=params)
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", [])
            print(f"  [HN] '{keyword}': {len(hits)} hits (nbHits={data.get('nbHits', '?')})")
            return hits
    except Exception as e:
        print(f"  [HN] '{keyword}' search FAILED: {type(e).__name__}: {e}")
        return []


def _hn_get_item(item_id: int) -> Optional[dict]:
    """获取 HN item 详情（story 或 comment）"""
    url = _HN_ITEM_URL.format(item_id)
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        print(f"  [HN] item/{item_id} fetch FAILED: {type(e).__name__}: {e}")
        return None


def _fetch_top_comments(story_id: int, limit: int = MAX_COMMENTS_PER_STORY) -> list[dict]:
    """抓取 story 的 top-level 评论"""
    item = _hn_get_item(story_id)
    if not item or not item.get("kids"):
        return []

    comments = []
    for cid in item["kids"][:limit * 2]:  # 多取一些，过滤 deleted/dead
        c = _hn_get_item(cid)
        if not c or c.get("deleted") or c.get("dead") or not c.get("text"):
            continue
        comments.append(c)
        if len(comments) >= limit:
            break
    return comments


# ────────────────────────────────────────────────────────────────────────────
# Skill 主体
# ────────────────────────────────────────────────────────────────────────────


class HNSkill(CollectorSkill):
    """Hacker News 采集技能。

    流程:
    1. LLM 生成关键词 → 2. HN API 搜索 → 3. 抓评论 → 4. 转 raw_evidence
    """

    def can_execute(self, search_terms: list[str], **kwargs) -> bool:
        """HN Skill 始终可用（API 无需认证）"""
        return True

    # 整体时间预算（秒）—— LLM ~8s + 7关键词搜索 ~12s + 评论抓取 ~20s ≈ 40s
    # collector_node 外层需要同步放宽，否则会被 wall-clock 硬杀
    _TIME_BUDGET = 35.0

    def execute(
        self,
        search_terms: list[str],
        product: str = "",
        focus: str = "",
        **kwargs,
    ) -> tuple[list[dict], dict]:
        """执行 HN 采集，返回 (evidences, meta)"""
        t_start = time.time()
        print(f"\n{'='*60}")
        print(f"[HN Skill] START — product={product}, focus={focus}")
        print(f"[HN Skill] search_terms from caller: {search_terms}")
        print(f"[HN Skill] time budget: {self._TIME_BUDGET}s")
        print(f"{'='*60}")

        # ── Step 1: LLM 生成关键词 ──────────────────────────────────────
        print(f"\n[HN Skill] Step 1: generating keywords via LLM ...")
        keywords = self._generate_keywords(product, focus, search_terms)
        print(f"[HN Skill] keywords ({len(keywords)}): {keywords}")

        # ── Step 2: 搜索 HN ─────────────────────────────────────────────
        print(f"\n[HN Skill] Step 2: searching HN for each keyword ...")
        all_stories: list[dict] = []
        seen_ids: set[int] = set()
        for kw in keywords:
            # 时间预算检查
            if time.time() - t_start > self._TIME_BUDGET:
                print("[HN Skill] time budget exhausted at keyword search, stopping early")
                break
            hits = _hn_search(kw, tags="story", hits_per_page=MAX_STORIES_PER_KEYWORD)
            for h in hits:
                sid = h.get("objectID")
                if sid and sid not in seen_ids:
                    seen_ids.add(sid)
                    all_stories.append(h)
            # 限速: 每次搜索间隔 0.3s
            time.sleep(0.3)
        print(f"[HN Skill] total unique stories: {len(all_stories)}")
        if not all_stories:
            print(f"[HN Skill] WARNING: zero stories found for all {len(keywords)} keywords!")
            print(f"  keywords were: {keywords}")
            print(f"  possible causes: product name too niche, keywords not matching HN content")

        # ── Step 3: 抓取评论 ────────────────────────────────────────────
        print(f"\n[HN Skill] Step 3: fetching top comments for stories ...")
        story_comments: dict[int, list[dict]] = {}
        for i, story in enumerate(all_stories[:15]):  # 最多处理 15 个 story
            # 时间预算检查
            if time.time() - t_start > self._TIME_BUDGET:
                print("[HN Skill] time budget exhausted at comment fetch, stopping early")
                break
            sid = story.get("objectID")
            if not sid:
                continue
            title = story.get("title", "")[:60]
            print(f"  [HN] ({i+1}/{min(len(all_stories), 15)}) fetching comments for: {title}")
            comments = _fetch_top_comments(sid, limit=MAX_COMMENTS_PER_STORY)
            if comments:
                story_comments[sid] = comments
                print(f"    → {len(comments)} comments")
            time.sleep(0.2)
        print(f"[HN Skill] stories with comments: {len(story_comments)}")

        # ── Step 4: 转换为 raw_evidence ─────────────────────────────────
        print(f"\n[HN Skill] Step 4: converting to raw_evidence ...")
        evidences = self._build_evidences(product, focus, all_stories, story_comments, keywords)
        print(f"[HN Skill] generated {len(evidences)} evidence items")

        # 统计
        claim_type_dist: dict[str, int] = {}
        for ev in evidences:
            ct = ev.get("claim_type", "unknown")
            claim_type_dist[ct] = claim_type_dist.get(ct, 0) + 1
        print(f"[HN Skill] claim_type distribution: {claim_type_dist}")

        elapsed = round(time.time() - t_start, 1)
        meta = {
            "skill": "hn",
            "keywords_generated": keywords,
            "stories_found": len(all_stories),
            "stories_with_comments": len(story_comments),
            "evidence_count": len(evidences),
            "claim_type_distribution": claim_type_dist,
            "elapsed_seconds": elapsed,
        }
        print(f"\n[HN Skill] DONE — {len(evidences)} evidence, {elapsed}s elapsed")
        print(f"{'='*60}\n")
        return evidences, meta

    # ────────────────────────────────────────────────────────────────────────
    # 内部方法
    # ────────────────────────────────────────────────────────────────────────

    def _generate_keywords(
        self, product: str, focus: str, search_terms: list[str]
    ) -> list[str]:
        """调用 LLM 生成 HN 搜索关键词。失败时 fallback 到基础组合。"""
        from .llm import get_llm, is_mock_mode

        # Mock 模式直接返回组合关键词
        if is_mock_mode():
            print("  [HN] mock mode — using fallback keywords")
            return self._fallback_keywords(product, focus)

        # 构造 prompt
        parts = [f"产品: {product}"]
        if focus:
            parts.append(f"分析方向: {focus}")
        if search_terms:
            parts.append(f"已有搜索词: {', '.join(search_terms)}")
        user_msg = "\n".join(parts)

        llm = get_llm()
        try:
            result = llm.call_json(
                _KEYWORD_GEN_PROMPT,
                {"product": product, "focus": focus, "search_terms": search_terms},
                max_tokens=1024,
                label=f"hn_keywords_{product}",
            )
            kws = result.get("keywords", [])
            reasoning = result.get("reasoning", "")
            if kws:
                print(f"  [HN] LLM reasoning: {reasoning}")
                # 过滤非英文关键词（HN 是英文社区）
                english_kws = [str(k).strip().lower() for k in kws if k and _is_english_keyword(str(k))]
                dropped = len(kws) - len(english_kws)
                if dropped:
                    print(f"  [HN] WARN: dropped {dropped} non-English keywords")
                if english_kws:
                    return english_kws
                print("  [HN] WARN: all LLM keywords were non-English, falling back")
        except Exception as e:
            print(f"  [HN] LLM keyword gen FAILED: {type(e).__name__}: {e}")

        # fallback
        print("  [HN] falling back to rule-based keywords")
        return self._fallback_keywords(product, focus)

    @staticmethod
    def _fallback_keywords(product: str, focus: str) -> list[str]:
        """无 LLM 时的兜底关键词组合。product 一般是英文，focus 可能是中文需要跳过。"""
        kws = [product.lower()]
        if focus and _is_english_keyword(focus):
            kws.append(f"{product.lower()} {focus.lower()}")
            kws.append(focus.lower())
        elif focus:
            print(f"  [HN] fallback: focus '{focus}' is Chinese, skipping in keywords")
        # 通用补充
        kws.extend([
            f"{product.lower()} review",
            f"{product.lower()} vs",
            f"{product.lower()} alternative",
        ])
        return kws

    def _build_evidences(
        self,
        product: str,
        focus: str,
        stories: list[dict],
        story_comments: dict[int, list[dict]],
        keywords: list[str],
    ) -> list[dict]:
        """将 HN story + comments 转换为 raw_evidence 列表"""
        evidences: list[dict] = []
        now = datetime.now(timezone.utc)
        observed = now.strftime("%Y-%m-%d")

        for story in stories:
            sid = story.get("objectID")
            title = story.get("title", "")
            url = story.get("url") or f"https://news.ycombinator.com/item?id={sid}"
            points = story.get("points", 0) or 0
            author = story.get("author", "")
            created = story.get("created_at", "")

            # story 标题作为 evidence
            if title:
                claim = title[:200]
                snippet = f"[HN story by {author}, {points} points] {title}"
                claim_type = _infer_claim_type(title)
                eid = _evidence_id(product, url, claim)
                # A1: freshness 按 HN 发帖时间对 TTL 判定
                published_at = created[:10] if created else None
                evidences.append({
                    "evidence_id": eid,
                    "product": product,
                    "claim_type": claim_type,
                    "source_type": "hacker_news",
                    "source_bias": "community_feedback",
                    "source_url": url,
                    "observed_at": observed,
                    "source_freshness": compute_freshness(published_at, claim_type),
                    "published_at": published_at,
                    "claim": claim,
                    "extracted_snippet": snippet,
                    "source_reliability": scoring_config.reliability("hn_story", 0.70),
                    "claim_relevance": self._calc_relevance(title, product, focus, keywords),
                    "evidence_confidence": min(0.50 + points / 200, 0.85),
                    "metadata": {
                        "hn_story_id": sid,
                        "points": points,
                        "author": author,
                        "created_at": created,
                    },
                })

            # 评论作为 evidence
            for comment in story_comments.get(sid, []):
                ctext = _strip_html(comment.get("text", ""))
                if len(ctext) < 30:
                    continue
                c_author = comment.get("author", "")
                c_url = f"https://news.ycombinator.com/item?id={comment.get('id', sid)}"
                claim = ctext[:200]
                snippet = f"[HN comment by {c_author}] {ctext[:500]}"
                claim_type = _infer_claim_type(ctext)
                eid = _evidence_id(product, c_url, claim)
                # A1: freshness 按评论发布时间对 TTL 判定
                c_created = comment.get("created_at", "")
                c_published_at = c_created[:10] if c_created else None
                evidences.append({
                    "evidence_id": eid,
                    "product": product,
                    "claim_type": claim_type,
                    "source_type": "hacker_news",
                    "source_bias": "community_feedback",
                    "source_url": c_url,
                    "observed_at": observed,
                    "source_freshness": compute_freshness(c_published_at, claim_type),
                    "published_at": c_published_at,
                    "claim": claim,
                    "extracted_snippet": snippet,
                    "source_reliability": scoring_config.reliability("hn_comment", 0.65),
                    "claim_relevance": self._calc_relevance(ctext, product, focus, keywords),
                    "evidence_confidence": 0.55,
                    "metadata": {
                        "hn_parent_story_id": sid,
                        "hn_comment_id": comment.get("id"),
                        "author": c_author,
                    },
                })

        # 去重 (同一 evidence_id 后者覆盖)
        seen: dict[str, dict] = {}
        for ev in evidences:
            seen[ev["evidence_id"]] = ev
        return list(seen.values())

    @staticmethod
    def _calc_relevance(text: str, product: str, focus: str, keywords: list[str]) -> float:
        """根据文本与 product/focus/keywords 的匹配度计算相关性分数"""
        lower = text.lower()
        score = 0.4  # 基线
        if product.lower() in lower:
            score += 0.20
        if focus and focus.lower() in lower:
            score += 0.15
        # 关键词命中
        kw_hits = sum(1 for kw in keywords if kw in lower)
        score += min(kw_hits * 0.05, 0.20)
        return round(min(score, 1.0), 2)
