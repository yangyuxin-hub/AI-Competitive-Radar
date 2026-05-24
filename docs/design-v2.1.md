# 竞品分析 Agent 协作系统 — 设计文档 v2.1

> 字节跳动 AI 全栈挑战赛 · Topic 3
> 版本：v2.1-frozen · 日期：2026-05-23
> 核心原则：**证据覆盖率可控、失败可降级、证据链可复现**

---

## 一、项目定位

| 维度 | 内容 |
|------|------|
| 目标用户 | 企业产品团队的产品经理 / 数据分析师 |
| 分析目的 | 学习竞品优点，发现功能差距，优化自身产品 |
| Demo 赛道 | AI 编程工具：Cursor vs Windsurf vs GitHub Copilot |
| 示例输入 | "分析 Cursor 和 Windsurf 在代码补全体验上的差距" |
| 示例输出 | 结构化竞品报告（功能对比 + 用户痛点 + 定价 + SWOT + 优先级建议） |
| 扩展性 | 换行业 = 新增 products.yaml 配置，不改代码 |

---

## 二、系统架构

```
用户输入
    ↓
[AgentState 初始化]
    ↓
Collector ──────────────────────────────────────────────┐
  AdapterRegistry                                        │
  ├── OfficialPageAdapter  (实时)                        │
  ├── PricingPageAdapter   (实时)                        │
  ├── RedditAdapter        (实时，需 API Key)             │
  ├── CacheAdapter         (缓存补齐缺失 claim_type)      │
  └── MockAdapter          (Demo 保底)                   │
    ↓ raw_evidence                                        │
Analyzer                                                  │
  ├── 填充 feature_tree                                   │
  ├── 填充 pricing_model                                  │
  ├── 填充 user_persona                                   │
  ├── 填充 swot                                           │
  └── 填充 recommendations                                │
    ↓ schema_draft                                        │
Writer                                                    │
  └── 渲染 Markdown 报告                                  │
    ↓ report_draft                                        │
Reviewer (R1–R7)                                          │
    ↓                                                     │
  passed  → 输出报告                                      │
  running → 打回对应 Agent（最多 2 轮）───────────────────┘
  degraded → 降级报告（分层输出）
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

    # 打回信息（结构化）
    reject_target:        Optional[Literal["collector", "analyzer", "writer"]]
    reject_requirements:  Optional[list[dict]]   # Collector 精准补证据用

    # 流控
    retry_count: int
    max_retries: int
    status: Literal["running", "passed", "degraded", "failed"]
```

---

## 四、竞品知识 Schema v2.1

### 4.0 analysis_meta

```json
{
  "report_id": "CR-20260523-001",
  "schema_version": "2.1",
  "target_product": "Cursor",
  "competitors": ["Windsurf", "GitHub Copilot"],
  "analysis_focus": ["代码补全体验"],
  "analysis_purpose": "学习竞品优点，优化自身产品",
  "generated_at": "2026-05-23T10:00:00Z",
  "data_cutoff": "2026-05-23",
  "agent_trace_id": "trace_xxx"
}
```

### 4.1 raw_evidence（Collector 唯一输出）

```json
[
  {
    "evidence_id":        "S3F8A1C2",
    "product":            "Cursor",
    "claim_type":         "feature_existence | pricing | user_pain | performance_quality | market_signal",
    "source_type":        "official_page | official_doc | pricing_page | reddit | producthunt | hn | web_search",
    "source_bias":        "vendor_claim | user_generated | third_party | unknown",
    "source_url":         "https://cursor.com/features",
    "observed_at":        "2026-05-23",
    "source_freshness":   "current | stale | unknown",
    "claim":              "Cursor supports multi-line code completion across files",
    "extracted_snippet":  "Supports multi-line edits and predictions across your codebase...",
    "source_reliability": 0.85,
    "claim_relevance":    0.90,
    "evidence_confidence": 0.77
  }
]
```

**evidence_id 生成（确定性 hash，不用 uuid）：**

```python
import hashlib

def generate_evidence_id(product: str, source_url: str, claim: str) -> str:
    raw = f"{product}|{source_url}|{claim}".encode("utf-8")
    return "S" + hashlib.sha1(raw).hexdigest()[:8].upper()
```

**source_reliability 对照（claim_type × source_bias）：**

| claim_type | vendor_claim | user_generated | third_party | web_search |
|-----------|------|------|------|------|
| feature_existence | 0.85 | 0.60 | 0.75 | 0.60 |
| pricing | 0.90 | 0.40 | 0.70 | 0.55 |
| user_pain | 0.30 | 0.85 | 0.70 | 0.55 |
| performance_quality | 0.50 | 0.75 | 0.75 | 0.60 |
| market_signal | 0.60 | 0.65 | 0.75 | 0.60 |

> source_bias 映射：official_page / official_doc / pricing_page → vendor_claim；reddit / producthunt / hn → user_generated；独立评测站 → third_party；web_search → web_search

**source_freshness TTL（按 claim_type）：**

```python
FRESHNESS_TTL_DAYS = {
    "pricing":             7,
    "feature_existence":  30,
    "performance_quality": 60,
    "user_pain":           90,
    "market_signal":       30,
}
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
                "aggregation_type": "sampled_evidence | external_summary",
                "positive_mentions": 18,
                "negative_mentions": 4,
                "neutral_mentions": 8,
                "sample_size": 30,
                "sample_evidence_ids": ["S001", "S002", "...共30条"],
                "representative_evidence_ids": ["S008", "S014"],
                "method": "LLM sentiment classification over collected comments"
              },
              "evidence_ids": ["S008", "S014"]
            }
          }
        },
        "gap": {
          "winner": "Cursor",
          "gap_type": "accuracy",
          "reason": "Cursor 在跨文件上下文与多行补全上用户正反馈更多",
          "evidence_ids": ["S3F8A1C2", "S008", "S021"],
          "confidence": 0.78
        }
      }
    ]
  }
}
```

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
            "price": {
              "amount": 20,
              "currency": "USD",
              "normalized_usd_month": 20
            },
            "limits": [
              {
                "limit_name": "code_completion",
                "limit_value": "unlimited",
                "unit": "requests_per_month"
              }
            ],
            "display_limits": "无限补全",
            "observed_at": "2026-05-23",
            "source_freshness": "current",
            "evidence_ids": ["S041"]
          }
        ]
      }
    ],
    "pricing_gap": {
      "target_position": "similar | cheaper | more_expensive | unknown",
      "summary": "三者 Pro 档价格相近，差异主要在免费额度",
      "evidence_ids": ["S041", "S051", "S061"],
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
        "description": "个人开发者，用于日常编码、项目重构和快速原型开发",
        "evidence_ids": ["S081"],
        "confidence": 0.76
      }
    ],
    "pain_points": [
      {
        "pain_id": "P001",
        "description": "补全上下文理解不够深，跨文件引用错误",
        "frequency": {
          "level": "high",
          "count": "30条评论中15条提及",
          "sample_size": 30,
          "evidence_ids": ["S033", "S034", "S035"]
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
      "action": "优化跨文件上下文召回策略，提升多行补全准确性",
      "rationale": "该问题同时出现在功能差距与用户痛点中",
      "source_feature_ids": ["F001"],
      "source_pain_ids": ["P001"],
      "evidence_ids": ["S008", "S033", "S034"],
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

**优先级映射：**

```
P0: final_score >= 4.2
P1: 3.4 – 4.19
P2: 2.6 – 3.39
P3: < 2.6
```

### 4.6 swot（辅助参考）

```json
{
  "swot": {
    "target": "Cursor",
    "note": "核心结论以 feature_gap 和 recommendations 为准",
    "strengths":     [{"point": "多行补全质量较强", "evidence_ids": ["S3F8A1C2"], "confidence": 0.78}],
    "weaknesses":    [{"point": "价格偏高",         "evidence_ids": ["S041"],     "confidence": 0.70}],
    "opportunities": [{"point": "企业采购市场空白",  "evidence_ids": ["S071"],     "confidence": 0.60}],
    "threats":       [{"point": "Windsurf 响应速度追近", "evidence_ids": ["S021"], "confidence": 0.65}]
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
    def fetch(self, product: str, focus: str) -> list[RawEvidence]:
        pass

    @abstractmethod
    def can_fetch(self, product: str) -> bool:
        pass
```

### 5.2 产品配置（config/products.yaml）

```yaml
products:
  Cursor:
    aliases: ["Cursor AI", "Cursor editor"]
    official_pages:
      - https://cursor.com/features
    pricing_pages:
      - https://cursor.com/pricing
  Windsurf:
    aliases: ["Windsurf", "Codeium Windsurf"]
    official_pages:
      - https://codeium.com/windsurf
    pricing_pages:
      - https://codeium.com/pricing
  GitHubCopilot:
    aliases: ["GitHub Copilot", "Copilot"]
    official_pages:
      - https://github.com/features/copilot
    pricing_pages:
      - https://github.com/features/copilot/plans
```

### 5.3 AdapterRegistry

```python
REQUIRED_CLAIM_TYPES = {"feature_existence", "performance_quality", "pricing", "user_pain"}

class AdapterRegistry:
    def __init__(self):
        self.live_adapters = [
            OfficialPageAdapter(),
            PricingPageAdapter(),
            RedditAdapter()
        ]
        self.cache = CacheAdapter()
        self.mock  = MockAdapter()

    def fetch_all(self, product: str, focus: str) -> tuple[list[RawEvidence], dict]:
        all_evidences  = []
        adapter_events = []

        # 第一层：实时抓取
        for adapter in self.live_adapters:
            if not adapter.can_fetch(product):
                continue
            try:
                evs = adapter.fetch(product, focus)
                all_evidences.extend(evs)
                self.cache.save(product, evs)           # merge 写入缓存
                adapter_events.append({
                    "adapter": type(adapter).__name__,
                    "status": "success", "count": len(evs)
                })
            except FetchError as e:
                adapter_events.append({
                    "adapter": type(adapter).__name__,
                    "status": "failed", "reason": str(e), "fallback": "cache"
                })

        # 第二层：缓存补齐缺失 claim_type
        missing = REQUIRED_CLAIM_TYPES - {e.claim_type for e in all_evidences}
        if missing and self.cache.can_fetch(product):
            cached = self.cache.fetch(product, focus)
            all_evidences.extend(e for e in cached if e.claim_type in missing)

        # 第三层：Mock 补齐仍缺失的 claim_type
        still_missing = REQUIRED_CLAIM_TYPES - {e.claim_type for e in all_evidences}
        if still_missing and self.mock.can_fetch(product):
            mock_evs = self.mock.fetch(product, focus)
            all_evidences.extend(e for e in mock_evs if e.claim_type in still_missing)

        # dedupe 在 coverage 计算之前，避免重复证据虚高覆盖率
        all_evidences = dedupe_evidence(all_evidences)
        coverage = {ct: sum(1 for e in all_evidences if e.claim_type == ct)
                    for ct in REQUIRED_CLAIM_TYPES}
        return all_evidences, {"adapter_events": adapter_events, "coverage": coverage}
```

### 5.4 CacheAdapter（merge 写入，不覆盖）

```python
class CacheAdapter(SourceAdapter):
    def save(self, product: str, evidences: list[RawEvidence]):
        path = self._cache_path(product)
        old = self._load(path)
        merged = {ev.evidence_id: ev for ev in old}
        for ev in evidences:
            merged[ev.evidence_id] = ev      # 同 ID 用新数据覆盖
        self._dump(path, list(merged.values()))

    def fetch(self, product: str, focus: str) -> list[RawEvidence]:
        evidences = self._load(self._cache_path(product))
        for ev in evidences:
            days_old = (date.today() - date.fromisoformat(ev.observed_at)).days
            ttl = FRESHNESS_TTL_DAYS.get(ev.claim_type, 30)
            ev.source_freshness = "current" if days_old < ttl else "stale"
        return [ev for ev in evidences
                if score_relevance(ev.extracted_snippet, focus) > 0.3]
```

### 5.5 Collector 节点

```python
def collector_node(state: AgentState) -> AgentState:
    meta     = state["analysis_meta"]
    products = [meta["target_product"]] + meta["competitors"]
    focus    = meta["analysis_focus"][0]

    fetched         = []
    collection_meta = {"products": {}}

    for product in products:
        evs, meta_info = registry.fetch_all(product, focus)
        fetched.extend(evs)
        collection_meta["products"][product] = meta_info

    # 被打回时：按结构化 requirements 精准补证据（两阶段，避免重复塞旧证据）
    if state.get("reject_requirements"):
        merged = patch_by_requirements(
            existing     = state.get("raw_evidence", []),
            new          = fetched,
            requirements = state["reject_requirements"]
        )
    else:
        merged = fetched

    merged = dedupe_evidence(merged)

    return {
        **state,
        "raw_evidence":       [asdict(e) if not isinstance(e, dict) else e for e in merged],
        "collection_meta":    collection_meta,
        "reject_requirements": None,
        "reject_target":       None
    }
```

### 5.6 补丁函数

```python
def patch_by_requirements(existing, new, requirements) -> list:
    existing_ids = {e["evidence_id"] if isinstance(e, dict) else e.evidence_id
                    for e in existing}
    # 展开所有 required_claim_types（不只取第一个）
    needed_types = {
        ct
        for r in requirements
        if r.get("reject_target") == "collector"
        for ct in r.get("required_claim_types", [])
    }
    patches = [e for e in new
               if (e["evidence_id"] if isinstance(e, dict) else e.evidence_id)
               not in existing_ids
               and (e["claim_type"] if isinstance(e, dict) else e.claim_type)
               in needed_types]
    return existing + patches

def dedupe_evidence(evidences) -> list:
    merged = {}
    for ev in evidences:
        eid = ev["evidence_id"] if isinstance(ev, dict) else ev.evidence_id
        merged[eid] = ev
    return list(merged.values())
```

---

## 六、Reviewer v2.2

### 6.1 规则体系

```python
REVIEWER_RULES = {
    "R1": "evidence_reference_integrity",  # evidence_id 非空且存在于 raw_evidence
    "R2": "claim_type_compatibility",      # claim_type 与使用位置匹配
    "R3": "aggregation_integrity",         # 聚合数字、样本量、方法自洽
    "R4": "reasoning_chain_integrity",     # rec → feature/pain/evidence 链路完整
    "R5": "structured_contradiction",      # 结构化字段冲突检测
    "R6": "semantic_grounding",            # LLM judge 语义一致性（可选）
    "R7": "freshness_and_confidence",      # 时效性降权（仅 warning）
}
```

### 6.2 issue_type → reject_target 映射

```python
ISSUE_TYPE_TO_TARGET = {
    "missing_evidence_ids":       "analyzer",
    "evidence_id_not_found":      "collector",
    "malformed_evidence":         "collector",
    "invalid_evidence_usage":     "analyzer",
    "claim_type_mismatch":        "analyzer",
    "quality_only_official":      "collector",   # warning，不打回
    "aggregation_sum_overflow":   "analyzer",
    "aggregation_method_missing": "analyzer",
    "broken_reasoning_chain":     "analyzer",
    "evidence_ref_broken":        "collector",
    "structured_contradiction":   "analyzer",
    "semantic_grounding_fail":    "analyzer",
    "bad_report_format":          "writer",
}
```

### 6.3 severity 策略

| 问题类型 | 级别 | 处理 |
|---------|------|------|
| evidence_id 缺失 | error | 打回 Analyzer |
| evidence_id 不存在 | error | 打回 Collector |
| malformed_evidence | error | 打回 Collector |
| claim_type 不匹配 | error | 打回 Analyzer |
| quality_score 只有官网 | **warning** | 前端展示，不打回 |
| aggregation 数字溢出 | error | 打回 Analyzer |
| aggregation method 缺失 | error | 打回 Analyzer |
| reasoning chain 断裂 | error | 打回 Analyzer |
| 结构化冲突 | error | 打回 Analyzer |
| 语义校验 fail | error | 打回 Analyzer |
| 语义校验 weak | warning | 降低 confidence |
| source_freshness stale | warning | 降低 quality_score |

### 6.4 collect_all_evidence_refs（统一遍历，含 user_segments）

```python
def collect_all_evidence_refs(schema: dict) -> list[tuple[str, list[str], list[str]]]:
    refs = []

    # feature_tree
    for f in schema.get("feature_tree", {}).get("features", []):
        fid = f["feature_id"]
        for product, data in f.get("products", {}).items():
            refs.append((f"feature_tree.{fid}.{product}.support_evidence_ids",
                         data.get("support_evidence_ids", []),
                         ["feature_existence"]))
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

    # user_persona（pain_points + user_segments 都检查）
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

    # swot（含 pricing）
    for dim in ["strengths", "weaknesses", "opportunities", "threats"]:
        for i, item in enumerate(schema.get("swot", {}).get(dim, [])):
            refs.append((f"swot.{dim}[{i}].evidence_ids",
                         item.get("evidence_ids", []),
                         ["feature_existence","pricing","user_pain","performance_quality","market_signal"]))

    return refs
```

### 6.5 Reviewer 节点（含 reject_target 写回）

```python
# R6 可选：不把 llm 放进 state，用闭包注入
ENABLE_LLM_REVIEW = True

def make_reviewer_node(llm):
    def reviewer_node(state: AgentState) -> AgentState:
        schema = state["schema_draft"]

        all_issues = (
            check_evidence_reference_integrity(schema) +
            check_claim_type_compatibility(schema) +
            check_aggregation_integrity(schema) +
            check_reasoning_chain(schema) +
            check_structured_contradiction(schema) +
            check_freshness_and_confidence(schema)
        )
        if ENABLE_LLM_REVIEW:
            all_issues += check_semantic_grounding(schema, llm)

        errors   = [i for i in all_issues if i["severity"] == "error"]
        warnings = [i for i in all_issues if i["severity"] == "warning"]

        # passed_rules：从全量规则集合减去有问题的
        all_rule_ids  = set(REVIEWER_RULES.keys())
        failed_rules  = {i["rule"] for i in errors}
        warning_rules = {i["rule"] for i in warnings}
        passed_rules  = sorted(all_rule_ids - failed_rules - warning_rules)

        module_status = {}
        for module in ["raw_evidence","feature_tree","pricing_model",
                        "user_persona","recommendations","swot"]:
            errs = [i for i in errors   if module in i["location"]]
            wrns = [i for i in warnings if module in i["location"]]
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

        retry_count = state["retry_count"] + 1
        if retry_count >= state["max_retries"]:
            return {**state, "quality_report": quality_report,
                    "retry_count": retry_count, "status": "degraded",
                    "reject_target": None}

        # Counter 选打回目标；并列时 collector > analyzer > writer
        from collections import Counter
        target_counts = Counter(i["reject_target"] for i in errors)
        priority = {"collector": 2, "analyzer": 1, "writer": 0}
        reject_target = max(
            target_counts,
            key=lambda t: (target_counts[t], priority.get(t, 0))
        )

        # 结构化 requirements（给 Collector 精准补证据用）
        reject_requirements = [
            {
                "rule":                 i["rule"],
                "issue_type":           i["issue_type"],
                "location":             i["location"],
                "required_claim_types": i.get("required_claim_types", []),
                "reject_target":        i["reject_target"]
            }
            for i in errors if i["reject_target"] == "collector"
        ]

        return {
            **state,
            "quality_report":      quality_report,
            "retry_count":         retry_count,
            "reject_target":       reject_target,           # 写回 state
            "reject_requirements": reject_requirements or None,
            "status":              "running"
        }
    return reviewer_node
```

### 6.6 降级报告节点

```python
def degraded_writer_node(state: AgentState) -> AgentState:
    qr      = state["quality_report"]
    passed  = [m for m, s in qr["module_status"].items() if s == "passed"]
    warning = [m for m, s in qr["module_status"].items() if s == "warning"]
    failed  = [m for m, s in qr["module_status"].items() if s == "failed"]

    actions = {
        "collector": "补充用户侧证据来源（Reddit / ProductHunt）",
        "analyzer":  "修正证据引用关系与结论推理链",
        "writer":    "修正报告格式与引用标注"
    }
    needed = list({i["reject_target"] for i in qr["errors"]})

    report = f"""# 竞品分析报告（部分置信）

> 质检评分：{qr['quality_score']}/100 · 经 {state['max_retries']} 轮质检后降级输出

## 质检状态
- ✅ 通过：{', '.join(passed) or '无'}
- ⚠️ 存疑：{', '.join(warning) or '无'}
- ❌ 失败：{', '.join(failed) or '无'}

## 建议补充动作
{chr(10).join(f'- {actions[t]}' for t in needed if t in actions)}

---

## 可参考结论（通过质检模块）

{state.get('report_draft', '')}

---

## 不建议直接采纳的结论

{chr(10).join(f'- [{i["location"]}] {i["issue"]}' for i in qr["errors"])}
"""
    return {**state, "report_draft": report, "status": "degraded"}
```

---

## 七、LangGraph 编排

```python
reviewer_node = make_reviewer_node(llm)   # 闭包注入 LLM

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
    return state["reject_target"] or "analyzer"   # reject_target 已写回 state

graph.add_conditional_edges(
    "reviewer", route_after_review,
    {"collector": "collector", "analyzer": "analyzer",
     "writer": "writer", "degraded_writer": "degraded_writer", "end": END}
)
graph.add_edge("degraded_writer", END)

app = graph.compile()
```

---

## 八、可观测性

```python
import os
from functools import wraps
from datetime import datetime

# LangSmith 接入（3 行）
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"]    = "your_key"
os.environ["LANGCHAIN_PROJECT"]    = "competitive-analysis-agent"

# Agent 装饰器
def observable_agent(agent_name: str):
    def decorator(func):
        @wraps(func)
        def wrapper(state):
            log_agent_event(agent_name, "start", {"retry_count": state.get("retry_count", 0)})
            start = datetime.now()
            try:
                result = func(state)
                duration_ms = int((datetime.now() - start).total_seconds() * 1000)
                log_agent_event(agent_name, "end", {
                    "duration_ms": duration_ms,
                    "status": result.get("status", "running")
                })
                return result
            except Exception as e:
                log_agent_event(agent_name, "error", {"error": str(e)})
                raise
        return wrapper
    return decorator
```

日志格式（agent_trace.jsonl）：

```jsonl
{"timestamp":"2026-05-23T10:00:01","agent":"collector","event":"start","data":{"retry_count":0}}
{"timestamp":"2026-05-23T10:00:03","agent":"collector","event":"fetch_fallback","data":{"product":"Cursor","tier":"cache"}}
{"timestamp":"2026-05-23T10:00:05","agent":"reviewer","event":"end","data":{"status":"passed","quality_score":87}}
```

---

## 九、Analyzer 硬约束（待实现）

Analyzer 是最容易幻觉的节点，必须在 Prompt 中强制以下约束：

```text
1. 只能引用 raw_evidence 中存在的 evidence_id，禁止编造 ID
2. 事实性结论只能基于 extracted_snippet 中明确出现的信息；建议性结论必须同时引用 source_feature_ids 或 source_pain_ids，并在 rationale 中说明推导关系
3. feature gap 必须覆盖 target + 至少 1 个 competitor
4. 每个 recommendation 必须至少引用 1 个 feature_id 或 1 个 pain_id
5. priority_score 必须按加权公式计算，禁止手写 priority 字段
6. 证据不足时输出 support_status: "unknown"，不强行补结论
7. sampled_evidence 模式下，aggregation.sample_size 必须等于 sample_evidence_ids 的实际长度；若不记录全量 sample_evidence_ids，则必须填写 aggregation_method 说明采样来源
```

---

## 十、评分对照

| 评分维度 | 权重 | 覆盖 |
|---------|------|------|
| 多 Agent 协作与输出可信度 | 35% | 四 Agent 分工 · DAG · 结构化消息 · R1-R7 闭环 · Schema 完整 · 溯源完整 |
| 技术深度与工程完整度 | 25% | 端到端链路 · LangSmith+JSONL · 引用强制幻觉抑制 · 三层降级 |
| 业务价值与产品体验 | 20% | 产品经理场景 · 可换行业 · 降级报告产品化 |
| 代码质量与文档 | 10% | 模块化 · 配置化 · README |
| 合规、材料与答辩 | 10% | robots.txt 白名单 · source_bias 标注 |

---

## 十一、开发周期（Demo 优先版）

| 时间 | 交付物 | 原则 |
|------|--------|------|
| Day 1–2 | Schema v2.1 + sample_sources.json + sample_report.json | 先锁数据结构 |
| Day 3–4 | LangGraph 四节点跑通 Mock 数据闭环 | 不接真实抓取 |
| Day 5–7 | 前端：输入页 + Agent 状态页 + 报告溯源页 | 先让 Demo 可看 |
| Week 2 | 接入实时采集 + 缓存兜底 | 不追求全自动 |
| Week 3 | Reviewer 打回演示 + 可观测性 + 答辩脚本 | 强化亮点 |

---

## 十二、待完成模块

- [ ] Analyzer 节点完整实现（含 Prompt 约束）
- [ ] sample_sources.json（Demo Mock 数据，覆盖三个产品四类 claim_type）
- [ ] 前端交互设计（输入页 / Agent 状态页 / 报告溯源页）
- [ ] Writer 节点完整实现
- [ ] README + 部署说明
- [ ] products.yaml 配置文件
