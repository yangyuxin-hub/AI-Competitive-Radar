# ③ 证据按维度召回(RAG 化)· 详细设计

> 优化总纲见 [optimization-plan.md](optimization-plan.md) ③。本文回答四个落地问题:
> **证据索引存哪 / embedding 还是关键词 / 每维度 top-k / 跟 `_compact_evidence` 怎么衔接**,并给分期落地路径。
> 目标:把"每产品 top-8 证据一锅端给模型"改成"**每个 (产品 × 维度) 单独召回真讲这维度的证据**"——治本功能矩阵塌陷,同时提速、抗幻觉。

---

## 0. 问题与目标

**现状**:`_feature_fill(product)` 拿到的是该产品的 top-8 **泛功能证据**,不区分维度。模型填"实时协同"格时,得在 8 条杂证据里自己找相关的——很多维度根本没有对应证据,于是要么硬凑(幻觉),要么 unknown(塌陷)。

**目标**:对每个 **(产品 × 维度)** 单元格,**只检索真正提到该维度的证据**喂进去。召回到 → 据实填(support + quality);召回为空 → 老实 unknown(并触发对该维度的定向补采,已有)。

**收益**:① 效果——每格有据可依,不塌不编;② 性能——prompt 变小、更快更省;③ 可控——能看清"这格召回了哪几条",好审核。

---

## 1. 证据索引存哪?→ **每轮内存索引 + evidence_id 维度的磁盘 embedding 缓存**

**关键事实**:证据是**每轮新鲜采集**的(live/cache/mock),且一次分析量很小(~50–150 条)。所以:

- **不建持久化向量库**(chroma/faiss-on-disk/sqlite-vec):对 150 条/轮是杀鸡用牛刀,还引入跨轮陈旧问题。
- **每轮在内存建索引**:`_step1_facts` 里证据定稿后,把这 ~150 条的 embedding 算成一个 numpy 矩阵,连同证据列表持有,查完即弃。
- **磁盘只缓存 embedding 向量**:键用 `evidence_id`(=内容 hash,内容稳定),存 `data/cache/embeddings/<evidence_id>.npy` 或单个 `embeddings.jsonl`。**好处**:gap-refill 重出、reviewer 打回重跑时,同一条证据不重复算 embedding(evidence_id 不变即命中)。无 TTL(内容 hash 天然失效控制)。

> 结论:**索引是每轮内存对象;embedding 向量按 evidence_id 落盘缓存**,避免重算。

---

## 2. embedding 还是关键词召回?→ **embedding 主召回 + 关键词兜底**

**决定性因素是跨语言**:证据片段中英混杂(官网英文 + 社区中英),维度名是中文(`实时多人协同`)。

- **纯关键词/BM25 会漏**:`实时协同` 匹配不到 `real-time collaboration`。
- **embedding 能跨语种语义对齐**——这是这个场景的硬需求,所以**主召回用 embedding**。
- **关键词兜底**:没有 embedding provider(没配 key / 离线)时,降级为 token 重叠召回 + 一张**关键术语中英同义表**(协同↔collaboration、补全↔completion…),保证"无 embedding 也能跑,只是精度低"。

**embedding provider**(新建薄封装 `src/embed.py`):
- 配置 `EMBED_MODEL` / `EMBED_BASE_URL` / 复用 `ARK_API_KEY`;ARK 有 `doubao-embedding`(中英都行)。
- 接口:`embed_texts(list[str]) -> np.ndarray`(批量一次调用);带 evidence_id 缓存。
- `embed_available()`:无 key → False → 自动走关键词兜底。

> 结论:**embedding 主、关键词兜底**;provider 可配、可降级。

---

## 3. 每维度 top-k?→ **feature 单元格 k=4 + 相似度阈值;先只上功能矩阵**

- **(产品 × 维度) 召回 top-k**:单元格只需判"有没有 + 好不好",证据不必多。取 **k=4**,并设 **相似度阈值**(余弦 ≥ `RAG_SIM_THRESHOLD`,默认 0.25)。
  - **阈值是抗幻觉关键**:没有任何证据过阈 → 该格 **unknown**(不硬塞),并触发对"该维度"的 feature_existence 定向补采(已实现)。
- **召回范围先只圈功能矩阵**(塌陷重灾区):
  - `feature_existence`(判 support)+ `performance_quality`/`user_pain`(判 quality),按维度召回。
  - **pricing / user_persona 暂不改**:定价是产品级、量小,直接喂即可;痛点是"先发现主题"而非"按已知维度召回",性质不同,留到二期。
- 参数全 env 可调:`RAG_TOPK`(默认 4)、`RAG_SIM_THRESHOLD`(默认 0.25)。

> 结论:**功能矩阵每格 top-4 + 阈值兜底**;定价/痛点一期不动。

---

## 4. 跟 `_compact_evidence` 怎么衔接?→ **不替换,加在 feature_fill 前面一层**

二者职责正交,**组合而非替代**:
- `_compact_evidence` = 粗粒度**分桶 + 去重 + 截断**(广度、便宜)。
- RAG 召回 = 细粒度**按维度精检**(精度、针对矩阵)。

**改造点只在 feature 这条线**:

```
              spine 仍用 _compact_evidence(看全局才好出维度)
                       │  产出维度骨架 d1..dn
                       ▼
  建 EvidenceIndex(本轮全量 feature 类证据)   ← 新增
                       │
  feature_fill(product P):
     for d in 维度:
        packet[d] = index.recall(query=d.name, product=P,
                                  claim_types=feature类, k=4, thr=0.25)   ← 新增
     把「按维度分组的证据 packet」喂给填充 LLM(取代原来一股脑 top-8)
                       │
        某维度 packet 空 → 该格 unknown(确定性,不调 LLM 猜)
```

- **spine / pricing / persona 不变**,仍走 `_compact_evidence`。
- **feature_fill 的输入从"产品 top-8"变成"产品的 {维度→top-4} 分组包"**;填充 prompt 相应改成"逐维度看对应证据填"。
- `_compact_evidence` 的**近似去重/截断工具复用**在召回结果上(召回回来的 4 条也去个重、截 180 字)。
- **无 embedding 时**:`index.recall` 内部走关键词兜底;再不行回退到现在的 `_compact_evidence` 行为(零回归)。

---

## 5. 分期落地

| 期 | 范围 | 验收 |
|---|---|---|
| **一期** | `src/embed.py`(embedding+缓存+关键词兜底)+ `EvidenceIndex` + feature_fill 接召回 | 设计样例(设计工具/PM)矩阵 `—` 显著下降且**不靠硬凑**;每格能列出召回的 evidence_id |
| **二期** | 召回扩到痛点(主题发现后按主题召回 UGC)+ 召回结果进审核记录(review.md 显示每格召回了啥) | 痛点引用更准;可审核 |
| **三期(可选)** | embedding 缓存复用到 intake 竞品去重、reviewer R6 语义校验 | 复用提效 |

---

## 6. 风险与权衡

- **embedding provider 依赖** → 关键词兜底 + 自动降级,保证可跑。
- **阈值难调**:太高全 unknown、太低进噪声 → env 可调 + 设计样例标定默认值。
- **跨语种 embedding 质量**取决于模型(doubao-embedding 中英尚可);二期可评估多语 sentence-transformers 本地模型作离线兜底。
- **召回 ≠ 真相**:召回的是"语义相关",仍可能相关但不直接回答 support——所以 quality_score 仍要求基于 snippet 原文,sanitize 仍兜底删非法引用。

---

## 7. 新增/改动清单(一期)

- 新增 `src/embed.py`:`embed_texts` / `embed_available` / evidence_id 向量缓存
- 新增 `EvidenceIndex`(放 `src/analyzer.py` 或单独 `src/recall.py`):`build(evidence)` / `recall(query, product, claim_types, k, thr)`,embedding 主 + 关键词兜底
- 改 `_feature_fill` / `_feature_spine` 调用处:fill 前建索引、按维度召回、分组喂入
- 改 `prompts/analyzer_facts.md`:fill 段落改成"逐维度看对应证据"
- 配置:`EMBED_MODEL/EMBED_BASE_URL`、`RAG_TOPK`、`RAG_SIM_THRESHOLD`、`RAG_RECALL=0` 总开关(回退现状)
