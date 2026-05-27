# Prompt 设计说明书

> 版本:v1.1 · 最后修订:2026-05-27 · 模型:Doubao-Seed-2.0-lite

---

## 一、Prompt 全景

| Prompt | 文件 | v | 调用方 | 职责 | 输出 | max_tokens |
|--------|------|-----|--------|------|------|------------|
| analyzer_facts | `prompts/analyzer_facts.md` | 1.1 | `analyzer.py:_step1_facts()` | 证据→结构化事实 | feature_tree + pricing_model + user_persona | 8192 |
| analyzer_derivations | `prompts/analyzer_derivations.md` | 1.1 | `analyzer.py:_step2_derivations()` | 事实→推导结论 | swot + recommendations | 3072 |
| url_discovery | `prompts/url_discovery.md` | 1.1 | `collector.py:discover_urls()` | 产品名→官网URL | official_pages + pricing_pages | 1024 |
| intake | `prompts/intake.md` | 1.0 | `intake.py:_propose_via_llm()` | 意图→选择题 | 候选产品/焦点/目的 | 1024 |

额外的 LLM 调用点：
- **Judge** (`src/judge.py:_build_system_prompt()`):动态由 `config/quality_rubric.yaml` 生成 prompt，不在本文档范围内。
- **Repair hint** (`src/analyzer.py:_build_repair_hint()`):quick_validate 失败时拼接到系统 prompt 末尾，不是独立 prompt。

### 调用链路

```
collector.py → discover_urls() → url_discovery.md + product name → LLM
intake.py    → _propose_via_llm() → intake.md + user_input → LLM
analyzer.py  → _step1_facts()    → analyzer_facts.md + evidence JSON → LLM
             → (if quick_validate fails)  → analyzer_facts.md + repair_hint → LLM
analyzer.py  → _step2_derivations() → analyzer_derivations.md + facts JSON → LLM
             → (if quick_validate fails)  → analyzer_derivations.md + repair_hint → LLM
```

---

## 二、各 Prompt 设计思路

### 2.1 analyzer_facts.md — Step 1 事实层

**设计目标**：把 30+ 条 raw_evidence 蒸馏为结构化事实，不做推理。

**关键设计决策**：
- **硬约束对齐 Reviewer/quick_validate**:8 条 HARD CONSTRAINTS 每一条都对应着一个可自动检验的规则（R1 引用完整性、R3 聚合一致性、R5 结构冲突等），让 LLM 在生成时就知道什么会触发打回。
- **方法论前置**:分析边界、事实与感知分离、证据冲突处理、竞品分层——这些在 System Prompt 的前 500 tokens 内就告诉 LLM，确保它在生成时时刻记住这些语境。
- **Schema 用"关键字段表 + 骨架"而非完整 JSONC**:Doubao-Seed-2.0-lite 上下文有限（8k），完整带注释的 JSON schema 浪费约 60 行（~500 tokens）。改为表格式速查 + 简化骨架，省下的空间用于更多的 few-shot。
- **2 组 few-shot**:Feature+Pain（演示冲突处理）+ Pricing（演示多 tier），覆盖三个输出模块。每组 few-shot 只用 4 条 evidence，强调"只展示风格，真实输出基于全部 evidence"。
- **证据冲突处理(方法论 §3)**:v1.1 新增。告诉 LLM 当 vendor 说"支持 X"但用户说"X 很慢"时，support_status 取 vendor、quality_score 取 user，不要回避矛盾。

**与 Reviewer 规则的对应**：

| 硬约束 | Reviewer 规则 |
|--------|--------------|
| H1 不许编造 evidence_id | R1 引用完整性 |
| H2 不许超出 snippet | R6 语义落地(full 模式) |
| H3 每个 feature 覆盖 target+competitor | quick_validate 的 gap 检查 |
| H4 aggregation 规范(sample_size=pos+neg+neu) | R3 聚合一致性 |
| H5 support_status 4 值 | R5 结构冲突(取值检查) |

### 2.2 analyzer_derivations.md — Step 2 推导层

**设计目标**：基于 Step 1 的事实做竞争逻辑推导，产出 SWOT 和优先级建议。

**关键设计决策**：
- **SWOT 推导规则(v1.1 新增)**:4 象限各给出具体的推断规则（如 Strengths 必须对应到 gap.winner==target 的 feature），避免 LLM 产出"列表式 SWOT"（每个点一句话，没有竞争逻辑）。
- **评分 1-5 五级锚点(v1.1 升级)**:原 1-3-5 三级改为 1-5 五级，每级有可操作的定义。LLM 在 judgement call（2 分还是 3 分）时有参考。
- **H8 数量兜底**:原"3-6 条"在证据少时过于严格，改为"目标 3-6 条，最少 2 条"。
- **H11 正面化**:不只是"不要空泛"，还给出具体要素（对象 + 指标 + 验收方式）。

**Scoring 公式与代码验证的对应**：

```
final_score = 0.35 * pain_frequency + 0.30 * business_impact
           + 0.20 * implementation_feasibility + 0.15 * evidence_confidence
```

这个公式在 `quick_validate_derivations()` (analyzer.py:243) 和 `check_structured_contradiction()` (reviewer.py:158) 两处被验证。Prompt 中的 H4 约束 LLM 按公式填，代码中两次校验确保自洽。

### 2.3 url_discovery.md — URL 发现

**设计目标**：让 LLM 根据产品名找到真实的官网 URL。

**关键设计决策**：
- **正面/负面示例(v1.1 新增)**:明确列出"这是官网" vs "这不是官网"的对比，防止 LLM 返回 Wikipedia/G2/ProductHunt。
- **降级指南(v1.1 新增)**:找不到时显式返回空列表，不要猜测。
- **歧义处理(v1.1 新增)**:产品名有歧义时优先选知名 SaaS。

### 2.4 intake.md — 意图问询

**设计目标**：把用户一句话变成 5 道选择题。

**关键设计决策**：
- **从代码提取为独立文件(v1.0)**:原 `_PROPOSE_SYSTEM` 硬编码在 `src/intake.py` 中，调试和调优需要改代码。提取为 `prompts/intake.md` 后与其它 prompt 统一管理。
- **约束增强**:增加了"不要推荐已下线产品"、"focus 应贴合用户关键词"、"domain_name 用中文"等约束。
- **输出示例**:提供了一个完整的输入→输出示例。

---

## 三、模型适配说明

当前所有 prompt 针对 **Doubao-Seed-2.0-lite** 调优。该模型特点：

- **上下文窗口**:8k tokens。analyzer_facts 的 System Prompt 约 2800 tokens，加上 evidence JSON（通常 15-30 条，约 3000-5000 tokens），总输入约 6000-8000 tokens，逼近上限。因此 prompt 必须精简。
- **指令跟随**:较强，能准确执行 12→8 条硬约束。但面对模糊指令时容易产出模板化输出，因此方法论部分需要给出具体判断维度。
- **JSON 模式**:支持 `response_format=json_object`，多数情况能返回合法 JSON。偶尔（约 5-10%）会额外包裹 markdown 围栏，代码中有 `_strip_json()` 兜底。

**若换模型**（如答辩时换 Seed-2.0-pro 或 DeepSeek-V3）：
- 更强的模型可能需要**减少约束**、增加自由度——它自己就能做出更好的判断
- 更强的模型通常上下文更大，可以**增加 few-shot 数量和复杂度**
- 需要重新验证 Repair 触发率（更强的模型 quick_validate 失败率可能更低）

---

## 四、Prompt 修改操作手册

### 4.1 修改流程

1. **确定目标**:改 prompt 要解决什么具体问题？（如"R2 warning 太多，原因是 claim_type 分类不准"）
2. **改 prompt 文件**:在 `prompts/*.md` 中修改
3. **跑 Mock 模式验证**:`ANALYZER_MOCK=1 python -m src.graph`
4. **跑真实 LLM 验证**(如有 ARK_API_KEY):`python -m src.graph`
5. **对比 quality_score 和 Reviewer 结果**:确认没有引入新 error，warning 数量是否改善
6. **更新本文档的版本号与变更日志**

### 4.2 判断 Prompt 好坏的标准

| 指标 | 如何测量 |
|------|----------|
| 一次通过率 | 跑 5 次，看 quick_validate 触发 repair 的比例。目标 <30% |
| Reviewer error 数 | 看 R1/R4/R5 error。目标 0 |
| Reviewer warning 数 | 看 R2/R3/R7 warning。当前基线 AI 编码 37/100，PM 49/100 |
| 输出完整性 | schema_draft 是否覆盖了所有有证据的功能/产品/pain |
| Token 效率 | prompt 的 token 数 vs 输出质量。不要为了省 token 删关键约束 |

### 4.3 常见问题与排查

| 症状 | 可能原因 | 排查方向 |
|------|----------|----------|
| quick_validate 频繁触发 | Schema 约束描述不清或 LLM 不遵守 | 检查 repair hint 是否有对应修复指引 |
| R2 大量 warning | `allowed_claim_types` 范围太窄或 prompt 没说明 | 检查 `collect_all_evidence_refs()` 中的类型映射 + prompt 中的方法论 |
| 输出 feature 太少 | 方法论中"分析边界"收得太紧或 few-shot 暗示了不该有的约束 | 检查 few-shot 覆盖的 feature 数量是否合理 |
| SWOT 全是功能复读 | 方法论缺少 SWOT 推导规则 | v1.1 已增加，若仍出现则检查是否被 LLM 忽略 |
| LLM 编造 evidence_id | Mock 模式的 sample_report ID 被记住 | 在 few-shot 中强调"不要照抄 ID" |

---

## 五、变更日志

### v1.1 (2026-05-27) — Prompt 体系审计与改进

**analyzer_facts.md**:
- Schema 章节:完整 JSONC → 关键字段表 + 简化骨架(减少约 30 行)
- 硬约束 12→8 条:合并 H3+H9(覆盖检查)、H11 移至方法论、H10 具体化、H12 删除
- 补 pricing_model few-shot 示例(2 tiers × 3 products)
- 新增证据冲突处理规则(方法论 §3)
- Repair 章节增加常见修复指引

**analyzer_derivations.md**:
- 新增 SWOT 推导规则(4 象限各 1-2 条)
- 补 SWOT few-shot 示例(4 象限各 1 条)
- Scoring 评分指引 1-3-5→1-5 五级锚点
- H8 数量约束加兜底(最少 2 条)
- H11 正面化(action 需含对象+指标+验收方式)

**url_discovery.md**:
- 新增正面/负面 URL 示例
- 新增降级指南(找不到返回空列表)
- 新增产品名歧义处理规则

**intake.md** (新建):
- 从 `src/intake.py` 提取 `_PROPOSE_SYSTEM`
- 新增约束(不要推荐已下线产品、focus 应贴合关键词)
- 新增完整输入→输出示例

**代码改动**:
- `src/intake.py`: `_PROPOSE_SYSTEM` 常量 → `_load_prompt("intake")`
- `src/analyzer.py`: `_build_repair_hint()` 增加常见修复指引

### v1.0 (2026-05-25) — 初始版本

- `analyzer_facts.md`、`analyzer_derivations.md`、`url_discovery.md` 创建
- `_PROPOSE_SYSTEM` 在 `src/intake.py` 中硬编码
