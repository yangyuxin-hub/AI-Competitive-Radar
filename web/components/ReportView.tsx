"use client";

import { Children, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Report, Completeness, BusinessValue, ResearchMethod } from "@/lib/types";
import type {
  Feature,
  Recommendation,
  SwotItem,
  Swot,
  PricingProduct,
  PainPoint,
  CompetitorLandscape,
  LandscapeEntry,
  PositioningProduct,
  PricingModel,
  FeatureAnalysis,
  FeatureTree,
} from "@/lib/schema";
import { EvidenceProvider, Chips, Chip } from "./Evidence";

// 把正文 markdown 里的 [SXXXXXXX] 字面量替换成可跳转的富证据 chip(悬浮看概要、点击看原文+原网址)。
// 报告正文(report_draft)由 writer 生成,chip 是纯文本,不解析就只是无意义编号。
const CHIP_RE = /(\[S[A-Z0-9]{7}\])/g;
function injectChips(children: ReactNode): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child !== "string" || !child.includes("[S")) return child;
    return child.split(CHIP_RE).map((part, i) => {
      const m = /^\[(S[A-Z0-9]{7})\]$/.exec(part);
      return m ? <Chip key={i} id={m[1]} /> : part;
    });
  });
}

// react-markdown 把文本交给各元素渲染;在这些含文本的元素里拦截 chip 文本 → 富 chip。
const proseComponents: Components = {
  p: (props) => <p>{injectChips(props.children)}</p>,
  li: (props) => <li>{injectChips(props.children)}</li>,
  td: (props) => <td>{injectChips(props.children)}</td>,
  th: (props) => <th>{injectChips(props.children)}</th>,
  strong: (props) => <strong>{injectChips(props.children)}</strong>,
  em: (props) => <em>{injectChips(props.children)}</em>,
  h2: (props) => <h2>{injectChips(props.children)}</h2>,
  h3: (props) => <h3>{injectChips(props.children)}</h3>,
};

const CONF_LABEL: Record<string, { text: string; tone: string }> = {
  high: { text: "置信高", tone: "bg-emerald-500/15 text-emerald-300" },
  medium: { text: "置信中", tone: "bg-sky-500/15 text-sky-300" },
  low: { text: "置信低", tone: "bg-neutral-500/15 text-neutral-400" },
};

function QualityBadge({ score, status }: { score?: number; status: string }) {
  const s = score ?? 0;
  const tone =
    status === "passed" && s >= 80
      ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
      : status === "degraded"
        ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
        : "bg-sky-500/15 text-sky-400 border-sky-500/30";
  return (
    <span className={`shrink-0 rounded-full border px-3 py-1 text-xs font-medium ${tone}`}>
      质检 {score ?? "?"}/100 · {status}
    </span>
  );
}

function SectionTitle({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="scroll-mt-20 border-b border-white/10 pb-2 text-lg font-semibold text-white">
      {children}
    </h2>
  );
}

/** 真实质量分:support_status=unknown 或 score<=0(Analyzer 对「证据不足」的占位)→ null(渲染「未评分」)。
 *  与 writer._score_cell 同口径,避免把「没数据」当成真打 0 分拉低均分。 */
function realScore(cell?: { support_status?: string; quality_score?: { score?: number } | number | null }): number | null {
  if (!cell) return null;
  if ((cell.support_status ?? "").toLowerCase() === "unknown") return null;
  const s = typeof cell.quality_score === "number" ? cell.quality_score : cell.quality_score?.score;
  if (typeof s !== "number" || s <= 0) return null;
  return s;
}

function averageScores(features: Feature[], cols: string[]) {
  return Object.fromEntries(
    cols
      .map((c) => {
        const scores = features
          .map((f) => realScore(f.products[c]))
          .filter((x): x is number => typeof x === "number");
        return [c, scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null];
      })
      .filter(([, v]) => v != null)
  ) as Record<string, number>;
}

const _mean = (xs: number[]): number | null =>
  xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

/** 缺格的「评估式推测」:确定性插值——优先用该产品在其他维度的真实均分,
 *  否则用该维度在其他产品的真实均分。透明可解释、不调 LLM、不写回 schema,只用于 UI 兜底展示。 */
function cellEstimate(
  feature: Feature,
  product: string,
  features: Feature[],
  cols: string[]
): { value: number; basis: string } | null {
  const byProduct = _mean(
    features.map((f) => realScore(f.products[product])).filter((x): x is number => x != null)
  );
  if (byProduct != null) return { value: byProduct, basis: "推测·基于该产品其他维度均值" };
  const byFeature = _mean(
    cols.map((c) => realScore(feature.products[c])).filter((x): x is number => x != null)
  );
  if (byFeature != null) return { value: byFeature, basis: "推测·基于该维度其他产品均值" };
  return null;
}

const _scoreTone = (v: number) =>
  v >= 4 ? "bg-emerald-400" : v >= 2.5 ? "bg-sky-400" : "bg-amber-400";

/** 分数可视化条:real=实色实测,estimate=虚化+「推测」,none=灰虚线「未评分」。 */
function ScoreBar({
  value,
  kind,
  scale = 5,
  title,
}: {
  value: number | null;
  kind: "real" | "estimate" | "none";
  scale?: number;
  title?: string;
}) {
  if (kind === "none" || value == null) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] text-neutral-600" title={title}>
        <span className="h-1.5 w-12 rounded-full border border-dashed border-white/15" />
        未评分
      </span>
    );
  }
  const pct = Math.max(4, Math.min(100, (value / scale) * 100));
  const est = kind === "estimate";
  return (
    <span className="inline-flex items-center gap-1.5" title={title}>
      <span className="relative h-1.5 w-12 overflow-hidden rounded-full bg-white/10">
        <span
          className={`absolute inset-y-0 left-0 rounded-full ${_scoreTone(value)} ${est ? "opacity-40" : ""}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className={`tabular-nums text-[11px] ${est ? "text-neutral-500" : "text-neutral-200"}`}>
        {est ? `~${value.toFixed(1)}` : Number(value.toFixed(1))}
        <span className="text-neutral-600">/{scale}</span>
      </span>
      {est && (
        <span className="rounded bg-white/5 px-1 text-[9px] leading-tight text-neutral-500">推测</span>
      )}
    </span>
  );
}

function evidenceStats(report: Report) {
  const count = (key: keyof Report["raw_evidence"][number]) => {
    const out: Record<string, number> = {};
    for (const e of report.raw_evidence) {
      const k = String(e[key] ?? "unknown");
      out[k] = (out[k] ?? 0) + 1;
    }
    return Object.entries(out).sort((a, b) => b[1] - a[1]);
  };
  return {
    byClaim: count("claim_type"),
    byBias: count("source_bias"),
    byType: count("source_type"),
  };
}

function fallbackQualityDimensions(report: Report) {
  if (report.quality_report?.quality_dimensions) {
    return Object.entries(report.quality_report.quality_dimensions).map(([id, d]) => ({ id, ...d }));
  }
  const stats = evidenceStats(report);
  const hasUser = stats.byBias.some(([k]) => k === "user_generated");
  const hasThird = stats.byBias.some(([k]) => k === "third_party");
  const vendorOnly = report.raw_evidence.length > 0 && stats.byBias.length === 1 && stats.byBias[0][0] === "vendor_claim";
  return [
    { id: "evidence_coverage", label: "证据覆盖度", score: Math.min(100, stats.byClaim.length * 25), note: "按四类证据覆盖估算" },
    { id: "source_credibility", label: "来源可信度", score: vendorOnly ? 55 : hasUser && hasThird ? 88 : 72, note: "厂商/用户/第三方来源结构" },
    { id: "traceability", label: "结论可追溯性", score: 90, note: "基于报告 evidence chip 估算" },
  ];
}

function uncertaintyNotes(report: Report) {
  const stats = evidenceStats(report);
  const notes: string[] = [];
  if (report.raw_evidence.length > 0 && stats.byBias.length === 1 && stats.byBias[0][0] === "vendor_claim") {
    notes.push("当前证据主要来自厂商官方材料，体验质量和用户痛点需要用户侧或第三方证据交叉验证。");
  }
  const stale = report.raw_evidence.filter((e) => e.source_freshness === "stale").length;
  if (stale) notes.push(`${stale} 条证据超过 TTL，定价或功能判断需复核最新页面。`);
  if (!(report.schema_draft?.recommendations ?? []).length) {
    notes.push("缺少可执行建议，当前报告还不能直接作为排期依据。");
  }
  const swot = report.schema_draft?.swot;
  if (swot && ![...swot.strengths, ...swot.weaknesses, ...swot.opportunities, ...swot.threats].length) {
    notes.push("SWOT 为空，说明事实层到战略判断层推导不足。");
  }
  return notes.length ? notes : ["未发现明显信息缺口；关键决策前仍建议复核最新定价页和用户侧反馈。"];
}

function confidenceSummary(report: Report) {
  const ev = report.raw_evidence ?? [];
  const hasUser = ev.some((e) => e.source_bias === "user_generated");
  const hasThird = ev.some((e) => e.source_bias === "third_party");
  const hasPricing = ev.some((e) => e.claim_type === "pricing");
  const hasQuality = ev.some((e) => e.claim_type === "performance_quality" || e.claim_type === "user_pain");
  const level = ev.length >= 12 && hasUser && hasThird && hasPricing && hasQuality
    ? "high"
    : ev.length >= 6 && hasPricing && (hasUser || hasThird || hasQuality)
      ? "medium"
      : "low";
  const missing: string[] = [];
  if (!hasUser) missing.push("真实用户反馈");
  if (!hasThird) missing.push("第三方评测");
  if (!hasQuality) missing.push("性能/留存类证据");
  if (!hasPricing) missing.push("明确价格页");
  const label = level === "high" ? "高" : level === "medium" ? "中等" : "低";
  const note = missing.length
    ? `当前证据主要覆盖公开资料，仍缺 ${missing.slice(0, 3).join("、")}。`
    : "证据已覆盖价格、用户侧和第三方材料，仍建议在采购或立项前复核最新页面。";
  return { level, label, note };
}

function evidenceIdsForProduct(report: Report, product: string, limit = 4) {
  const ids: string[] = [];
  for (const f of report.schema_draft?.feature_tree?.features ?? []) {
    const cell = f.products[product];
    ids.push(...(cell?.quality_score?.evidence_ids ?? []), ...(cell?.support_evidence_ids ?? []));
    if (f.gap?.winner === product) ids.push(...(f.gap.evidence_ids ?? []));
  }
  for (const p of report.schema_draft?.pricing_model?.products ?? []) {
    if (p.name === product) {
      for (const tier of p.tiers) ids.push(...(tier.evidence_ids ?? []));
    }
  }
  return [...new Set(ids)].slice(0, limit);
}

function productFitLines(report: Report, cols: string[]) {
  const features = report.schema_draft?.feature_tree?.features ?? [];
  const avgs = averageScores(features, cols);
  const pos = new Map((report.schema_draft?.positioning_map?.products ?? []).map((p) => [p.name, p]));
  return cols.slice(0, 4).map((name) => {
    const p = pos.get(name);
    const wins = features.filter((f) => f.gap?.winner === name).slice(0, 2).map((f) => f.name);
    const score = avgs[name];
    const scenario = p?.core_scenario || p?.value_proposition || p?.target_user;
    const basis = [
      scenario,
      wins.length ? `相对强项: ${wins.join("、")}` : null,
      score != null ? `已验证体验均分 ${score.toFixed(1)}/5` : null,
    ].filter(Boolean).join("；");
    return {
      product: name,
      text: basis ? `${name}: ${basis}` : `${name}: 当前证据不足，暂不做强弱判断`,
      ids: evidenceIdsForProduct(report, name),
    };
  });
}

function audienceRecommendations(report: Report, cols: string[], recs: Recommendation[]) {
  const features = report.schema_draft?.feature_tree?.features ?? [];
  const avgs = averageScores(features, cols);
  const pricing = report.schema_draft?.pricing_model?.products ?? [];
  const byName = Object.fromEntries(pricing.map((p) => [p.name, p]));
  const scored = cols.filter((c) => avgs[c] != null).sort((a, b) => (avgs[b] ?? 0) - (avgs[a] ?? 0));
  const priced = cols
    .map((c) => ({ name: c, price: minMonthlyPrice(byName[c]) }))
    .filter((p): p is { name: string; price: number } => p.price != null)
    .sort((a, b) => a.price - b.price);
  const enterprise = cols.find((c) =>
    /github|copilot|enterprise|team/i.test(c) ||
    (byName[c]?.tiers ?? []).some((t) => /team|enterprise|business|团队|企业/i.test(`${t.tier_name} ${t.segment ?? ""}`))
  );
  const controllable = cols.find((c) => /cline|open|self|local|oss/i.test(c));
  const topAction = recs.find((r) => (r.action_type ?? "").toString().toLowerCase() === "attack") ?? recs[0];
  return [
    { label: "个人/高频开发", value: scored[0] || priced[0]?.name || cols[0] || "待确认" },
    { label: "预算敏感/可控成本", value: controllable || priced[0]?.name || "待确认" },
    { label: "团队/企业采购", value: enterprise || scored[0] || "待确认" },
    { label: "产品机会", value: topAction?.action || "补齐用户侧证据后再判断机会点" },
  ];
}

export default function ReportView({ report }: { report: Report }) {
  const [showRaw, setShowRaw] = useState(false);
  const s = report.schema_draft;

  if (!s) {
    return <p className="text-neutral-500">无结构化数据</p>;
  }

  // 痛点归因类分析:能力评分仅辅助,缺证据不做「推测」兜底
  const painMode = report.meta.analysis_intent === "pain_attribution";
  // 合成(模拟访谈)证据 id 集合——用于给"仅靠合成数据支撑"的结论打「模拟」标,避免误读为真实发现
  const syntheticIds = new Set(
    (report.raw_evidence ?? [])
      .filter((e) => (e.source_url ?? "").startsWith("synthetic"))
      .map((e) => e.evidence_id)
  );
  const cols = s.feature_tree
    ? Object.keys(s.feature_tree.features[0]?.products ?? {})
    : [report.meta.target_product, ...report.meta.competitors];
  const recs = [...(s.recommendations ?? [])].sort(
    (a, b) => (b.priority_score?.final_score ?? 0) - (a.priority_score?.final_score ?? 0)
  );
  const stats = evidenceStats(report);
  const dimensions = fallbackQualityDimensions(report);
  const uncertainties = uncertaintyNotes(report);
  const confidence = confidenceSummary(report);
  const navSections = [
    { id: "evidence", label: "证据概览", show: true },
    { id: "decision", label: "决策摘要", show: true },
    { id: "positioning", label: "产品定位", show: Boolean(s.competitor_landscape) || (s.positioning_map?.products?.length ?? 0) > 0 },
    { id: "workflow", label: "核心场景", show: Boolean(s.feature_tree) },
    { id: "matrix", label: "功能矩阵", show: Boolean(s.feature_tree) },
    { id: "pricing", label: "定价模型", show: Boolean(s.pricing_model) },
    { id: "personas", label: "用户场景", show: Boolean(s.user_persona) },
    { id: "strategy", label: "机会/风险", show: true },
    { id: "swot", label: "SWOT", show: Boolean(s.swot) },
    { id: "appendix", label: "详细附录", show: true },
  ].filter((sec) => sec.show);

  return (
    <EvidenceProvider evidence={report.raw_evidence}>
      <div className="space-y-8 ca-stagger">
        {/* §0 报告头部 */}
        <ReportHeader report={report} cols={cols} recs={recs} confidence={confidence} />

        {/* Sub-nav */}
        <nav className="sticky top-0 z-10 -mx-2 flex flex-wrap gap-1 bg-neutral-950/80 px-2 py-2 backdrop-blur">
          {navSections.map((sec) => (
            <a
              key={sec.id}
              href={`#${sec.id}`}
              className="rounded-lg px-3 py-1 text-xs text-neutral-400 transition hover:bg-white/5 hover:text-sky-300"
            >
              {sec.label}
            </a>
          ))}
        </nav>

        {/* 证据与生成概览 */}
        <section className="space-y-3">
          <SectionTitle id="evidence">证据与生成概览</SectionTitle>
          {report.completeness && <CompletenessCard data={report.completeness} />}
          <div className="grid gap-3 lg:grid-cols-[1fr_1fr]">
            <QualityBreakdown dimensions={dimensions} />
            <EvidenceCoverage byClaim={stats.byClaim} byBias={stats.byBias} byType={stats.byType} />
          </div>
          {report.business_value && <BusinessValueCard data={report.business_value} />}
          <RunTracePanel report={report} />
        </section>

        {/* §1 决策摘要 */}
        <DecisionBriefCard report={report} cols={cols} recs={recs} />

        {/* §2 产品定位对比 */}
        {(s.competitor_landscape || (s.positioning_map?.products?.length ?? 0) > 0) && (
          <ProductPositioningSection report={report} cols={cols} />
        )}

        {/* §3 核心场景对比 */}
        {s.feature_tree && <WorkflowComparisonSection featureTree={s.feature_tree} cols={cols} />}

        {/* §4 功能矩阵与权重评分 */}
        {s.feature_tree && (
          <section className="space-y-3">
            <SectionTitle id="matrix">功能矩阵与权重评分 · {s.feature_tree.category}</SectionTitle>
            <ScoringRubric />
            <CapabilityChart features={s.feature_tree.features} cols={cols} painMode={painMode} />
            <FeatureMatrix features={s.feature_tree.features} cols={cols} painMode={painMode} />
            {s.feature_tree.analysis && (
              <FeatureCoverageCard analysis={s.feature_tree.analysis} cols={cols} target={report.meta.target_product} />
            )}
          </section>
        )}

        {/* §5 定价模型与性价比 */}
        {s.pricing_model && (
          <section className="space-y-3">
            <SectionTitle id="pricing">定价模型与性价比</SectionTitle>
            <PricingTakeaways model={s.pricing_model} cols={cols} features={s.feature_tree?.features ?? []} target={report.meta.target_product} />
            <div className="grid gap-3 md:grid-cols-2">
              {s.pricing_model.products.map((p) => (
                <PricingCard key={p.name} product={p} />
              ))}
            </div>
            {s.feature_tree && <PriceAbilityMap products={s.pricing_model.products} features={s.feature_tree.features} cols={cols} target={report.meta.target_product} />}
            <PricingInsights model={s.pricing_model} />
          </section>
        )}

        {/* §6 用户画像与使用场景 */}
        {s.user_persona && (
          <UserScenarioSection
            report={report}
            cols={cols}
            syntheticIds={syntheticIds}
          />
        )}

        {/* §7 机会点 / 风险 / 战略建议 */}
        <StrategicAdviceSection report={report} recs={recs} />

        {/* SWOT 独立栏目 */}
        {s.swot && <SwotSection swot={s.swot} />}

        {/* §8 详细附录 */}
        <section className="space-y-3">
          <SectionTitle id="appendix">详细附录</SectionTitle>
          <UncertaintyBox notes={uncertainties} />
          <AppendixNotes />
          <TechCapabilitySection report={report} cols={cols} />
          <MarkdownExportPanel
            report={report}
            open={showRaw}
            onToggle={setShowRaw}
          />
        </section>
      </div>
    </EvidenceProvider>
  );
}

function markdownFilename(report: Report) {
  const target = report.meta.target_product || "report";
  const focus = report.meta.analysis_focus?.[0] || "analysis";
  const id = report.report_id || report.meta.report_id || "competitive-report";
  const raw = `${id}-${target}-${focus}.md`;
  return raw.replace(/[\\/:*?"<>|\s]+/g, "-").replace(/-+/g, "-");
}

const DEPRECATED_MARKDOWN_SECTIONS = ["功能定位", "产品形态", "商业模式", "护城河", "蓝海"];

function stripDeprecatedMarkdownSections(markdown: string) {
  const lines = markdown.split(/\r?\n/);
  const kept: string[] = [];
  let skipping = false;
  for (const line of lines) {
    if (line.startsWith("## ")) {
      const title = line
        .replace(/^##\s+(?:[一二三四五六七八九十]+、\s*)?/, "")
        .trim();
      skipping = DEPRECATED_MARKDOWN_SECTIONS.some((section) => title.includes(section));
    }
    if (!skipping) kept.push(line);
  }
  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function markdownSource(report: Report) {
  const md = report.report_draft?.trim();
  if (md) return stripDeprecatedMarkdownSections(md);
  return [
    `# ${report.meta.target_product} vs ${report.meta.competitors.join(" / ")}`,
    "",
    `- 焦点：${report.meta.analysis_focus.join(" / ")}`,
    `- 报告 ID：${report.report_id}`,
    "",
    "暂无原始 Markdown 正文。",
  ].join("\n");
}

function ReportHeader({
  report,
  cols,
  recs,
  confidence,
}: {
  report: Report;
  cols: string[];
  recs: Recommendation[];
  confidence: ReturnType<typeof confidenceSummary>;
}) {
  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-[0.18em] text-neutral-500">Competitive Report</div>
          <h1 className="mt-2 text-2xl font-semibold text-white">
            {report.meta.target_product}{" "}
            <span className="text-neutral-500">vs {report.meta.competitors.join(" / ")}</span>
          </h1>
          <p className="mt-1 text-sm text-neutral-500">
            分析对象: {report.meta.analysis_focus.join(" / ") || "全维度"} · 数据截止 {report.meta.data_cutoff}
          </p>
        </div>
        <QualityBadge score={report.quality_report?.quality_score} status={report.status} />
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat label="产品组" value={`${cols.length} 个产品`} />
        <Stat label="证据数量" value={`${report.raw_evidence.length} 条`} />
        <Stat label="报告时间" value={report.created_at?.slice(0, 10) || report.meta.generated_at?.slice(0, 10) || "—"} />
        <Stat label="置信度" value={confidence.label} />
        <Stat label="建议数量" value={`${recs.length} 条`} />
      </div>
      <div className="mt-3">
        <OverallGrade report={report} />
      </div>
    </section>
  );
}

function ProductPositioningSection({ report, cols }: { report: Report; cols: string[] }) {
  const products = report.schema_draft?.positioning_map?.products ?? [];
  return (
    <section className="space-y-3">
      <SectionTitle id="positioning">产品定位对比</SectionTitle>
      {products.length > 0 ? <PositioningMapCards products={products} /> : (
        <p className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-sm text-neutral-500">
          当前证据不足，未生成稳定定位象限。
        </p>
      )}
      <ProductDifferenceTable report={report} cols={cols} />
      {report.schema_draft?.competitor_landscape && (
        <CompetitorLandscapeCard landscape={report.schema_draft.competitor_landscape} />
      )}
    </section>
  );
}

function ProductDifferenceTable({ report, cols }: { report: Report; cols: string[] }) {
  const fits = productFitLines(report, cols);
  return (
    <div className="overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-white/5">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">产品</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">核心差异</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">证据</th>
          </tr>
        </thead>
        <tbody>
          {fits.map((f) => (
            <tr key={f.product} className="border-t border-white/10">
              <td className="px-3 py-2 font-medium text-neutral-100">{f.product}</td>
              <td className="px-3 py-2 leading-relaxed text-neutral-400">{f.text.replace(`${f.product}: `, "")}</td>
              <td className="px-3 py-2"><Chips ids={f.ids} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const WORKFLOW_BUCKETS = [
  { key: "understand", label: "需求理解", patterns: ["需求", "issue", "文档", "上下文", "context", "read"] },
  { key: "generate", label: "代码生成", patterns: ["生成", "补全", "completion", "generate", "chat"] },
  { key: "edit", label: "多文件修改", patterns: ["多文件", "跨文件", "编辑", "diff", "修改", "multi"] },
  { key: "verify", label: "执行验证", patterns: ["执行", "终端", "测试", "验证", "run", "test", "terminal"] },
  { key: "collab", label: "团队协作", patterns: ["团队", "协作", "PR", "review", "权限", "审计", "enterprise"] },
  { key: "cost", label: "成本控制", patterns: ["成本", "价格", "用量", "模型", "自托管", "cost", "usage"] },
];

function workflowBucketOf(feature: Feature) {
  const text = `${feature.name} ${feature.feature_id}`.toLowerCase();
  return WORKFLOW_BUCKETS.find((b) => b.patterns.some((p) => text.includes(p.toLowerCase()))) ?? WORKFLOW_BUCKETS[1];
}

function isAiCodingWorkflow(featureTree: FeatureTree) {
  if (featureTree.source_skill === "ai_coding") return true;
  const text = `${featureTree.category ?? ""} ${featureTree.features.map((f) => f.name).join(" ")}`;
  return /代码|编程|IDE|Agent|补全|重构|测试|终端/i.test(text);
}

function featureWinner(feature: Feature) {
  const winner = feature.gap?.winner;
  if (!winner || winner === "tie" || winner === "unclear" || winner === "unknown") return "待确认";
  return winner;
}

function featureEvidenceSummary(feature: Feature, cols: string[]) {
  const missing: string[] = [];
  let knownCount = 0;
  for (const c of cols) {
    const cell = feature.products[c];
    const known = realScore(cell) != null || cell?.support_status === "supported" || cell?.support_status === "partially_supported";
    if (known) knownCount += 1;
    else missing.push(c);
  }
  if (!missing.length) return `${knownCount}/${cols.length} 产品有证据`;
  return `${knownCount}/${cols.length} 产品有证据；缺 ${missing.join("、")}`;
}

function WorkflowComparisonSection({ featureTree, cols }: { featureTree: FeatureTree; cols: string[] }) {
  const features = featureTree.features ?? [];
  const aiCoding = isAiCodingWorkflow(featureTree);
  const rows = aiCoding ? WORKFLOW_BUCKETS.map((bucket) => {
    const matched = features.filter((f) => workflowBucketOf(f).key === bucket.key);
    const winnerCount: Record<string, number> = {};
    for (const f of matched) {
      const winner = f.gap?.winner;
      if (winner && winner !== "tie" && winner !== "unclear") winnerCount[winner] = (winnerCount[winner] ?? 0) + 1;
    }
    const winner = Object.entries(winnerCount).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "待确认";
    const coverage = cols.map((c) => {
      const known = matched.filter((f) => realScore(f.products[c]) != null || f.products[c]?.support_status === "supported" || f.products[c]?.support_status === "partially_supported").length;
      return `${c}: ${matched.length ? Math.round((known / matched.length) * 100) : 0}%`;
    });
    return { key: bucket.key, label: bucket.label, capability: matched.slice(0, 4).map((f) => f.name).join(" / "), winner, coverage };
  }).filter((r) => r.capability) : features.slice(0, 6).map((feature) => ({
    key: feature.feature_id,
    label: feature.name,
    capability: feature.gap?.reason || "按当前证据做单项能力判断",
    winner: featureWinner(feature),
    coverage: [featureEvidenceSummary(feature, cols)],
  }));

  if (!rows.length) return null;

  return (
    <section className="space-y-3">
      <SectionTitle id="workflow">核心场景对比</SectionTitle>
      {!aiCoding && (
        <p className="text-xs leading-relaxed text-neutral-500">
          当前报告不是 AI 编程工具，场景按本次功能树生成；只展示有结构化维度的核心场景，避免套用固定模板。
        </p>
      )}
      <div className="overflow-x-auto rounded-xl border border-white/10">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-white/5">
            <tr>
              <th className="px-3 py-2 text-left font-medium text-neutral-300">{aiCoding ? "开发者工作流" : "核心能力/场景"}</th>
              <th className="px-3 py-2 text-left font-medium text-neutral-300">{aiCoding ? "覆盖功能" : "判断摘要"}</th>
              <th className="px-3 py-2 text-left font-medium text-neutral-300">当前 winner</th>
              <th className="px-3 py-2 text-left font-medium text-neutral-300">{aiCoding ? "覆盖度" : "证据状态"}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-white/10">
                <td className="px-3 py-3 font-medium text-neutral-100">{r.label}</td>
                <td className="max-w-md px-3 py-3 text-xs leading-relaxed text-neutral-400">
                  {r.capability || "证据不足"}
                </td>
                <td className="px-3 py-3 text-sky-300">{r.winner}</td>
                <td className="px-3 py-3 text-xs leading-relaxed text-neutral-500">{r.coverage.join("；")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {features.length > rows.length && (
        <p className="text-xs text-neutral-600">其余 {features.length - rows.length} 个低优先级维度已收起，可在“功能矩阵”查看完整评分。</p>
      )}
    </section>
  );
}

function UserScenarioSection({
  report,
  cols,
  syntheticIds,
}: {
  report: Report;
  cols: string[];
  syntheticIds: Set<string>;
}) {
  const persona = report.schema_draft?.user_persona;
  if (!persona) return null;
  return (
    <section className="space-y-3">
      <SectionTitle id="personas">用户画像与使用场景</SectionTitle>
      {report.research_method && <ResearchMethodCard data={report.research_method} />}
      <div className="grid gap-3 md:grid-cols-2">
        {(persona.user_segments ?? []).map((s) => (
          <div key={s.segment_id} className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
            <div className="text-sm font-medium text-neutral-100">{s.name}</div>
            <p className="mt-1 text-sm leading-relaxed text-neutral-400">{s.description || "暂无画像说明"}</p>
            <Chips ids={s.evidence_ids} />
          </div>
        ))}
        {(!persona.user_segments || persona.user_segments.length === 0) && (
          <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-sm text-neutral-500">
            使用人群画像证据不足，暂不硬写。
          </div>
        )}
      </div>
      <div className="grid gap-2">
        {persona.pain_points.map((p) => (
          <PainRow key={p.pain_id} pain={p} syntheticIds={syntheticIds} />
        ))}
      </div>
      <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-xs leading-relaxed text-neutral-500">
        迁移成本判断: 当前基于 {cols.join(" / ")} 的功能、价格与证据覆盖做弱判断；如需采购级结论，需要补企业权限、审计、真实留存和迁移案例。
      </div>
    </section>
  );
}

function StrategicAdviceSection({ report, recs }: { report: Report; recs: Recommendation[] }) {
  const analysis = report.schema_draft?.feature_tree?.analysis;
  const moatItems = (analysis?.moat_candidates ?? []).map((m) =>
    `${m.name}${m.factors?.length ? `: ${m.factors.join("、")}` : ""}`
  );
  const whitespace = analysis?.whitespace ?? [];
  const whitespaceItems = whitespace.map((w) => `${w.name}: ${w.reason || w.barrier || "待验证"}`);
  const recommendationOpportunities = recs
    .filter((r) => (r.action_type ?? "").toString().toLowerCase() === "attack")
    .slice(0, 3)
    .map((r) => `${r.action}${r.target_competitor ? `（对标 ${Array.isArray(r.target_competitor) ? r.target_competitor.join("、") : r.target_competitor}）` : ""}`);
  const opportunityItems = whitespaceItems.length ? whitespaceItems : recommendationOpportunities;
  const swot = report.schema_draft?.swot;
  const riskItems = [...(swot?.weaknesses ?? []), ...(swot?.threats ?? [])].slice(0, 4).map((r) => r.point);
  const panels = [
    moatItems.length ? { title: "护城河", items: moatItems, tone: "emerald" as const } : null,
    opportunityItems.length
      ? { title: whitespaceItems.length ? "蓝海机会" : "可验证机会", items: opportunityItems, tone: "sky" as const }
      : null,
    riskItems.length ? { title: "风险", items: riskItems, tone: "amber" as const } : null,
  ].filter(Boolean) as { title: string; items: string[]; tone: "emerald" | "sky" | "amber" }[];
  const gridClass = panels.length >= 3 ? "grid gap-3 lg:grid-cols-3" : panels.length === 2 ? "grid gap-3 lg:grid-cols-2" : "grid gap-3";
  const hiddenNotes = [
    !moatItems.length ? "护城河需要难复制因素证据，本次不单独占栏。" : "",
    !whitespaceItems.length
      ? opportunityItems.length
        ? "蓝海缺少高置信空白区证据，已降级展示建议中的可验证机会。"
        : "蓝海证据不足，本次不单独占栏。"
      : "",
  ].filter(Boolean);
  return (
    <section className="space-y-3">
      <SectionTitle id="strategy">机会点 / 风险 / 战略建议</SectionTitle>
      {panels.length > 0 ? (
        <div className={gridClass}>
          {panels.map((panel) => (
            <InsightList key={panel.title} title={panel.title} items={panel.items} tone={panel.tone} />
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-sm leading-relaxed text-neutral-500">
          暂无高置信机会或风险信号；建议先补齐用户痛点、定价与关键功能证据。
        </div>
      )}
      {hiddenNotes.length > 0 && (
        <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-xs leading-relaxed text-neutral-500">
          {hiddenNotes.join(" ")}
        </div>
      )}
      <GroupedRecommendations recs={recs} />
    </section>
  );
}

function InsightList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "emerald" | "sky" | "amber";
}) {
  const toneClass = tone === "emerald" ? "text-emerald-300" : tone === "sky" ? "text-sky-300" : "text-amber-300";
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className={`text-sm font-medium ${toneClass}`}>{title}</div>
      <ul className="mt-2 space-y-1.5 text-sm leading-relaxed text-neutral-400">
        {items.slice(0, 4).map((item, i) => <li key={i}>• {item}</li>)}
      </ul>
    </div>
  );
}

function SwotSection({ swot }: { swot: Swot }) {
  return (
    <section className="space-y-3">
      <SectionTitle id="swot">SWOT</SectionTitle>
      <div className="grid gap-3 sm:grid-cols-2">
        <SwotQuad title="优势 S" items={swot.strengths} tone="emerald" />
        <SwotQuad title="劣势 W" items={swot.weaknesses} tone="red" />
        <SwotQuad title="机会 O" items={swot.opportunities} tone="sky" />
        <SwotQuad title="威胁 T" items={swot.threats} tone="amber" />
      </div>
    </section>
  );
}

function AppendixNotes() {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-xs leading-relaxed text-neutral-500">
      PEST 和详细宏观战略分析默认不进入正文；当前报告未生成 PEST，避免对开发工具竞品分析硬套行业宏观框架。
    </div>
  );
}

function RunTracePanel({ report }: { report: Report }) {
  if (!report.stage_timings || report.stage_timings.length === 0) return null;
  const totalTokens = report.stage_timings.reduce((a, t) => a + (t.tokens || 0), 0);
  const totalDuration = report.stage_timings.reduce((a, t) => a + (t.duration_sec || 0), 0);
  return (
    <details className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-sm">
      <summary className="cursor-pointer text-neutral-400">
        生成过程 · 共 {totalDuration}s{totalTokens > 0 && ` · ${totalTokens.toLocaleString()} tokens`}
      </summary>
      <div className="mt-2 space-y-1.5">
        {report.stage_timings.map((t, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            <span className="shrink-0">{t.icon ?? "•"}</span>
            <span className="shrink-0 text-neutral-300">{t.label}</span>
            <span className="shrink-0 font-mono text-emerald-400">{t.duration_sec}s</span>
            {t.tokens ? <span className="shrink-0 font-mono text-sky-400/70">{t.tokens >= 1000 ? `${(t.tokens / 1000).toFixed(1)}k` : t.tokens} tokens</span> : null}
            {t.result && <span className="text-neutral-500">· {t.result}</span>}
          </div>
        ))}
      </div>
    </details>
  );
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function MarkdownExportPanel({
  report,
  open,
  onToggle,
}: {
  report: Report;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  const [copied, setCopied] = useState(false);
  const text = markdownSource(report);
  const filename = markdownFilename(report);

  async function onCopy() {
    await copyText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  function onDownload() {
    const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return (
    <details
      open={open}
      onToggle={(e) => onToggle((e.target as HTMLDetailsElement).open)}
      className="rounded-xl border border-white/10 bg-white/[0.02]"
    >
      <summary className="cursor-pointer px-4 py-3 text-sm text-neutral-400">
        查看与导出原始 Markdown 报告
      </summary>
      <div className="space-y-4 border-t border-white/10 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-medium text-neutral-200">Markdown 源码</div>
            <div className="mt-0.5 text-xs text-neutral-500">{filename}</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onCopy}
              className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-neutral-300 transition hover:border-sky-500/40 hover:text-sky-300"
            >
              {copied ? "已复制" : "复制 Markdown"}
            </button>
            <button
              type="button"
              onClick={onDownload}
              className="rounded-lg bg-sky-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-sky-400"
            >
              下载 .md
            </button>
          </div>
        </div>

        <textarea
          readOnly
          value={text}
          className="min-h-72 w-full resize-y rounded-xl border border-white/10 bg-neutral-950/70 p-3 font-mono text-xs leading-relaxed text-neutral-200 outline-none selection:bg-sky-500/30"
          spellCheck={false}
        />

        <details className="rounded-xl border border-white/10 bg-white/[0.02]">
          <summary className="cursor-pointer px-3 py-2 text-xs text-neutral-500">渲染预览</summary>
          <div className="prose-report border-t border-white/10 px-4 py-3 text-sm text-neutral-300">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={proseComponents}>
              {text}
            </ReactMarkdown>
          </div>
        </details>
      </div>
    </details>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className="text-xs text-neutral-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-white">{value}</div>
    </div>
  );
}

function gradeOf(score: number): { letter: string; label: string; tone: string } {
  if (score >= 85) return { letter: "A", label: "优秀", tone: "text-emerald-400" };
  if (score >= 70) return { letter: "B", label: "良好", tone: "text-sky-400" };
  if (score >= 55) return { letter: "C", label: "及格", tone: "text-amber-400" };
  return { letter: "D", label: "待补强", tone: "text-red-400" };
}

function OverallGrade({ report }: { report: Report }) {
  const quality = report.quality_report?.quality_score;
  const completeness = report.completeness?.overall;
  const parts: { label: string; value: number }[] = [];
  if (typeof quality === "number") parts.push({ label: "质检规范", value: quality });
  if (typeof completeness === "number") parts.push({ label: "任务完成度", value: completeness });
  if (parts.length === 0) return null;

  const overall = Math.round(parts.reduce((a, p) => a + p.value, 0) / parts.length);
  const g = gradeOf(overall);
  const statusLabel =
    report.status === "passed" ? "已通过质检" : report.status === "degraded" ? "降级输出" : report.status;

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm">
      <span className={`font-mono text-lg font-bold leading-none ${g.tone}`}>{g.letter}</span>
      <span className="text-neutral-400">综合评级</span>
      <span className={`font-mono text-base font-semibold ${g.tone}`}>
        {overall}
        <span className="text-xs font-normal text-neutral-600">/100</span>
      </span>
      <span className="text-xs text-neutral-600">· {statusLabel}</span>
      <span className="ml-auto flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-500">
        {parts.map((p) => (
          <span key={p.label}>
            {p.label} <span className="font-mono text-neutral-300">{Math.round(p.value)}</span>
          </span>
        ))}
      </span>
    </div>
  );
}

function ResearchMethodCard({ data }: { data: ResearchMethod }) {
  return (
    <div className="rounded-xl border border-violet-500/20 bg-violet-500/[0.05] p-4">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-neutral-200">调研方法 · 问卷 + 模拟访谈</span>
        <span className="rounded bg-violet-500/15 px-2 py-0.5 text-[11px] text-violet-300">
          合成数据
        </span>
      </div>
      <div className="mt-1 text-[11px] text-neutral-500">{data.method}</div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-neutral-400">
        <span>问卷 <span className="font-mono text-neutral-200">{data.questions.length}</span> 题</span>
        <span>模拟访谈 <span className="font-mono text-neutral-200">{data.n_findings}</span> 条</span>
        <span>受访画像 <span className="font-mono text-neutral-200">{data.personas.length}</span> 类</span>
      </div>
      {data.questions.length > 0 && (
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-xs text-neutral-300">
          {data.questions.map((q, i) => (
            <li key={q.id ?? i}>{q.text}</li>
          ))}
        </ol>
      )}
      {data.personas.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {data.personas.map((p, i) => (
            <span key={i} className="rounded bg-white/5 px-2 py-0.5 text-[11px] text-neutral-400">
              {p}
            </span>
          ))}
        </div>
      )}
      {data.findings && data.findings.length > 0 && (
        <details className="group mt-3" open>
          <summary className="cursor-pointer select-none text-[11px] font-medium text-neutral-400 hover:text-neutral-200">
            模拟访谈记录 · {data.findings.length} 条回答（点击展开/收起）
          </summary>
          <div className="mt-2 space-y-2">
            {data.findings.map((f, i) => {
              const q = data.questions.find((qq) => qq.id === f.question_id);
              return (
                <div
                  key={f.evidence_id ?? i}
                  className="rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-violet-300">
                      {f.persona}
                    </span>
                    {f.product && <span className="text-neutral-500">{f.product}</span>}
                    {f.claim_type && (
                      <span className="rounded bg-white/5 px-1.5 py-0.5 text-neutral-500">
                        {CLAIM_TYPE_LABEL[f.claim_type] ?? f.claim_type}
                      </span>
                    )}
                  </div>
                  {q && <div className="mt-1 text-[11px] text-neutral-500">Q：{q.text}</div>}
                  <div className="mt-0.5 text-xs leading-relaxed text-neutral-300">{f.finding}</div>
                  {f.expectation && (
                    <div className="mt-0.5 text-[11px] text-amber-300/80">期望：{f.expectation}</div>
                  )}
                </div>
              );
            })}
          </div>
        </details>
      )}
    </div>
  );
}

const CLAIM_TYPE_LABEL: Record<string, string> = {
  user_pain: "用户痛点",
  performance_quality: "性能与质量",
  feature_existence: "功能具备性",
  pricing: "定价",
};

function BusinessValueCard({ data }: { data: BusinessValue }) {
  return (
    <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/[0.05] p-5">
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-sm font-medium text-neutral-200">业务价值 · vs 传统人工</div>
        {data.headline && <div className="text-xs font-medium text-emerald-300">{data.headline}</div>}
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-neutral-500">
              <th className="py-1 pr-3 text-left font-normal">指标</th>
              <th className="py-1 pr-3 text-left font-normal">人工(估算)</th>
              <th className="py-1 pr-3 text-left font-normal">本系统(实测)</th>
              <th className="py-1 text-left font-normal">提升</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.metric} className="border-t border-white/5">
                <td className="py-1.5 pr-3 text-neutral-300">{r.metric}</td>
                <td className="py-1.5 pr-3 text-neutral-500">{r.manual}</td>
                <td className="py-1.5 pr-3 font-mono text-neutral-100">{r.system}</td>
                <td className="py-1.5 text-emerald-400">{r.delta}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.assumptions && (
        <div className="mt-2 text-[11px] leading-relaxed text-neutral-600">{data.assumptions}</div>
      )}
    </div>
  );
}

function CompletenessCard({ data }: { data: Completeness }) {
  const tone =
    data.overall >= 75 ? "text-emerald-400" : data.overall >= 50 ? "text-amber-400" : "text-red-400";
  return (
    <div className="rounded-xl border border-sky-500/20 bg-sky-500/[0.06] p-4">
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-sm font-medium text-neutral-200">任务完成度</div>
        <div className={`font-mono text-2xl font-semibold ${tone}`}>
          {data.overall}
          <span className="text-sm text-neutral-500">/100</span>
        </div>
      </div>
      <div className="mt-1 text-[11px] text-neutral-500">
        {data.counts.features} 功能 · {data.counts.products} 产品 · {data.counts.recommendations} 建议 ·{" "}
        {data.counts.swot_items} SWOT 项
      </div>
      <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
        {data.aspects.map((a) => {
          const pct = Math.round((a.value || 0) * 100);
          return (
            <div key={a.key}>
              <div className="mb-1 flex items-center justify-between gap-2 text-xs">
                <span className="text-neutral-300">{a.label}</span>
                <span className="font-mono text-neutral-400">{pct}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-400"
                  style={{ width: `${Math.max(4, Math.min(100, pct))}%` }}
                />
              </div>
              {a.detail && (
                <div className="mt-1 text-[11px] leading-relaxed text-neutral-600">{a.detail}</div>
              )}
            </div>
          );
        })}
      </div>
      {data.missing_action_fields && data.missing_action_fields.length > 0 && (
        <div className="mt-3 text-[11px] text-amber-400/80">
          ⚠ 建议普遍缺字段：{data.missing_action_fields.join("、")}
        </div>
      )}
    </div>
  );
}

function QualityBreakdown({
  dimensions,
}: {
  dimensions: { id: string; label: string; score: number; note?: string }[];
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className="mb-3 text-sm font-medium text-neutral-200">质量子分</div>
      <div className="space-y-2.5">
        {dimensions.map((d) => (
          <div key={d.id}>
            <div className="mb-1 flex items-center justify-between gap-3 text-xs">
              <span className="text-neutral-300">{d.label}</span>
              <span className="font-mono text-neutral-400">{d.score}/100</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-400"
                style={{ width: `${Math.max(4, Math.min(100, d.score))}%` }}
              />
            </div>
            {d.note && <div className="mt-1 text-[11px] leading-relaxed text-neutral-600">{d.note}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

function EvidenceCoverage({
  byClaim,
  byBias,
  byType,
}: {
  byClaim: [string, number][];
  byBias: [string, number][];
  byType: [string, number][];
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className="mb-3 text-sm font-medium text-neutral-200">证据覆盖地图</div>
      <MiniBars title="证据类型" rows={byClaim} />
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <MiniList title="来源立场" rows={byBias} />
        <MiniList title="来源类型" rows={byType.slice(0, 5)} />
      </div>
    </div>
  );
}

function MiniBars({ title, rows }: { title: string; rows: [string, number][] }) {
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <div>
      <div className="mb-2 text-xs text-neutral-500">{title}</div>
      <div className="space-y-1.5">
        {rows.map(([k, n]) => (
          <div key={k} className="grid grid-cols-[120px_1fr_36px] items-center gap-2 text-xs">
            <span className="truncate text-neutral-400">{k}</span>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-sky-500/70" style={{ width: `${(n / max) * 100}%` }} />
            </div>
            <span className="text-right font-mono text-neutral-500">{n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MiniList({ title, rows }: { title: string; rows: [string, number][] }) {
  return (
    <div>
      <div className="mb-2 text-xs text-neutral-500">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {rows.map(([k, n]) => (
          <span key={k} className="rounded bg-white/5 px-2 py-1 text-xs text-neutral-300">
            {k} ×{n}
          </span>
        ))}
      </div>
    </div>
  );
}

function UncertaintyBox({ notes }: { notes: string[] }) {
  return (
    <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.06] p-4">
      <div className="mb-2 text-sm font-medium text-amber-200">信息缺口 / 不确定性</div>
      <ul className="space-y-1.5 text-sm leading-relaxed text-neutral-300">
        {notes.map((n, i) => (
          <li key={i}>{i + 1}. {n}</li>
        ))}
      </ul>
    </div>
  );
}

function CapabilityChart({ features, cols, painMode }: { features: Feature[]; cols: string[]; painMode?: boolean }) {
  const rows = cols
    .map((c) => {
      const real = _mean(
        features.map((f) => realScore(f.products[c])).filter((x): x is number => x != null)
      );
      // 痛点归因分析:不做「推测」兜底,缺证据维度坦然按未评分
      const filled = painMode
        ? null
        : _mean(
            features
              .map((f) => realScore(f.products[c]) ?? cellEstimate(f, c, features, cols)?.value ?? null)
              .filter((x): x is number => x != null)
          );
      const n = features.filter((f) => realScore(f.products[c]) != null).length;
      return { name: c, real, filled, n, total: features.length };
    })
    .sort((a, b) => (b.filled ?? b.real ?? -1) - (a.filled ?? a.real ?? -1));

  return (
    <div className="rounded-xl border border-white/10 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-neutral-400">
        <span>能力均分对比（满分 5）</span>
        <span className="flex items-center gap-3 text-[10px] text-neutral-500">
          <span className="flex items-center gap-1"><span className="h-2 w-3 rounded-sm bg-sky-400" />实测</span>
          {!painMode && <span className="flex items-center gap-1"><span className="h-2 w-3 rounded-sm bg-sky-400/30" />含推测兜底</span>}
        </span>
      </div>
      <div className="space-y-2.5">
        {rows.map((r) => (
          <div key={r.name} className="flex items-center gap-3">
            <div className="w-24 shrink-0 truncate text-xs text-neutral-300">{r.name}</div>
            <div className="relative h-4 flex-1 overflow-hidden rounded bg-white/[0.04]">
              {r.filled != null && (
                <div
                  className="absolute inset-y-0 left-0 rounded bg-sky-400/25"
                  style={{ width: `${Math.min(100, (r.filled / 5) * 100)}%` }}
                />
              )}
              {r.real != null && (
                <div
                  className={`absolute inset-y-0 left-0 rounded ${_scoreTone(r.real)}`}
                  style={{ width: `${Math.min(100, (r.real / 5) * 100)}%` }}
                />
              )}
            </div>
            <div className="w-32 shrink-0 text-right text-[11px] tabular-nums">
              {r.real != null ? (
                <span className="text-neutral-200">{r.real.toFixed(1)}</span>
              ) : (
                <span className="text-neutral-600">未评分</span>
              )}
              {r.filled != null && (r.real == null || Math.abs(r.filled - r.real) > 0.05) && (
                <span className="text-neutral-500"> · 含推测~{r.filled.toFixed(1)}</span>
              )}
              <span className="ml-1 text-neutral-600">({r.n}/{r.total})</span>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[10px] leading-relaxed text-neutral-600">
        {painMode
          ? "痛点归因分析：能力评分仅作辅助参考，缺证据的维度按「未评分」处理，不做推测兜底。括号为「已评分维度 / 总维度」。"
          : "实色=有证据的实测均分；浅色=对缺失维度的确定性推测兜底（基于同产品其他维度 / 同维度其他产品均值），仅供横向参考。括号为「已评分维度 / 总维度」。"}
      </p>
    </div>
  );
}

function FeatureMatrix({ features, cols, painMode }: { features: Feature[]; cols: string[]; painMode?: boolean }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="bg-white/5">
            <th className="px-3 py-2 text-left font-medium text-neutral-300">功能</th>
            {cols.map((c) => (
              <th key={c} className="px-3 py-2 text-left font-medium text-neutral-300">
                {c}
              </th>
            ))}
            <th className="px-3 py-2 text-left font-medium text-neutral-300">关键差异</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">产品含义</th>
          </tr>
        </thead>
        <tbody>
          {features.map((f) => (
            <tr key={f.feature_id} className="border-t border-white/10">
              <td className="px-3 py-3 align-top">
                <div className="font-medium text-neutral-100">{f.name}</div>
              </td>
              {cols.map((c) => {
                const cell = f.products[c];
                if (!cell)
                  return (
                    <td key={c} className="px-3 py-3 align-top text-neutral-600">
                      —
                    </td>
                  );
                const scale = cell.quality_score?.scale ?? 5;
                const real = realScore(cell);
                const notSupported = (cell.support_status ?? "").toLowerCase() === "not_supported";
                // 痛点归因分析:不做推测,缺证据显示「未评分」
                const est = !painMode && real == null && !notSupported ? cellEstimate(f, c, features, cols) : null;
                return (
                  <td key={c} className="px-3 py-3 align-top">
                    {notSupported ? (
                      <span className="text-xs text-red-400/80">✕ 不支持</span>
                    ) : (
                      <ScoreBar
                        value={real ?? est?.value ?? null}
                        kind={real != null ? "real" : est ? "estimate" : "none"}
                        scale={scale}
                        title={est?.basis}
                      />
                    )}
                    <Chips ids={cell.quality_score?.evidence_ids ?? cell.support_evidence_ids} />
                  </td>
                );
              })}
              <td className="min-w-64 px-3 py-3 align-top text-xs leading-relaxed text-neutral-400">
                {f.gap?.winner && <span className="text-emerald-400/80">胜出 {f.gap.winner} · </span>}
                {f.gap?.reason ?? "待补充差异解释"}
                <Chips ids={f.gap?.evidence_ids} />
              </td>
              <td className="min-w-48 px-3 py-3 align-top text-xs leading-relaxed text-neutral-400">
                {f.gap?.winner === cols[0]
                  ? "可作为优势叙事继续放大"
                  : f.gap?.winner
                    ? `需要补齐或解释 ${f.gap.winner} 的领先点`
                    : "需要补充证据确认"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PricingCard({ product }: { product: PricingProduct }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className="mb-2 font-medium text-neutral-100">{product.name}</div>
      <div className="space-y-2">
        {product.tiers.map((t) => (
          <div key={t.tier_name} className="text-sm">
            <div className="flex items-baseline justify-between">
              <span className="text-neutral-300">
                {t.tier_name}
                {t.segment && (
                  <span className="ml-1.5 rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-neutral-400">
                    {t.segment}
                  </span>
                )}
              </span>
              <span className="font-mono text-sky-300">
                {t.price?.normalized_usd_month != null
                  ? `$${t.price.normalized_usd_month}/mo`
                  : t.price?.amount != null
                    ? `${t.price.amount} ${t.price.currency ?? ""}`
                    : "—"}
              </span>
            </div>
            {t.display_limits && (
              <div className="mt-0.5 text-xs text-neutral-500">
                {Array.isArray(t.display_limits)
                  ? t.display_limits.slice(0, 3).join(" · ")
                  : t.display_limits}
              </div>
            )}
            <Chips ids={t.evidence_ids} />
          </div>
        ))}
      </div>
    </div>
  );
}

function PricingTakeaways({
  model,
  cols,
  features,
  target,
}: {
  model: PricingModel;
  cols: string[];
  features: Feature[];
  target: string;
}) {
  const byName = Object.fromEntries(model.products.map((p) => [p.name, p]));
  const avgs = averageScores(features, cols);
  const priced = cols
    .flatMap((name) => {
      const price = minMonthlyPrice(byName[name]);
      return price == null ? [] : [{ name, price, score: avgs[name] ?? null }];
    })
    .sort((a, b) => a.price - b.price);
  const cheapest = priced[0]?.name;
  const targetPrice = minMonthlyPrice(byName[target]);
  const highUsage = cols.find((name) => /usage|credit|用量|积分/i.test(byName[name]?.pricing_engine?.archetype ?? ""));
  const enterprise = cols.find((name) =>
    /github|copilot|enterprise|team/i.test(name) ||
    (byName[name]?.tiers ?? []).some((t) => /team|enterprise|business|团队|企业/i.test(`${t.tier_name} ${t.segment ?? ""}`))
  );
  const caveats = [
    targetPrice == null ? `${target} 未抓到明确付费档价格` : null,
    priced.length < cols.length ? "部分产品缺少可比付费价，不能硬算完整单位成本" : null,
  ].filter(Boolean);
  const rows = [
    { k: "谁最便宜", v: cheapest ? `${cheapest} 的入门付费价最低` : "当前价格证据不足" },
    { k: "谁适合个人", v: cheapest || Object.entries(avgs).sort((a, b) => b[1] - a[1])[0]?.[0] || "待确认" },
    { k: "谁适合团队", v: enterprise || "待确认，需补企业采购/权限/审计证据" },
    { k: "成本风险", v: highUsage ? `${highUsage} 更需要关注高频使用下的边际成本` : "未发现明确用量型成本风险" },
  ];
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {rows.map((r) => (
          <div key={r.k}>
            <div className="text-[11px] text-neutral-500">{r.k}</div>
            <div className="mt-1 text-sm leading-relaxed text-neutral-200">{r.v}</div>
          </div>
        ))}
      </div>
      {caveats.length > 0 && (
        <p className="mt-3 border-t border-white/10 pt-3 text-xs leading-relaxed text-amber-300/80">
          {caveats.join("；")}。
        </p>
      )}
    </div>
  );
}

function minMonthlyPrice(product?: PricingProduct) {
  // 0 是「未抓到价格」占位(Analyzer 抽不到数值时填 0),不是真实免费价 — 与后端 _lowest_price 同口径。
  // 排除后该列含义 = 最低付费档,免费档不再把整列刷成 $0/mo。
  const prices = (product?.tiers ?? [])
    .map((t) => t.price?.normalized_usd_month)
    .filter((x): x is number => typeof x === "number" && x > 0);
  return prices.length ? Math.min(...prices) : null;
}

/** 部分信息也给判断:price/score 缺谁就按可得的单维判,只有全缺才落 "—"。
 *  返回 missing 标记供表脚注汇总(避免逐格刷"信息不足/待确认"的破碎观感)。 */
function judgeValueThreat(
  price: number | null,
  score: number | null,
  targetPrice: number | null,
  targetScore: number | null
): { value: string; threat: string; missing: "price" | "score" | "both" | null } {
  const hasP = price != null && targetPrice != null;
  const hasS = score != null && targetScore != null;
  if (hasP && hasS) {
    if (price < targetPrice && score >= targetScore - 0.5)
      return { value: "性价比压力强", threat: "高", missing: null };
    if (score > targetScore) return { value: "能力领先", threat: "中高", missing: null };
    if (price < targetPrice) return { value: "价格防守强", threat: "中", missing: null };
    return { value: "差异化压力有限", threat: "低", missing: null };
  }
  if (hasS) {
    // 只有能力分 → 按能力单维判,价格列脚注说明
    if (score > targetScore + 0.4) return { value: "能力领先", threat: "中高", missing: "price" };
    if (score >= targetScore - 0.5) return { value: "能力接近", threat: "中", missing: "price" };
    return { value: "能力落后", threat: "低", missing: "price" };
  }
  if (hasP) {
    // 只有价格 → 按价格单维判
    if (price < targetPrice) return { value: "价格更低", threat: "待确认", missing: "score" };
    return { value: "价格更高", threat: "低", missing: "score" };
  }
  return { value: "—", threat: "—", missing: "both" };
}

function PriceAbilityMap({
  products,
  features,
  cols,
  target,
}: {
  products: PricingProduct[];
  features: Feature[];
  cols: string[];
  target: string;
}) {
  const avgs = averageScores(features, cols);
  const byName = Object.fromEntries(products.map((p) => [p.name, p]));
  const targetPrice = minMonthlyPrice(byName[target]);
  const targetScore = avgs[target] ?? null;
  const noPrice: string[] = [];
  const noScore: string[] = [];
  const rows = cols.map((name) => {
    const price = minMonthlyPrice(byName[name]);
    const score = avgs[name] ?? null;
    if (price == null) noPrice.push(name);
    if (score == null) noScore.push(name);
    if (name === target) return { name, price, score, value: "基准产品", threat: "—" };
    const j = judgeValueThreat(price, score, targetPrice, targetScore);
    return { name, price, score, value: j.value, threat: j.threat };
  });
  const notes: string[] = [];
  if (noScore.length) notes.push(`${noScore.join("、")} 缺少体验类证据,能力分未评出`);
  if (noPrice.length) notes.push(`${noPrice.join("、")} 未抓到付费档价格数值`);
  if (notes.length) notes.push("缺失维度的判断按可得信息单维给出,空格以 — 展示");
  return (
    <div className="overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-white/5">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">产品</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">入门付费价</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">能力均分</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">性价比判断</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">对 {target} 的威胁</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="border-t border-white/10">
              <td className="px-3 py-2 text-neutral-200">{r.name}</td>
              <td className={`px-3 py-2 font-mono ${r.price != null ? "text-sky-300" : "text-neutral-600"}`}>
                {r.price != null ? `$${r.price}/mo` : "—"}
              </td>
              <td className="px-3 py-2">
                <ScoreBar value={r.score} kind={r.score != null ? "real" : "none"} />
              </td>
              <td className={`px-3 py-2 ${r.value === "—" ? "text-neutral-600" : "text-neutral-400"}`}>
                {r.value}
              </td>
              <td className={`px-3 py-2 ${r.threat === "—" ? "text-neutral-600" : "text-neutral-400"}`}>
                {r.threat}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {notes.length > 0 && (
        <div className="border-t border-white/10 px-3 py-2 text-[11px] leading-relaxed text-neutral-500">
          {notes.join(";")}。
        </div>
      )}
    </div>
  );
}

function ConfBadge({ confidence }: { confidence?: string }) {
  const c = CONF_LABEL[confidence ?? "low"] ?? CONF_LABEL.low;
  return <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${c.tone}`}>{c.text}</span>;
}

// ── §0 决策摘要 ─────────────────────────────────────────────────────────────
function DecisionBriefCard({ report, cols, recs }: { report: Report; cols: string[]; recs: Recommendation[] }) {
  const productLines = productFitLines(report, cols);
  const recommendations = audienceRecommendations(report, cols, recs);
  const conf = confidenceSummary(report);
  const ds = report.schema_draft?.decision_summary;
  const extra = [
    ds?.what_to_learn?.answer,
    ds?.what_to_avoid?.answer,
  ].filter(Boolean).slice(0, 2) as string[];

  return (
    <section className="space-y-3">
      <SectionTitle id="decision">决策摘要 · 结论页</SectionTitle>
      <div className="rounded-xl border border-sky-500/20 bg-sky-500/[0.05] p-4">
        <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
          <div>
            <div className="text-xs font-medium text-sky-300">结论</div>
            <div className="mt-3 space-y-2.5">
              {productLines.map((line) => (
                <p key={line.product} className="text-sm leading-relaxed text-neutral-200">
                  {line.text}
                  <Chips ids={line.ids} />
                </p>
              ))}
              {extra.map((line, i) => (
                <p key={i} className="text-sm leading-relaxed text-neutral-400">{line}</p>
              ))}
            </div>
          </div>
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-sky-300">推荐</div>
              <div className="mt-2 space-y-1.5">
                {recommendations.map((item) => (
                  <div key={item.label} className="flex gap-2 text-sm">
                    <span className="w-28 shrink-0 text-neutral-500">{item.label}</span>
                    <span className="leading-relaxed text-neutral-200">{item.value}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-lg border border-white/10 bg-neutral-950/40 p-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-neutral-500">置信度</span>
                <ConfBadge confidence={conf.level} />
                <span className="text-sm text-neutral-200">{conf.label}</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-neutral-500">{conf.note}</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function ScoringRubric() {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs leading-relaxed text-neutral-500">
      <span className="font-medium text-neutral-300">评分口径：</span>
      0=未覆盖/无证据，1=基础覆盖，2=可用但限制明显，3=成熟可用，4=领先，5=明显领先。
      评分依据为官方文档、价格页、公开用户反馈和第三方材料；未找到证据时标记为 Unknown，不默认给分。
    </div>
  );
}

// ── §1 竞品格局 ─────────────────────────────────────────────────────────────
const RELATION_BLOCKS: { key: keyof CompetitorLandscape; label: string; tone: string }[] = [
  { key: "direct", label: "直接竞品", tone: "text-red-300 border-red-500/20" },
  { key: "indirect", label: "间接竞品", tone: "text-amber-300 border-amber-500/20" },
  { key: "alternative", label: "替代方案", tone: "text-sky-300 border-sky-500/20" },
];

function CompetitorLandscapeCard({ landscape }: { landscape: CompetitorLandscape }) {
  return (
    <div className="space-y-3">
      {RELATION_BLOCKS.map(({ key, label, tone }) => {
        const items = (landscape[key] ?? []) as LandscapeEntry[];
        if (!items.length) return null;
        return (
          <div key={key} className={`rounded-xl border bg-white/[0.02] p-4 ${tone.split(" ")[1]}`}>
            <div className={`mb-2 text-xs font-medium ${tone.split(" ")[0]}`}>{label} · {items.length}</div>
            <div className="space-y-2">
              {items.map((it, i) => (
                <div key={i} className="text-sm">
                  <span className="font-medium text-neutral-100">{it.name}</span>
                  <span className="text-neutral-400"> — {it.reason}</span>
                  <Chips ids={it.evidence_ids} />
                </div>
              ))}
            </div>
          </div>
        );
      })}
      {landscape.selection_rationale && (
        <p className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs leading-relaxed text-neutral-500">
          <span className="text-neutral-400">竞品筛选理由：</span>{landscape.selection_rationale}
        </p>
      )}
    </div>
  );
}

// ── §5 技术能力 ─────────────────────────────────────────────────────────────
function TechCapabilitySection({ report, cols }: { report: Report; cols: string[] }) {
  const tc = (report.schema_draft as { tech_capability?: { products?: Record<string, Record<string, unknown>> } } | undefined)?.tech_capability?.products;
  const hasData = tc && Object.values(tc).some((p) => p && Object.values(p).some((v) => v && v !== "unknown"));
  // 无任何技术指标证据时整段不渲染(避免 unknown 死壳);缺口已在 §13 不确定性里如实声明。
  if (!hasData) return null;
  const keys = [...new Set(Object.values(tc!).flatMap((p) => Object.keys(p)))];
  return (
    <section className="space-y-2">
      <SectionTitle id="tech">技术能力</SectionTitle>
      <div className="overflow-x-auto rounded-xl border border-white/10">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-white/5">
            <tr>
              <th className="px-3 py-2 text-left font-medium text-neutral-300">指标</th>
              {cols.map((c) => <th key={c} className="px-3 py-2 text-left font-medium text-neutral-300">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k} className="border-t border-white/10">
                <td className="px-3 py-2 text-neutral-300">{k}</td>
                {cols.map((c) => {
                  const v = tc![c]?.[k];
                  return <td key={c} className={`px-3 py-2 ${v && v !== "unknown" ? "text-neutral-200" : "text-neutral-600"}`}>{String(v ?? "unknown")}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ── §6 定位地图 ─────────────────────────────────────────────────────────────
function PositioningMapCards({ products }: { products: PositioningProduct[] }) {
  return (
    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
      {products.map((p) => (
        <div key={p.name} className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-neutral-100">{p.name}</span>
            {p.positioning_label && (
              <span className="shrink-0 rounded bg-sky-500/15 px-2 py-0.5 text-[10px] text-sky-300">{p.positioning_label}</span>
            )}
          </div>
          <dl className="mt-2 space-y-1.5 text-xs leading-relaxed">
            <Field k="目标用户" v={p.target_user} />
            <Field k="核心场景" v={p.core_scenario} />
            <Field k="价值主张" v={p.value_proposition} />
          </dl>
          <Chips ids={p.evidence_ids} />
        </div>
      ))}
    </div>
  );
}

function Field({ k, v }: { k: string; v?: string }) {
  if (!v) return null;
  return (
    <div>
      <dt className="text-neutral-600">{k}</dt>
      <dd className="text-neutral-300">{v}</dd>
    </div>
  );
}

// ── §7 功能覆盖与差距 ────────────────────────────────────────────────────────
function FeatureCoverageCard({ analysis, cols, target }: { analysis: FeatureAnalysis; cols: string[]; target: string }) {
  const coverage = analysis.coverage ?? {};
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-3">
        {cols.map((c) => {
          const cov = coverage[c];
          const known = cov?.coverage_known_only;
          const ev = cov?.evidence_coverage_rate;
          return (
            <div key={c} className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs">
              <div className="font-medium text-neutral-200">{c}</div>
              <div className="mt-1 text-neutral-400">功能覆盖率 <span className="font-mono text-neutral-200">{known != null ? `${Math.round(known * 100)}%` : "?"}</span></div>
              <div className="text-neutral-400">证据覆盖率 <span className="font-mono text-neutral-200">{ev != null ? `${Math.round(ev * 100)}%` : "?"}</span></div>
            </div>
          );
        })}
      </div>
      {(analysis.winners?.length ?? 0) > 0 && (
        <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
          <div className="mb-2 text-sm font-medium text-neutral-200">单项胜负（缺深度证据时如实判 tie / unclear）</div>
          <ul className="space-y-1.5 text-sm text-neutral-400">
            {analysis.winners!.map((w, i) => (
              <li key={w.feature_id ?? i} className="leading-relaxed">
                <span className="text-neutral-300">{w.name}：</span>
                <span className={w.winner === target ? "text-emerald-400" : "text-sky-300"}>{w.winner}</span>
                <ConfBadge confidence={w.confidence} />
                {w.reason && <span className="text-neutral-500"> — {w.reason}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(analysis.differentiation_matrix?.length ?? 0) > 0 && (
        <ul className="space-y-1 text-xs text-neutral-400">
          {analysis.differentiation_matrix!.map((d, i) => d.note && <li key={i}>• {d.note}</li>)}
        </ul>
      )}
    </div>
  );
}

// ── §8 定价同口径 insights ───────────────────────────────────────────────────
function PricingInsights({ model }: { model: PricingModel }) {
  const cmp = model.engine_comparison;
  const lines = [...(cmp?.insights ?? []), ...((cmp?.gaps ?? []).map((g) => g.note).filter(Boolean) as string[])];
  if (!lines.length) return null;
  return (
    <ul className="space-y-1 rounded-xl border border-white/10 bg-white/[0.02] p-4 text-xs leading-relaxed text-neutral-400">
      {lines.map((l, i) => <li key={i}>• {l}</li>)}
    </ul>
  );
}

// ── §12 优先级建议 · Learn / Avoid / Attack 分组 ─────────────────────────────
const ACTION_BLOCKS: { key: string; title: string; desc: string; tone: string }[] = [
  { key: "learn", title: "Learn · 学竞品已验证强项", desc: "把竞品已验证的强项学过来", tone: "border-emerald-500/20" },
  { key: "avoid", title: "Avoid · 避开竞品高权重领先区", desc: "竞品高权重领先、硬碰硬不划算的区域", tone: "border-amber-500/20" },
  { key: "attack", title: "Attack · 切入高价值空白区", desc: "高价值但竞品覆盖不足的空白区", tone: "border-sky-500/20" },
];

function GroupedRecommendations({ recs }: { recs: Recommendation[] }) {
  const groups: Record<string, Recommendation[]> = { learn: [], avoid: [], attack: [], other: [] };
  for (const r of recs) {
    const t = (r.action_type ?? "").toString().toLowerCase();
    (groups[t] ?? groups.other).push(r);
  }
  return (
    <div className="space-y-4">
      {ACTION_BLOCKS.map(({ key, title, desc, tone }) => (
        <div key={key} className={`rounded-xl border ${tone} bg-white/[0.02] p-4`}>
          <div className="text-sm font-medium text-neutral-200">{title}</div>
          <div className="mt-0.5 text-[11px] text-neutral-500">{desc}</div>
          <div className="mt-3 space-y-3">
            {groups[key].length ? groups[key].map((r) => <RecCard key={r.rec_id} rec={r} />)
              : <p className="text-xs text-neutral-600">暂无</p>}
          </div>
        </div>
      ))}
      {groups.other.length > 0 && (
        <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
          <div className="text-sm font-medium text-neutral-200">其它建议</div>
          <div className="mt-3 space-y-3">
            {groups.other.map((r) => <RecCard key={r.rec_id} rec={r} />)}
          </div>
        </div>
      )}
    </div>
  );
}

function SwotQuad({
  title,
  items,
  tone,
}: {
  title: string;
  items: SwotItem[];
  tone: "emerald" | "red" | "sky" | "amber";
}) {
  const border = {
    emerald: "border-emerald-500/20",
    red: "border-red-500/20",
    sky: "border-sky-500/20",
    amber: "border-amber-500/20",
  }[tone];
  return (
    <div className={`rounded-xl border ${border} bg-white/[0.02] p-4`}>
      <div className="mb-2 text-sm font-medium text-neutral-200">{title}</div>
      <ul className="space-y-1.5 text-sm text-neutral-400">
        {items.length === 0 && <li className="text-neutral-600">—</li>}
        {items.map((it, i) => (
          <li key={i} className="leading-relaxed">
            • {it.point}
            <Chips ids={it.evidence_ids} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function PainRow({ pain, syntheticIds }: { pain: PainPoint; syntheticIds?: Set<string> }) {
  const ids = pain.evidence_ids ?? pain.frequency?.evidence_ids ?? [];
  // 仅靠合成(模拟访谈)证据支撑 → 明确标注,避免把虚构的精确数字误读为真实发现
  const synthOnly = !!syntheticIds && ids.length > 0 && ids.every((i) => syntheticIds.has(i));
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-sm">
      <div className="flex flex-wrap items-start gap-2">
        {synthOnly && (
          <span
            className="mt-0.5 shrink-0 rounded bg-violet-500/15 px-2 py-0.5 text-xs text-violet-300"
            title="该痛点仅由模拟问卷/访谈(合成数据)支撑,非真实用户反馈,仅供参考"
          >
            模拟数据
          </span>
        )}
        {pain.frequency?.level && (
          <span className="mt-0.5 shrink-0 rounded bg-white/5 px-2 py-0.5 text-xs text-neutral-400">
            {pain.frequency.level}
          </span>
        )}
        {pain.frequency?.count && (
          <span className="mt-0.5 shrink-0 rounded bg-amber-500/10 px-2 py-0.5 text-xs text-amber-300">
            {pain.frequency.count}
          </span>
        )}
        {pain.affected_products && pain.affected_products.length > 0 && (
          <span className="mt-0.5 shrink-0 rounded bg-sky-500/10 px-2 py-0.5 text-xs text-sky-300">
            {pain.affected_products.join(" / ")}
          </span>
        )}
        <div className="text-neutral-300">
          {pain.description}
          <Chips ids={pain.evidence_ids ?? pain.frequency?.evidence_ids} />
        </div>
      </div>
      {pain.user_expectation && (
        <div className="mt-2 text-xs leading-relaxed text-neutral-500">
          产品机会：{pain.user_expectation}
        </div>
      )}
    </div>
  );
}

function RecCard({ rec }: { rec: Recommendation }) {
  const ps = rec.priority_score;
  const tone =
    ps?.priority === "P0"
      ? "bg-red-500/15 text-red-400"
      : ps?.priority === "P1"
        ? "bg-amber-500/15 text-amber-400"
        : "bg-sky-500/15 text-sky-400";
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="font-medium text-neutral-100">{rec.action}</div>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${tone}`}>
          {ps?.priority ?? "-"} · {ps?.final_score?.toFixed(2) ?? "?"}
        </span>
      </div>
      {rec.target_competitor && (
        <div className="mt-1.5 text-[11px] text-neutral-500">
          对标 <span className="text-neutral-300">{Array.isArray(rec.target_competitor) ? rec.target_competitor.join("、") : rec.target_competitor}</span>
        </div>
      )}
      {rec.rationale && (
        <p className="mt-2 text-sm leading-relaxed text-neutral-400">
          {rec.rationale}
          <Chips ids={rec.evidence_ids ?? rec.evidence_refs} />
        </p>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <RecField label="目标收益" value={rec.expected_impact} />
        <RecField label="验收指标" value={rec.success_metric} />
        <RecField label="风险" value={rec.risk} />
        <RecField label="验证方式" value={rec.validation_method ?? rec.time_horizon} />
      </div>
    </div>
  );
}

function RecField({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded-lg bg-white/[0.03] px-3 py-2 text-xs">
      <div className="text-neutral-600">{label}</div>
      <div className="mt-1 leading-relaxed text-neutral-300">{value || "待补充"}</div>
    </div>
  );
}
