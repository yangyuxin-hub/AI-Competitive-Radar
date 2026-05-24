# 竞品分析 Agent 协作系统 — 设计文档 v2.2

> 字节跳动 AI 全栈挑战赛 · Topic 3
> 版本：v2.2.1-frozen · 日期：2026-05-24 · 基线：v2.1-frozen
> 修订动因：pre-mortem 发现 3 个 P0 风险(Analyzer 超 token / retry 太紧 / Writer 未设计)
> v2.2.1 收口:Writer/Reviewer 时序、示例冲突、并发 timeout、Reviewer minimal 模式
> 核心原则:**证据覆盖率可控、失败可降级、证据链可复现**
> **变更速览见 §十三**

---

## 一、项目定位

| 维度 | 内容 |
|------|------|
| 目标用户 | 企业产品团队的产品经理 / 数据分析师 |
| 分析目的 | 学习竞品优点,发现功能差距,优化自身产品 |
| Demo 赛道 | AI 编程工具:Cursor vs Windsurf vs GitHub Copilot |
| 示例输入 | "分析 Cursor 和 Windsurf 在代码补全体验上的差距" |
| 示例输出 | 结构化竞品报告(功能对比 + 用户痛点 + 定价 + SWOT + 优先级建议) |
| 扩展性 | 换行业 = 新增 products.yaml 配置,不改代码 |

---

## 二、系统架构

```
用户输入
    ↓
[AgentState 初始化]
    ↓
Collector ──────────────────────────────────────────────┐
  AdapterRegistry (并发, ThreadPoolExecutor)             │
  ├── OfficialPageAdapter  (实时)                        │
  ├── PricingPageAdapter   (实时)                        │
  ├── RedditAdapter        (实时, 需 API Key)            │
  ├── CacheAdapter         (缓存补齐缺失 claim_type)     │
  └── MockAdapter          (Demo 保底)                   │
    ↓ raw_evidence                                        │
Analyzer (两步,v2.2)                                      │
  ├── Step 1 facts:        feature_tree+pricing+persona  │
  │     └─ quick_validate (evidence_id / gap 覆盖)       │
  └── Step 2 derivations:  swot + recommendations        │
        └─ quick_validate (rec 引用 / priority 公式)     │
    ↓ schema_draft                                        │
Writer (v2.2 格式锁定)                                    │
  └── 每条 claim 后缀 [SXXXXXXX] chip                    │
    ↓ report_draft                                        │
Reviewer (R1–R7, R6 默认关)                               │
    ↓                                                     │
  passed  → 输出报告                                      │
  running → 打回对应 Agent (按 target 分桶配额) ─────────┘
  degraded → 降级报告(分层输出)
```

---

## 三、AgentState 定义

```python
from typing import TypedDict, Literal, Optional

class AgentState(TypedDict):
    # 输入
    user_input:    str
    analysis_meta: dict

    # 中间产物
    raw_evidence:    Optional[list[dict]]
    schema_draft:    Optional[dict]
    report_draft:    Optional[str]

    # 质检
    quality_report:  Optional[dict]
    collection_meta: Optional[dict]

    # 打回信息(结构化)
    reject_target:        Optional[Literal["collector", "analyzer", "writer"]]
    reject_requirements:  Optional[list[dict]]   # Collector 精准补证据用

    # 流控 (v2.2: 按 target 分桶,Collector 重试无效给 1 次,Analyzer 推理可改进给 2 次)
    retry_count:            dict[str, int]      # {"collector": 0, "analyzer": 0, "writer": 0}
    max_retries_per_target: dict[str, int]      # {"collector": 1, "analyzer": 2, "writer": 1}
    status: Literal["running", "passed", "degraded", "failed"]
```

---

## 四、竞品知识 Schema v2.1 (字段未变,仅修示例)

### 4.0 analysis_meta

```json
{
  "report_id": "CR-20260524-001",
  "schema_version": "2.1",
  "target_product": "Cursor",
  "competitors": ["Windsurf", "GitHubCopilot"],
  "analysis_focus": ["代码补全体验"],
  "analysis_purpose": "学习竞品优点,优化自身产品",
  "generated_at": "2026-05-24T10:00:00Z",
  "data_cutoff": "2026-05-24",
  "agent_trace_id": "trace_xxx"
}
```

> **命名约定(v2.2)**:`competitors` 与 `products.yaml` key 一致,采用去空格 PascalCase(`GitHubCopilot`);展示名走 `aliases`。否则 AdapterRegistry 查不到。

### 4.1 raw_evidence(Collector 唯一输出)

```json
[
  {
    "evidence_id":        "S3F8A1C2",
    "product":            "Cursor",
    "claim_type":         "feature_existence | pricing | user_pain | performance_quality | market_signal",
    "source_type":        "official_page | official_doc | pricing_page | reddit | producthunt | hn | web_search",
    "source_bias":        "vendor_claim | user_generated | third_party | unknown",
    "source_url":         "https://cursor.com/features",
    "observed_at":        "2026-05-24",
    "source_freshness":   "current | stale | unknown",
    "claim":              "Cursor supports multi-line code completion across files",
    "extracted_snippet":  "Supports multi-line edits and predictions across your codebase...",
    "source_reliability": 0.85,
    "claim_relevance":    0.90,
    "evidence_confidence": 0.77
  }
]
```

**evidence_id 生成(确定性 hash,不用 uuid)**:

```python
import hashlib

def generate_evidence_id(product: str, source_url: str, claim: str) -> str:
    # v2.2: 7 hex (16M 命名空间) + "S" 前缀 = 8 字符,与全文示例 S3F8A1C2 长度一致
    raw = f"{product}|{source_url}|{claim}".encode("utf-8")
    return "S" + hashlib.sha1(raw).hexdigest()[:7].upper()
```

**source_reliability 对照(claim_type × source_bias)**:

| claim_type | vendor_claim | user_generated | third_party | web_search |
|-----------|------|------|------|------|
| feature_existence | 0.85 | 0.60 | 0.75 | 0.60 |
| pricing | 0.90 | 0.40 | 0.70 | 0.55 |
| user_pain | 0.30 | 0.85 | 0.70 | 0.55 |
| performance_quality | 0.50 | 0.75 | 0.75 | 0.60 |
| market_signal | 0.60 | 0.65 | 0.75 | 0.60 |

> source_bias 映射:official_page / official_doc / pricing_page → vendor_claim;reddit / producthunt / hn → user_generated;独立评测站 → third_party;web_search → web_search
>
> **TODO(v2.3)**:此矩阵当前硬编码,跨行业差异大(医疗器械下 vendor_claim 反而最权威)。计划下沉到 `config/scoring/<industry>.yaml`,代码留默认值。

**source_freshness TTL(按 claim_type)**:

```python
FRESHNESS_TTL_DAYS = {
    "pricing":             7,
    "feature_existence":  30,
    "performance_quality": 60,
    "user_pain":           90,
    "market_signal":       30,
}
# TODO(v2.3): 同上,config 化,B2B 企业定价 TTL 可设 365 天
```

### 4.2 feature_tree

```json
{
  "feature_tree": {
    "category": "代码补全",
    "features": [
      {
        "feature_id": "F001",
        "name": "多行补全",
        "products": {
          "Cursor": {
            "support_status": "supported | partially_supported | not_supported | unknown",
            "support_evidence_ids": ["S3F8A1C2"],
            "quality_score": {
              "score": 4,
              "scale": 5,
              "basis": "用户评论中多行补全准确性正反馈较多",
              "aggregation": {
                "aggregation_type": "sampled_evidence",
                "positive_mentions": 18,
                "negative_mentions": 4,
                "neutral_mentions": 8,
                "sample_size": 30,
                "representative_evidence_ids": ["S008C014", "S014D77A"],
                "method": "LLM sentiment classification over 30 collected Reddit comments; representative_evidence_ids only show 2 selected examples (full list omitted to control schema size)"
              },
              "evidence_ids": ["S008C014", "S014D77A"]
            }
          }
        },
        "gap": {
          "winner": "Cursor",
          "gap_type": "accuracy",
          "reason": "Cursor 在跨文件上下文与多行补全上用户正反馈更多",
          "evidence_ids": ["S3F8A1C2", "S008C014", "S021FA22"],
          "confidence": 0.78
        }
      }
    ]
  }
}
```

> **v2.2 规范**(二选一):
> - **完整模式**:`sample_evidence_ids` 列全部 N 个合法 ID,`sample_size == len(sample_evidence_ids)`
> - **代表模式**(Demo 默认,见上例):**不写** `sample_evidence_ids` 字段,只用 `sample_size + representative_evidence_ids + method`,method 字段必须说明全量样本的来源
> 禁止占位字符串(如 `"...共30条"`)和列表长度与 sample_size 不一致 —— R3 会拦截

### 4.3 pricing_model

```json
{
  "pricing_model": {
    "products": [
      {
        "name": "Cursor",
        "tiers": [
          {
            "tier_name": "Pro",
            "billing_cycle": "monthly",
            "price": {"amount": 20, "currency": "USD", "normalized_usd_month": 20},
            "limits": [
              {"limit_name": "code_completion", "limit_value": "unlimited", "unit": "requests_per_month"}
            ],
            "display_limits": "无限补全",
            "observed_at": "2026-05-24",
            "source_freshness": "current",
            "evidence_ids": ["S041AAAA"]
          }
        ]
      }
    ],
    "pricing_gap": {
      "target_position": "similar | cheaper | more_expensive | unknown",
      "summary": "三者 Pro 档价格相近,差异主要在免费额度",
      "evidence_ids": ["S041AAAA", "S051BBBB", "S061CCCC"],
      "confidence": 0.82
    }
  }
}
```

### 4.4 user_persona

```json
{
  "user_persona": {
    "user_segments": [
      {
        "segment_id": "U001",
        "name": "独立开发者",
        "description": "个人开发者,用于日常编码、项目重构和快速原型开发",
        "evidence_ids": ["S081DDDD"],
        "confidence": 0.76
      }
    ],
    "pain_points": [
      {
        "pain_id": "P001",
        "description": "补全上下文理解不够深,跨文件引用错误",
        "frequency": {
          "level": "high",
          "count": "30条评论中15条提及",
          "sample_size": 30,
          "evidence_ids": ["S033EEEE", "S034FFFF", "S0350001"]
        },
        "affected_products": ["GitHubCopilot"],
        "affected_segments": ["U001"],
        "user_expectation": "补全结果应理解当前项目的跨文件依赖",
        "confidence": 0.80
      }
    ]
  }
}
```

### 4.5 recommendations

```json
{
  "recommendations": [
    {
      "rec_id": "R001",
      "action": "优化跨文件上下文召回策略,提升多行补全准确性",
      "rationale": "该问题同时出现在功能差距与用户痛点中",
      "source_feature_ids": ["F001"],
      "source_pain_ids": ["P001"],
      "evidence_ids": ["S008C014", "S033EEEE", "S034FFFF"],
      "priority_score": {
        "pain_frequency": 5,
        "business_impact": 4,
        "implementation_feasibility": 3,
        "evidence_confidence": 4,
        "weights": {
          "pain_frequency": 0.35,
          "business_impact": 0.30,
          "implementation_feasibility": 0.20,
          "evidence_confidence": 0.15
        },
        "final_score": 4.15,
        "priority": "P1"
      }
    }
  ]
}
```

**优先级映射**:

```
P0: final_score >= 4.2
P1: 3.4 – 4.19
P2: 2.6 – 3.39
P3: < 2.6
```

> TODO(v2.3): 阈值与权重 config 化

### 4.6 swot(辅助参考)

```json
{
  "swot": {
    "target": "Cursor",
    "note": "核心结论以 feature_gap 和 recommendations 为准",
    "strengths":     [{"point": "多行补全质量较强", "evidence_ids": ["S3F8A1C2"], "confidence": 0.78}],
    "weaknesses":    [{"point": "价格偏高",         "evidence_ids": ["S041AAAA"], "confidence": 0.70}],
    "opportunities": [{"point": "企业采购市场空白",  "evidence_ids": ["S071GGGG"], "confidence": 0.60}],
    "threats":       [{"point": "Windsurf 响应速度追近", "evidence_ids": ["S021FA22"], "confidence": 0.65}]
  }
}
```

---

## 五、Collector v2.3

### 5.1 Source Adapter 接口

```python
from abc import ABC, abstractmethod

class SourceAdapter(ABC):
    @abstractmethod
    def fetch(self, product: str, focus: str) -> list[RawEvidence]: ...

    @abstractmethod
    def can_fetch(self, product: str) -> bool: ...
```

### 5.2 产品配置(config/products.yaml)

```yaml
products:
  Cursor:
    aliases: ["Cursor AI", "Cursor editor"]
    official_pages:  [https://cursor.com/features]
    pricing_pages:   [https://cursor.com/pricing]
  Windsurf:
    aliases: ["Windsurf", "Codeium Windsurf"]
    official_pages:  [https://codeium.com/windsurf]
    pricing_pages:   [https://codeium.com/pricing]
  GitHubCopilot:
    aliases: ["GitHub Copilot", "Copilot"]
    official_pages:  [https://github.com/features/copilot]
    pricing_pages:   [https://github.com/features/copilot/plans]
```

### 5.3 AdapterRegistry

```python
REQUIRED_CLAIM_TYPES = {"feature_existence", "performance_quality", "pricing", "user_pain"}

class AdapterRegistry:
    def __init__(self):
        self.live_adapters = [OfficialPageAdapter(), PricingPageAdapter(), RedditAdapter()]
        self.cache = CacheAdapter()
        self.mock  = MockAdapter()

    def fetch_all(self, product: str, focus: str) -> tuple[list[RawEvidence], dict]:
        all_evidences, adapter_events = [], []

        # 第一层:实时抓取
        for adapter in self.live_adapters:
            if not adapter.can_fetch(product):
                continue
            try:
                evs = adapter.fetch(product, focus)
                all_evidences.extend(evs)
                self.cache.save(product, evs)
                adapter_events.append({"adapter": type(adapter).__name__,
                                       "status": "success", "count": len(evs)})
            except FetchError as e:
                adapter_events.append({"adapter": type(adapter).__name__,
                                       "status": "failed", "reason": str(e), "fallback": "cache"})

        # 第二层:缓存补齐缺失 claim_type
        missing = REQUIRED_CLAIM_TYPES - {e.claim_type for e in all_evidences}
        if missing and self.cache.can_fetch(product):
            cached = self.cache.fetch(product, focus)
            all_evidences.extend(e for e in cached if e.claim_type in missing)

        # 第三层:Mock 补齐仍缺失的 claim_type
        still_missing = REQUIRED_CLAIM_TYPES - {e.claim_type for e in all_evidences}
        if still_missing and self.mock.can_fetch(product):
            mock_evs = self.mock.fetch(product, focus)
            all_evidences.extend(e for e in mock_evs if e.claim_type in still_missing)

        # v2.2: dedupe 只在此处做一次,避免 coverage 虚高
        all_evidences = dedupe_evidence(all_evidences)
        coverage = {ct: sum(1 for e in all_evidences if e.claim_type == ct)
                    for ct in REQUIRED_CLAIM_TYPES}
        return all_evidences, {"adapter_events": adapter_events, "coverage": coverage}
```

### 5.4 CacheAdapter(merge 写入)

```python
class CacheAdapter(SourceAdapter):
    def can_fetch(self, product: str) -> bool:
        return self._cache_path(product).exists()

    def save(self, product: str, evidences: list[RawEvidence]):
        path = self._cache_path(product)
        old = self._load(path)
        merged = {ev.evidence_id: ev for ev in old}
        for ev in evidences:
            merged[ev.evidence_id] = ev          # 同 ID 用新数据覆盖
        self._dump(path, list(merged.values()))

    def fetch(self, product: str, focus: str) -> list[RawEvidence]:
        evidences = self._load(self._cache_path(product))
        for ev in evidences:
            days_old = (date.today() - date.fromisoformat(ev.observed_at)).days
            ttl = FRESHNESS_TTL_DAYS.get(ev.claim_type, 30)
            ev.source_freshness = "current" if days_old < ttl else "stale"
        # score_relevance: jieba 分词 + TF-IDF cosine 或 BM25,阈值 0.3
        return [ev for ev in evidences if score_relevance(ev.extracted_snippet, focus) > 0.3]
```

### 5.5 Collector 节点(v2.2: 多产品并发)

```python
from concurrent.futures import ThreadPoolExecutor, wait

def collector_node(state: AgentState) -> AgentState:
    """v2.2: 3 产品并发抓取,演示从 30s+ → ~10s
       v2.2.1: 真正生效的 wall-clock timeout — wait(timeout) 替代 as_completed+fut.result(timeout)
       注意: 主要靠每个 Adapter 内部 HTTP timeout(httpx 15s)兜底,这里只是最后防线"""
    meta     = state["analysis_meta"]
    products = [meta["target_product"]] + meta["competitors"]
    focus    = meta["analysis_focus"][0]

    fetched, collection_meta = [], {"products": {}}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(registry.fetch_all, p, focus): p for p in products}
        # 整体最多等 25 秒(单产品理论上 15s HTTP timeout + 余量)
        done, not_done = wait(futures.keys(), timeout=25)

        for fut in done:
            product = futures[fut]
            try:
                evs, meta_info = fut.result()
            except Exception as e:
                evs, meta_info = [], {"adapter_events": [{"status": "fatal", "reason": str(e)}],
                                      "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES}}
            fetched.extend(evs)
            collection_meta["products"][product] = meta_info

        for fut in not_done:
            product = futures[fut]
            fut.cancel()    # 注意: 已运行的线程不会真停,只阻止启动;HTTP 层 timeout 才是真正保障
            collection_meta["products"][product] = {
                "adapter_events": [{"status": "timeout", "reason": "wall-clock 25s exceeded"}],
                "coverage": {ct: 0 for ct in REQUIRED_CLAIM_TYPES}
            }

    # 被打回时:按结构化 requirements 精准补证据
    if state.get("reject_requirements"):
        merged = patch_by_requirements(
            existing     = state.get("raw_evidence", []),
            new          = fetched,
            requirements = state["reject_requirements"]
        )
    else:
        merged = fetched

    merged = dedupe_evidence(merged)   # patch 合并后兜底

    return {
        **state,
        "raw_evidence":        [asdict(e) if not isinstance(e, dict) else e for e in merged],
        "collection_meta":     collection_meta,
        "reject_requirements": None,
        "reject_target":       None
    }
```

### 5.6 补丁函数

```python
def patch_by_requirements(existing, new, requirements) -> list:
    existing_ids = {e["evidence_id"] if isinstance(e, dict) else e.evidence_id
                    for e in existing}
    # 展开所有 required_claim_types(不只取第一个)
    needed_types = {ct for r in requirements
                    if r.get("reject_target") == "collector"
                    for ct in r.get("required_claim_types", [])}
    patches = [e for e in new
               if (e["evidence_id"] if isinstance(e, dict) else e.evidence_id) not in existing_ids
               and (e["claim_type"] if isinstance(e, dict) else e.claim_type) in needed_types]
    return existing + patches

def dedupe_evidence(evidences) -> list:
    merged = {}
    for ev in evidences:
        eid = ev["evidence_id"] if isinstance(ev, dict) else ev.evidence_id
        merged[eid] = ev
    return list(merged.values())
```

---

## 六、Analyzer v1.0(v2.2 新增,两步式)

### 6.1 为什么拆两步

| 风险 | 现象 |
|------|------|
| **超 token** | 单次调用 5K input + 6-8K output,Doubao-Seed-Lite 的 max_tokens=4096 几乎必截断 |
| **推理污染事实** | facts 和 derivations 同时生成时,LLM 会为了让结论自洽倒推事实(幻觉重灾区) |
| **R4 链路模糊** | rec → feature/pain 的引用关系在单次输出里很难自校验 |

### 6.2 节点实现

```python
def analyzer_node(state: AgentState) -> AgentState:
    evidence = state["raw_evidence"]
    meta     = state["analysis_meta"]

    # ─── Step 1: 事实层(无推理) ───────────────────────────
    facts_prompt = build_facts_prompt(evidence, meta)
    facts = llm_call_json(facts_prompt)             # feature_tree + pricing_model + user_persona

    issues = quick_validate_facts(facts, evidence, meta)
    if issues:                                       # 本地一次性自修复
        facts = llm_call_json(facts_prompt + repair_hint(issues))

    # ─── Step 2: 推导层(以 facts 为输入) ──────────────────
    der_prompt = build_derivations_prompt(facts, evidence, meta)
    derivations = llm_call_json(der_prompt)         # swot + recommendations

    issues = quick_validate_derivations(derivations, facts, evidence)
    if issues:
        derivations = llm_call_json(der_prompt + repair_hint(issues))

    schema_draft = {"analysis_meta": meta, **facts, **derivations}
    return {**state, "schema_draft": schema_draft}
```

### 6.3 quick_validate(Analyzer 自校验,廉价机械,避免浪费 Reviewer retry)

```python
def quick_validate_facts(facts: dict, evidence: list[dict], meta: dict) -> list[str]:
    """v2.2.1: meta 显式传入,target/competitors 从 analysis_meta 取而不是塞进 facts"""
    valid_ids = {e["evidence_id"] for e in evidence}
    issues = []

    # (a) evidence_id 必须存在
    for path, eids, _ in collect_all_evidence_refs(facts):
        for eid in eids:
            if eid not in valid_ids:
                issues.append(f"{path}: 引用了不存在的 evidence_id {eid}")

    # (b) v2.2 新增 P1.5: feature gap 必须覆盖 target + ≥1 competitor
    target     = meta["target_product"]
    competitors = set(meta["competitors"])
    for feat in facts.get("feature_tree", {}).get("features", []):
        covered = set(feat.get("products", {}).keys())
        if target not in covered:
            issues.append(f"feature {feat.get('feature_id')}: 未覆盖 target product {target}")
        if not (covered & competitors):
            issues.append(f"feature {feat.get('feature_id')}: 未覆盖任何 competitor(competitors={competitors}, got={covered})")

    return issues

def quick_validate_derivations(derivations: dict, facts: dict, evidence: list[dict]) -> list[str]:
    issues = []
    valid_fids = {f["feature_id"] for f in facts.get("feature_tree", {}).get("features", [])}
    valid_pids = {p["pain_id"]    for p in facts.get("user_persona", {}).get("pain_points", [])}

    for rec in derivations.get("recommendations", []):
        # (c) 每条 rec 至少引用 1 feature 或 1 pain
        fids = set(rec.get("source_feature_ids", []))
        pids = set(rec.get("source_pain_ids", []))
        if not (fids & valid_fids) and not (pids & valid_pids):
            issues.append(f"{rec['rec_id']}: 未引用任何有效 feature/pain")

        # (d) priority 必须由公式计算,不能手填
        ps = rec.get("priority_score", {})
        weights = ps.get("weights", {})
        if weights and "final_score" in ps:
            expected = sum(ps.get(k, 0) * w for k, w in weights.items())
            if abs(expected - ps["final_score"]) > 0.01:
                issues.append(f"{rec['rec_id']}: priority final_score={ps['final_score']} 与公式={expected:.2f} 不一致")

    return issues
```

### 6.4 Analyzer Prompt 硬约束(写进 system prompt)

```text
1. 只能引用 raw_evidence 中存在的 evidence_id,禁止编造 ID
2. 事实性结论只能基于 extracted_snippet 中明确出现的信息;建议性结论必须同时引用
   source_feature_ids 或 source_pain_ids,并在 rationale 中说明推导关系
3. feature gap 必须覆盖 target + 至少 1 个 competitor
4. 每个 recommendation 必须至少引用 1 个 feature_id 或 1 个 pain_id
5. priority_score 必须按加权公式计算,禁止手写 priority 字段
6. 证据不足时输出 support_status: "unknown",不强行补结论
7. sampled_evidence 模式下,aggregation.sample_size 必须等于 sample_evidence_ids 的实际长度;
   若不记录全量 sample_evidence_ids,则必须填写 aggregation_method 说明采样来源
```

---

## 七、Writer v1.0(v2.2 新增,格式锁定)

### 7.1 输出规范

每条 claim 句末追加 `[SXXXXXXX]` 标记(8 字符 evidence_id),前端渲染为可点击 chip → 跳转 raw_evidence 详情。这是评分项"信息溯源完整"的载体。

> **v2.2.1 时序约束**:Writer 在 Reviewer **之前**运行,所以 **Writer 输出禁止包含 `quality_score / quality_report` 字段**。质检结果由前端从 `state.quality_report` 单独渲染(右上角徽章),不进 Markdown 正文。降级报告由 `degraded_writer_node`(在 Reviewer 之后)单独生成。

### 7.2 示例输出

```markdown
# Cursor vs Windsurf vs GitHubCopilot — 代码补全体验竞品报告

> 报告 ID: CR-20260524-001 · 数据截止: 2026-05-24
> (质检评分由前端从 quality_report 单独渲染,不在正文)

## 一、功能差距(F001 多行补全)

Cursor 在跨文件多行补全上表现优于 Windsurf [S3F8A1C2][S008C014],
基于 30 条社区评论的情感分析(正面 18 / 负面 4 / 中性 8 [S014D77A])。

| 产品 | 支持状态 | 质量评分 | 关键证据 |
|------|----------|----------|----------|
| Cursor   | ✅ 完全支持 | 4/5 | [S3F8A1C2] |
| Windsurf | ⚠️ 部分支持 | 3/5 | [S021FA22] |
| GitHubCopilot | ✅ 完全支持 | 3/5 | [S033EEEE] |

## 二、用户痛点(P001)

> 补全上下文理解不够深,跨文件引用错误 — 30 条评论中 15 条提及 [S033EEEE][S034FFFF]

## 三、改进建议(R001 · P1)

**优化跨文件上下文召回策略,提升多行补全准确性** [S008C014][S033EEEE]
- 源功能差距: F001 多行补全
- 源用户痛点: P001 上下文理解不深
- 优先级评分: 4.15(P1)
```

### 7.3 渲染器骨架

```python
def writer_node(state: AgentState) -> AgentState:
    schema = state["schema_draft"]
    sections = [
        render_header(schema["analysis_meta"], state.get("quality_report")),
        render_feature_gaps(schema["feature_tree"]),
        render_pricing(schema["pricing_model"]),
        render_personas(schema["user_persona"]),
        render_recommendations(schema["recommendations"]),
        render_swot(schema["swot"]),
    ]
    return {**state, "report_draft": "\n\n".join(sections)}

def cite(evidence_ids: list[str]) -> str:
    """渲染 evidence chip,前端识别 \[SXXXXXXX\] 模式"""
    return "".join(f"[{eid}]" for eid in evidence_ids)
```

---

## 八、Reviewer v2.2

### 8.0 运行模式(v2.2.1 新增,**Demo 默认 minimal**)

Reviewer 拆成 **hard gate**(必须通过,否则打回)和 **soft scoring**(只降 confidence/打 warning)两层。Demo 演示默认 minimal,Week 3 答辩或全量评测时切 full。

| 模式 | hard gate(error) | soft scoring(warning) | 关闭 |
|------|------------------|----------------------|------|
| **minimal**(默认) | R1 引用完整 / R4 推理链 / R5 结构冲突 | R2 / R3 / R7 | R6 |
| **full**(答辩) | R1-R5 | R7 | R6 仅在 R1-R5 全过后跑 1 次 |

```python
REVIEWER_MODE = os.environ.get("REVIEWER_MODE", "minimal")   # "minimal" | "full"

MODE_CONFIG = {
    "minimal": {"hard_gate": {"R1","R4","R5"},  "soft": {"R2","R3","R7"}, "llm": False},
    "full":    {"hard_gate": {"R1","R2","R3","R4","R5"}, "soft": {"R7"},  "llm": True},
}
```

**答辩话术**:"Reviewer 默认不全开。我们把规则分成 hard gate 和 soft scoring —— hard gate 只保证证据链不断、推荐可追溯、结构无冲突;其他规则只作为风险提示和置信度降权。这样既给了 LLM 自主空间,又保住了核心可信度。"

### 8.1 规则体系

```python
REVIEWER_RULES = {
    "R1": "evidence_reference_integrity",  # evidence_id 非空且存在于 raw_evidence
    "R2": "claim_type_compatibility",      # claim_type 与使用位置匹配
    "R3": "aggregation_integrity",         # 聚合数字、样本量、方法自洽
    "R4": "reasoning_chain_integrity",     # rec → feature/pain/evidence 链路完整
    "R5": "structured_contradiction",      # 结构化字段冲突检测
    "R6": "semantic_grounding",            # LLM judge 语义一致性(v2.2: 默认关,仅终轮启用)
    "R7": "freshness_and_confidence",      # 时效性降权(仅 warning)
}
```

### 8.2 issue_type → reject_target 映射

```python
ISSUE_TYPE_TO_TARGET = {
    "missing_evidence_ids":       "analyzer",
    "evidence_id_not_found":      "collector",
    "malformed_evidence":         "collector",
    "invalid_evidence_usage":     "analyzer",
    "claim_type_mismatch":        "analyzer",
    "quality_only_official":      "collector",   # warning,不打回
    "aggregation_sum_overflow":   "analyzer",
    "aggregation_method_missing": "analyzer",
    "broken_reasoning_chain":     "analyzer",
    "evidence_ref_broken":        "collector",
    "structured_contradiction":   "analyzer",
    "semantic_grounding_fail":    "analyzer",
    "bad_report_format":          "writer",
}
# TODO(v2.3): 13 项过细,跨行业易盲区;计划收敛到 3 类(evidence/logic/format)
```

### 8.3 severity 策略

| 问题类型 | 级别 | 处理 |
|---------|------|------|
| evidence_id 缺失 / 不存在 | error | 打回 Analyzer / Collector |
| malformed_evidence | error | 打回 Collector |
| claim_type 不匹配 | error | 打回 Analyzer |
| quality_score 只有官网 | **warning** | 前端展示,不打回 |
| aggregation 溢出 / method 缺失 | error | 打回 Analyzer |
| reasoning chain 断裂 | error | 打回 Analyzer |
| 结构化冲突 / 语义校验 fail | error | 打回 Analyzer |
| 语义校验 weak / freshness stale | warning | 降低 confidence/score |

### 8.4 collect_all_evidence_refs(统一遍历,含 user_segments)

```python
def collect_all_evidence_refs(schema: dict) -> list[tuple[str, list[str], list[str]]]:
    refs = []

    # feature_tree
    for f in schema.get("feature_tree", {}).get("features", []):
        fid = f["feature_id"]
        for product, data in f.get("products", {}).items():
            refs.append((f"feature_tree.{fid}.{product}.support_evidence_ids",
                         data.get("support_evidence_ids", []), ["feature_existence"]))
            refs.append((f"feature_tree.{fid}.{product}.quality_score.evidence_ids",
                         data.get("quality_score", {}).get("evidence_ids", []),
                         ["performance_quality", "user_pain"]))
        refs.append((f"feature_tree.{fid}.gap.evidence_ids",
                     f.get("gap", {}).get("evidence_ids", []),
                     ["feature_existence", "performance_quality", "user_pain"]))

    # pricing_model
    for product in schema.get("pricing_model", {}).get("products", []):
        for i, tier in enumerate(product.get("tiers", [])):
            refs.append((f"pricing_model.{product['name']}.tiers[{i}].evidence_ids",
                         tier.get("evidence_ids", []), ["pricing"]))
    refs.append(("pricing_model.pricing_gap.evidence_ids",
                 schema.get("pricing_model", {}).get("pricing_gap", {}).get("evidence_ids", []),
                 ["pricing", "market_signal"]))

    # user_persona
    for p in schema.get("user_persona", {}).get("pain_points", []):
        refs.append((f"user_persona.{p['pain_id']}.frequency.evidence_ids",
                     p.get("frequency", {}).get("evidence_ids", []),
                     ["user_pain", "performance_quality"]))
    for u in schema.get("user_persona", {}).get("user_segments", []):
        refs.append((f"user_persona.user_segments.{u['segment_id']}.evidence_ids",
                     u.get("evidence_ids", []),
                     ["user_pain", "market_signal", "performance_quality"]))

    # recommendations
    for r in schema.get("recommendations", []):
        refs.append((f"recommendations.{r['rec_id']}.evidence_ids",
                     r.get("evidence_ids", []),
                     ["feature_existence","user_pain","performance_quality","pricing","market_signal"]))

    # swot
    for dim in ["strengths", "weaknesses", "opportunities", "threats"]:
        for i, item in enumerate(schema.get("swot", {}).get(dim, [])):
            refs.append((f"swot.{dim}[{i}].evidence_ids",
                         item.get("evidence_ids", []),
                         ["feature_existence","pricing","user_pain","performance_quality","market_signal"]))

    return refs
```

### 8.5 Reviewer 节点(v2.2: 按 target 分桶 + R6 单次)

```python
# v2.2.1: mode-driven. minimal 模式 hard_gate={R1,R4,R5}, R6 关闭
#         full 模式 hard_gate=R1-R5, R6 仅在结构通过后跑一次

RULE_RUNNERS = {
    "R1": lambda s, ev: check_evidence_reference_integrity(s, ev),
    "R2": lambda s, ev: check_claim_type_compatibility(s),
    "R3": lambda s, ev: check_aggregation_integrity(s),
    "R4": lambda s, ev: check_reasoning_chain(s),
    "R5": lambda s, ev: check_structured_contradiction(s),
    "R7": lambda s, ev: check_freshness_and_confidence(s),
}

def make_reviewer_node(llm, mode: str = "minimal"):
    cfg = MODE_CONFIG[mode]

    def reviewer_node(state: AgentState) -> AgentState:
        schema   = state["schema_draft"]
        evidence = state["raw_evidence"]

        all_issues = []
        for rule_id, runner in RULE_RUNNERS.items():
            issues = runner(schema, evidence)
            # 按 mode 重写 severity: hard_gate → error, soft → warning, 其他丢弃
            for i in issues:
                if rule_id in cfg["hard_gate"]:
                    i["severity"] = "error"
                    all_issues.append(i)
                elif rule_id in cfg["soft"]:
                    i["severity"] = "warning"
                    all_issues.append(i)

        errors_pre = [i for i in all_issues if i["severity"] == "error"]

        # R6: 仅在 full 模式 + 结构错全清后跑一次(避免每 retry 都打 10-15 次 LLM)
        if cfg["llm"] and not errors_pre:
            all_issues += check_semantic_grounding(schema, llm)

        errors   = [i for i in all_issues if i["severity"] == "error"]
        warnings = [i for i in all_issues if i["severity"] == "warning"]

        all_rule_ids  = set(REVIEWER_RULES.keys())
        failed_rules  = {i["rule"] for i in errors}
        warning_rules = {i["rule"] for i in warnings}
        passed_rules  = sorted(all_rule_ids - failed_rules - warning_rules)

        # v2.2: module_status 用 startswith 而非子串匹配,避免误匹配
        module_status = {}
        for module in ["raw_evidence","feature_tree","pricing_model",
                        "user_persona","recommendations","swot"]:
            errs = [i for i in errors   if i["location"].startswith(module)]
            wrns = [i for i in warnings if i["location"].startswith(module)]
            module_status[module] = "failed" if errs else "warning" if wrns else "passed"

        quality_score = max(0, 100 - len(errors) * 10 - len(warnings) * 3)

        quality_report = {
            "quality_score":  quality_score,
            "passed_rules":   passed_rules,
            "failed_rules":   sorted(failed_rules),
            "warning_rules":  sorted(warning_rules),
            "module_status":  module_status,
            "errors":         errors,
            "warnings":       warnings,
        }

        if not errors:
            return {**state, "quality_report": quality_report,
                    "status": "passed", "reject_target": None}

        # ─── v2.2: 按 target 分桶配额 ────────────────────────
        from collections import Counter
        target_counts = Counter(i["reject_target"] for i in errors)
        priority = {"collector": 2, "analyzer": 1, "writer": 0}
        target = max(target_counts, key=lambda t: (target_counts[t], priority.get(t, 0)))

        retry_count = state["retry_count"]
        max_per     = state["max_retries_per_target"]

        if retry_count.get(target, 0) >= max_per.get(target, 0):
            # 该方向预算已用完 → 直接降级(不切换 target,避免无限绕)
            return {**state, "quality_report": quality_report,
                    "status": "degraded", "reject_target": None}

        new_retry = {**retry_count, target: retry_count.get(target, 0) + 1}

        reject_requirements = [
            {"rule": i["rule"], "issue_type": i["issue_type"],
             "location": i["location"],
             "required_claim_types": i.get("required_claim_types", []),
             "reject_target": i["reject_target"]}
            for i in errors if i["reject_target"] == "collector"
        ]

        return {
            **state,
            "quality_report":      quality_report,
            "retry_count":         new_retry,
            "reject_target":       target,
            "reject_requirements": reject_requirements or None,
            "status":              "running"
        }
    return reviewer_node
```

### 8.6 降级报告节点

```python
def degraded_writer_node(state: AgentState) -> AgentState:
    qr      = state["quality_report"]
    passed  = [m for m, s in qr["module_status"].items() if s == "passed"]
    warning = [m for m, s in qr["module_status"].items() if s == "warning"]
    failed  = [m for m, s in qr["module_status"].items() if s == "failed"]

    actions = {
        "collector": "补充用户侧证据来源(Reddit / ProductHunt)",
        "analyzer":  "修正证据引用关系与结论推理链",
        "writer":    "修正报告格式与引用标注"
    }
    needed = list({i["reject_target"] for i in qr["errors"]})

    report = f"""# 竞品分析报告(部分置信)

> 质检评分: {qr['quality_score']}/100 · 按 target 分桶配额耗尽后降级输出
> 重试明细: {state['retry_count']}

## 质检状态
- ✅ 通过: {', '.join(passed) or '无'}
- ⚠️ 存疑: {', '.join(warning) or '无'}
- ❌ 失败: {', '.join(failed) or '无'}

## 建议补充动作
{chr(10).join(f'- {actions[t]}' for t in needed if t in actions)}

---

## 可参考结论(通过质检模块)

{state.get('report_draft', '')}

---

## 不建议直接采纳的结论

{chr(10).join(f'- [{i["location"]}] {i["issue_type"]}: {i.get("detail", "")}' for i in qr["errors"])}
"""
    return {**state, "report_draft": report, "status": "degraded"}
```

> **v2.2 字段修正**:此前用 `i["issue"]`,但 issue 字典实际字段为 `rule / issue_type / location / detail`,统一为 `issue_type + detail`。

---

## 九、LangGraph 编排

```python
reviewer_node = make_reviewer_node(llm, mode=os.environ.get("REVIEWER_MODE", "minimal"))

graph = StateGraph(AgentState)
graph.add_node("collector",       collector_node)
graph.add_node("analyzer",        analyzer_node)
graph.add_node("writer",          writer_node)
graph.add_node("reviewer",        reviewer_node)
graph.add_node("degraded_writer", degraded_writer_node)

graph.set_entry_point("collector")
graph.add_edge("collector", "analyzer")
graph.add_edge("analyzer",  "writer")
graph.add_edge("writer",    "reviewer")

def route_after_review(state: AgentState) -> str:
    if state["status"] == "passed":   return "end"
    if state["status"] == "degraded": return "degraded_writer"
    return state["reject_target"] or "analyzer"   # 配额检查已在 Reviewer 内做完

graph.add_conditional_edges(
    "reviewer", route_after_review,
    {"collector": "collector", "analyzer": "analyzer",
     "writer": "writer", "degraded_writer": "degraded_writer", "end": END}
)
graph.add_edge("degraded_writer", END)

app = graph.compile()
```

### 初始 state(注意 retry 字段)

```python
initial_state = {
    "user_input": "...",
    "analysis_meta": {...},
    "retry_count": {"collector": 0, "analyzer": 0, "writer": 0},
    "max_retries_per_target": {"collector": 1, "analyzer": 2, "writer": 1},
    "status": "running",
    # 其余字段 None
}
```

---

## 十、可观测性与客户端配置

### 10.1 LangSmith + JSONL

```python
import os
from functools import wraps
from datetime import datetime

os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"]    = "your_key"
os.environ["LANGCHAIN_PROJECT"]    = "competitive-analysis-agent"

def observable_agent(agent_name: str):
    def decorator(func):
        @wraps(func)
        def wrapper(state):
            log_agent_event(agent_name, "start", {"retry_count": state.get("retry_count", {})})
            start = datetime.now()
            try:
                result = func(state)
                duration_ms = int((datetime.now() - start).total_seconds() * 1000)
                log_agent_event(agent_name, "end",
                                {"duration_ms": duration_ms,
                                 "status": result.get("status", "running")})
                return result
            except Exception as e:
                log_agent_event(agent_name, "error", {"error": str(e)})
                raise
        return wrapper
    return decorator
```

### 10.2 客户端 timeout(v2.2 新增)

```python
import httpx
from openai import OpenAI

# HTTP 抓取: 短 timeout, 失败快速降级到 cache
http_client = httpx.Client(
    timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
    follow_redirects=True,
)

# ARK / Doubao: LLM 长 schema 输出可能 40-60s,timeout 给足
ark_client = OpenAI(
    api_key=os.environ["ARK_API_KEY"],
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    timeout=90.0,
    max_retries=2,
)
```

### 10.3 日志格式(agent_trace.jsonl)

```jsonl
{"timestamp":"2026-05-24T10:00:01","agent":"collector","event":"start","data":{"retry_count":{"collector":0,"analyzer":0,"writer":0}}}
{"timestamp":"2026-05-24T10:00:03","agent":"collector","event":"fetch_fallback","data":{"product":"Cursor","tier":"cache"}}
{"timestamp":"2026-05-24T10:00:05","agent":"analyzer","event":"step1_facts_ok","data":{"duration_ms":3200,"feature_count":4}}
{"timestamp":"2026-05-24T10:00:09","agent":"analyzer","event":"step2_derivations_ok","data":{"duration_ms":3800,"rec_count":3}}
{"timestamp":"2026-05-24T10:00:10","agent":"reviewer","event":"end","data":{"status":"passed","quality_score":92}}
```

---

## 十一、评分对照

| 评分维度 | 权重 | 覆盖 |
|---------|------|------|
| 多 Agent 协作与输出可信度 | 35% | 四 Agent 分工 · DAG · 结构化消息 · R1-R7 闭环 · Schema 完整 · 溯源完整 |
| 技术深度与工程完整度 | 25% | 端到端链路 · LangSmith+JSONL · 引用强制幻觉抑制 · 三层降级 · Analyzer 两步 |
| 业务价值与产品体验 | 20% | 产品经理场景 · 可换行业 · 降级报告产品化(**Week 3 补量化指标**) |
| 代码质量与文档 | 10% | 模块化 · 配置化 · README · **TRAE 协作痕迹(Week 2 补)** |
| 合规、材料与答辩 | 10% | robots.txt 白名单 · source_bias 标注 |

---

## 十二、开发周期(Demo 优先版)

| 时间 | 交付物 | v2.2 重点 |
|------|--------|----------|
| Day 1–2 | Schema v2.1 + sample_sources.json + sample_report.json | 锁数据结构 |
| Day 3–4 | LangGraph 四节点跑通 Mock 数据闭环 | **Analyzer 两步骨架先跑通** |
| Day 5–7 | 前端:输入页 + Agent 状态页 + 报告溯源页 | **Writer chip 格式与前端对齐** |
| Week 2 | 接入实时采集 + 缓存兜底 + TRAE 痕迹规划 | timeout / 并发 |
| Week 3 | Reviewer 打回演示 + 可观测性 + 业务量化指标 + 答辩脚本 | R6 开启,跑端到端 |

---

## 十三、v2.2 changelog(vs v2.1)

### P0 修订(pre-mortem,不修必翻车)

| # | 位置 | 改动 |
|---|------|------|
| P0.1 | §六 新增 | Analyzer 拆 facts → derivations 两步;每步带 quick_validate 自修复一次 |
| P0.2 | §三 §8.5 | retry 改按 reject_target 分桶:`{collector:1, analyzer:2, writer:1}`,Collector 重试不补数据所以只给 1 次 |
| P0.3 | §七 新增 | Writer 输出格式锁定 `[SXXXXXXX]` chip,前端按此渲染溯源跳转 |

### P1 修订

| # | 位置 | 改动 |
|---|------|------|
| P1.4 | §5.5 | Collector 改 ThreadPoolExecutor(max_workers=6) 并发,单产品 20s hard timeout |
| P1.5 | §6.3 | quick_validate_facts 补 gap 覆盖检查(target + ≥1 competitor) |
| P1.6 | §8.5 | R6 默认 `ENABLE_LLM_REVIEW=False`,只在 R1-R5 全过后跑一次,从 30+ LLM 调用/请求 → ≤2 次 |

### 文档一致性修订

- **evidence_id**: `sha1[:8]` → `sha1[:7]`,统一为 8 字符,与 §4.1 示例 `S3F8A1C2` 对齐
- **competitors 命名**: `"GitHub Copilot"` → `"GitHubCopilot"`,与 `products.yaml` key 对齐
- **sample_evidence_ids**: 禁止 `"...共30条"` 占位字符串,要么列全要么用 `sample_size + representative_evidence_ids`
- **dedupe**: 只在 `registry.fetch_all` 内做一次,`collector_node` 仅对 patch 合并兜底
- **CacheAdapter**: 补 `can_fetch` 实现;`score_relevance` 注释为 BM25/TF-IDF
- **module_status**: 用 `startswith` 替代子串匹配,避免误匹配
- **degraded_writer**: 字段 `i["issue"]` → `i["issue_type"] + i["detail"]`
- **客户端 timeout**: httpx 读 15s / ARK LLM 90s(原 600s 默认 demo 会卡)

### v2.2.1 收口(冻结前最后一轮)

| 位置 | 改动 | 原因 |
|------|------|------|
| §7.1/7.2 | Writer 输出禁止含 `quality_score` 字段;前端从 `quality_report` 单独渲染 | Writer 在 Reviewer 前运行,此前示例的"质检评分:92/100"逻辑上不可能 |
| §4.2 | aggregation 示例改为代表模式(只用 `representative_evidence_ids + sample_size`),并明确二选一规范 | 旧示例 sample_size=30 但只列 3 个 ID,违反自家规则 |
| §6.3 | `quick_validate_facts(facts, evidence, meta)` 显式接收 meta;target/competitors 从 analysis_meta 取,不再依赖 facts._target | facts 结构里本来就没有这些字段 |
| §5.5 | Collector 并发改 `wait(futures, timeout=25)`,正确实现 wall-clock 兜底;说明真正保障在 Adapter 内 HTTP timeout | `as_completed + fut.result(timeout)` 不会真正强制超时 |
| §8.0 新增 | Reviewer 拆 hard_gate / soft scoring,Demo 默认 `REVIEWER_MODE=minimal`(只 R1/R4/R5 当 error);full 模式留给答辩 | 评委质疑"规则是否过拟合 Demo"时有结构化回答 |
| §8.5 | Reviewer 节点改 mode-driven(RULE_RUNNERS + cfg),旧 `ENABLE_LLM_REVIEW` 常量并入 mode 配置 | 与 §8.0 对齐 |

### 暂未处理(Week 3 评估或 v2.3)

- **规则瘦身**: R2/R3 合并进 R6 单一 LLM judge(目前已通过 minimal 模式回避);`source_reliability` 矩阵 + `FRESHNESS_TTL_DAYS` + `priority` 阈值 config 化 → 跨行业泛化
- **ISSUE_TYPE_TO_TARGET** 收敛: 13 项 → 3 类(evidence_missing / logic_broken / format_broken)
- **TRAE 协作痕迹**: commit message + README 关键决策点标注(评分维度 4)
- **业务价值量化**: "人工 4h → 系统 5min"、覆盖度提升、一致性等(评分维度 3,答辩必备)

---

## 设计冻结声明

**v2.2.1 即冻结版**。后续除非实现过程中发现新的硬阻塞,**不再继续重构文档**。

下一步交付物(按优先级):

1. `data/sample_sources.json` — 3 产品 × 4 claim_type,每产品 8-12 条,总 30-40 条
2. `data/sample_report.json` — 期望的 schema_draft 输出,用于对齐 Analyzer
3. `prompts/analyzer_facts.md` — Step 1 system prompt + few-shot
4. `prompts/analyzer_derivations.md` — Step 2 system prompt + few-shot

代码骨架在 sample 数据锁定后再起。
