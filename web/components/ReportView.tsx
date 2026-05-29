"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Report, Completeness, BusinessValue, ResearchMethod } from "@/lib/types";
import type {
  Feature,
  Recommendation,
  SwotItem,
  PricingProduct,
  PainPoint,
} from "@/lib/schema";
import { SUPPORT_META } from "@/lib/schema";
import { EvidenceProvider, Chips } from "./Evidence";

const SECTIONS = [
  { id: "overview", label: "概览" },
  { id: "evidence", label: "证据覆盖" },
  { id: "matrix", label: "功能矩阵" },
  { id: "pricing", label: "定价" },
  { id: "pains", label: "用户痛点" },
  { id: "recs", label: "改进建议" },
  { id: "swot", label: "SWOT" },
];

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

function averageScores(features: Feature[], cols: string[]) {
  return Object.fromEntries(
    cols
      .map((c) => {
        const scores = features
          .map((f) => f.products[c]?.quality_score?.score)
          .filter((x): x is number => typeof x === "number");
        return [c, scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null];
      })
      .filter(([, v]) => v != null)
  ) as Record<string, number>;
}

function buildDecisionSummary(report: Report, cols: string[], recs: Recommendation[]) {
  const features = report.schema_draft?.feature_tree?.features ?? [];
  const avgs = averageScores(features, cols);
  const leader = Object.entries(avgs).sort((a, b) => b[1] - a[1])[0];
  const target = report.meta.target_product;
  const pressure = [...features]
    .filter((f) => f.gap?.winner && f.gap.winner !== target)
    .sort((a, b) => (b.gap?.confidence ?? 0) - (a.gap?.confidence ?? 0))[0];
  const strength = [...features]
    .filter((f) => f.gap?.winner === target)
    .sort((a, b) => (b.gap?.confidence ?? 0) - (a.gap?.confidence ?? 0))[0];

  return {
    headline:
      report.summary ||
      (leader
        ? `结论：${leader[0]} 当前综合评分最高，${target} 需要围绕关键短板形成更清晰的防守策略。`
        : "结论：当前证据不足以形成稳定综合判断。"),
    leader: leader ? `${leader[0]} ${leader[1].toFixed(1)}/5` : "待确认",
    strength: strength?.name ?? "待确认",
    risk: pressure?.name ?? "待确认",
    action: recs[0]?.action ?? "补齐可执行建议与验证指标",
  };
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

export default function ReportView({ report }: { report: Report }) {
  const [showRaw, setShowRaw] = useState(false);
  const s = report.schema_draft;

  if (!s) {
    return <p className="text-neutral-500">无结构化数据</p>;
  }

  const cols = s.feature_tree
    ? Object.keys(s.feature_tree.features[0]?.products ?? {})
    : [report.meta.target_product, ...report.meta.competitors];
  const recs = [...(s.recommendations ?? [])].sort(
    (a, b) => (b.priority_score?.final_score ?? 0) - (a.priority_score?.final_score ?? 0)
  );
  const decision = buildDecisionSummary(report, cols, recs);
  const stats = evidenceStats(report);
  const dimensions = fallbackQualityDimensions(report);
  const uncertainties = uncertaintyNotes(report);

  return (
    <EvidenceProvider evidence={report.raw_evidence}>
      <div className="space-y-8 ca-stagger">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-white">
              {report.meta.target_product}{" "}
              <span className="text-neutral-500">vs {report.meta.competitors.join(" / ")}</span>
            </h1>
            <p className="mt-1 text-sm text-neutral-500">
              焦点 {report.meta.analysis_focus.join(" / ")} · 数据截止 {report.meta.data_cutoff} ·{" "}
              {report.raw_evidence.length} 条证据
            </p>
          </div>
          <QualityBadge score={report.quality_report?.quality_score} status={report.status} />
        </div>

        {/* Sub-nav */}
        <nav className="sticky top-0 z-10 -mx-2 flex flex-wrap gap-1 bg-neutral-950/80 px-2 py-2 backdrop-blur">
          {SECTIONS.map((sec) => (
            <a
              key={sec.id}
              href={`#${sec.id}`}
              className="rounded-lg px-3 py-1 text-xs text-neutral-400 transition hover:bg-white/5 hover:text-sky-300"
            >
              {sec.label}
            </a>
          ))}
        </nav>

        {/* 综合评级 — 一眼看懂这份报告几分 */}
        <OverallGrade report={report} />

        {/* 业务价值 — vs 人工竞品分析 */}
        {report.business_value && <BusinessValueCard data={report.business_value} />}

        {/* Overview */}
        <section className="space-y-3">
          <SectionTitle id="overview">概览</SectionTitle>
          <div className="rounded-xl border border-sky-500/20 bg-sky-500/[0.06] p-4">
            <div className="text-xs font-medium text-sky-300">Executive Summary</div>
            <p className="mt-2 text-base leading-relaxed text-neutral-100">{decision.headline}</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-4">
              <DecisionPill label="综合领先" value={decision.leader} />
              <DecisionPill label="目标优势" value={decision.strength} />
              <DecisionPill label="核心风险" value={decision.risk} />
              <DecisionPill label="优先行动" value={decision.action} />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="对比竞品" value={`${cols.length} 家`} />
            <Stat label="功能维度" value={`${s.feature_tree?.features.length ?? 0} 项`} />
            <Stat
              label="改进建议"
              value={`${recs.length} 条 · 最高 ${recs[0]?.priority_score?.priority ?? "-"}`}
            />
          </div>
          {s.pricing_model?.pricing_gap?.summary && (
            <p className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-sm leading-relaxed text-neutral-300">
              <span className="text-neutral-500">定价定位：</span>
              {s.pricing_model.pricing_gap.summary}
              <Chips ids={s.pricing_model.pricing_gap.evidence_ids} />
            </p>
          )}
          {report.stage_timings && report.stage_timings.length > 0 && (
            <details className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-sm">
              <summary className="cursor-pointer text-neutral-400">
                生成过程 · 共 {report.stage_timings.reduce((a, t) => a + (t.duration_sec || 0), 0)}s
              </summary>
              <div className="mt-2 space-y-1.5">
                {report.stage_timings.map((t, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className="shrink-0">{t.icon ?? "•"}</span>
                    <span className="shrink-0 text-neutral-300">{t.label}</span>
                    <span className="shrink-0 font-mono text-emerald-400">{t.duration_sec}s</span>
                    {t.result && <span className="text-neutral-500">· {t.result}</span>}
                  </div>
                ))}
              </div>
            </details>
          )}
        </section>

        <section className="space-y-3">
          <SectionTitle id="evidence">证据覆盖与质量</SectionTitle>
          {report.completeness && <CompletenessCard data={report.completeness} />}
          <div className="grid gap-3 lg:grid-cols-[1fr_1fr]">
            <QualityBreakdown dimensions={dimensions} />
            <EvidenceCoverage byClaim={stats.byClaim} byBias={stats.byBias} byType={stats.byType} />
          </div>
          <UncertaintyBox notes={uncertainties} />
        </section>

        {/* Feature matrix — hero */}
        {s.feature_tree && (
          <section className="space-y-3">
            <SectionTitle id="matrix">功能对比矩阵 · {s.feature_tree.category}</SectionTitle>
            <FeatureMatrix features={s.feature_tree.features} cols={cols} />
          </section>
        )}

        {/* Pricing */}
        {s.pricing_model && (
          <section className="space-y-3">
            <SectionTitle id="pricing">定价对比</SectionTitle>
            <div className="grid gap-3 md:grid-cols-2">
              {s.pricing_model.products.map((p) => (
                <PricingCard key={p.name} product={p} />
              ))}
            </div>
            {s.feature_tree && <PriceAbilityMap products={s.pricing_model.products} features={s.feature_tree.features} cols={cols} target={report.meta.target_product} />}
          </section>
        )}

        {/* SWOT */}
        {s.swot && (
          <section className="space-y-3">
            <SectionTitle id="swot">SWOT</SectionTitle>
            <div className="grid gap-3 sm:grid-cols-2">
              <SwotQuad title="优势 S" items={s.swot.strengths} tone="emerald" />
              <SwotQuad title="劣势 W" items={s.swot.weaknesses} tone="red" />
              <SwotQuad title="机会 O" items={s.swot.opportunities} tone="sky" />
              <SwotQuad title="威胁 T" items={s.swot.threats} tone="amber" />
            </div>
          </section>
        )}

        {/* Pains */}
        {s.user_persona && (
          <section className="space-y-3">
            <SectionTitle id="pains">用户痛点</SectionTitle>
            {report.research_method && <ResearchMethodCard data={report.research_method} />}
            <div className="space-y-2">
              {s.user_persona.pain_points.map((p) => (
                <PainRow key={p.pain_id} pain={p} />
              ))}
            </div>
          </section>
        )}

        {/* Recommendations */}
        <section className="space-y-3">
          <SectionTitle id="recs">改进建议</SectionTitle>
          <div className="space-y-3">
            {recs.map((r) => (
              <RecCard key={r.rec_id} rec={r} />
            ))}
          </div>
        </section>

        {/* Raw markdown fallback */}
        <details
          open={showRaw}
          onToggle={(e) => setShowRaw((e.target as HTMLDetailsElement).open)}
          className="rounded-xl border border-white/10 bg-white/[0.02]"
        >
          <summary className="cursor-pointer px-4 py-3 text-sm text-neutral-400">
            查看原始 Markdown 报告
          </summary>
          <div className="prose-report border-t border-white/10 px-4 py-3 text-sm text-neutral-300">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {report.report_draft ?? ""}
            </ReactMarkdown>
          </div>
        </details>
      </div>
    </EvidenceProvider>
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

function DecisionPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-white/10 bg-neutral-950/40 p-3">
      <div className="text-[11px] text-neutral-500">{label}</div>
      <div className="mt-1 line-clamp-2 text-xs font-medium leading-relaxed text-neutral-200">{value}</div>
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
    <div className="flex items-center gap-5 rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.06] to-white/[0.02] p-5">
      <div className="flex flex-col items-center">
        <div className={`font-mono text-5xl font-bold leading-none ${g.tone}`}>{g.letter}</div>
        <div className="mt-1 text-xs text-neutral-400">{g.label}</div>
      </div>
      <div className="h-12 w-px bg-white/10" />
      <div className="flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm text-neutral-400">综合评级</span>
          <span className={`font-mono text-2xl font-semibold ${g.tone}`}>{overall}</span>
          <span className="text-xs text-neutral-600">/100 · {statusLabel}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
          {parts.map((p) => (
            <div key={p.label} className="flex items-center gap-2 text-xs">
              <span className="text-neutral-500">{p.label}</span>
              <span className="font-mono text-neutral-300">{Math.round(p.value)}</span>
            </div>
          ))}
        </div>
      </div>
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
    </div>
  );
}

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

function FeatureMatrix({ features, cols }: { features: Feature[]; cols: string[] }) {
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
                const meta = SUPPORT_META[cell.support_status];
                return (
                  <td key={c} className="px-3 py-3 align-top">
                    <div className={`flex items-center gap-1 ${meta.tone}`}>
                      <span>{meta.icon}</span>
                      {cell.quality_score && (
                        <span className="text-xs">
                          {cell.quality_score.score}/{cell.quality_score.scale}
                        </span>
                      )}
                    </div>
                    <Chips
                      ids={cell.quality_score?.evidence_ids ?? cell.support_evidence_ids}
                    />
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
              <span className="text-neutral-300">{t.tier_name}</span>
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

function minMonthlyPrice(product?: PricingProduct) {
  const prices = (product?.tiers ?? [])
    .map((t) => t.price?.normalized_usd_month)
    .filter((x): x is number => typeof x === "number");
  return prices.length ? Math.min(...prices) : null;
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
  const targetScore = avgs[target];
  return (
    <div className="overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-white/5">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">产品</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">入门月价</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">能力均分</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">性价比判断</th>
            <th className="px-3 py-2 text-left font-medium text-neutral-300">对 {target} 的威胁</th>
          </tr>
        </thead>
        <tbody>
          {cols.map((name) => {
            const price = minMonthlyPrice(byName[name]);
            const score = avgs[name];
            let value = "信息不足";
            let threat = "待确认";
            if (name === target) {
              value = "基准产品";
              threat = "—";
            } else if (price != null && score != null && targetPrice != null && targetScore != null) {
              if (price < targetPrice && score >= targetScore - 0.5) {
                value = "性价比压力强";
                threat = "高";
              } else if (score > targetScore) {
                value = "能力领先";
                threat = "中高";
              } else if (price < targetPrice) {
                value = "价格防守强";
                threat = "中";
              } else {
                value = "差异化压力有限";
                threat = "低";
              }
            }
            return (
              <tr key={name} className="border-t border-white/10">
                <td className="px-3 py-2 text-neutral-200">{name}</td>
                <td className="px-3 py-2 font-mono text-sky-300">{price != null ? `$${price}/mo` : "待确认"}</td>
                <td className="px-3 py-2 text-neutral-300">{score != null ? `${score.toFixed(1)}/5` : "—"}</td>
                <td className="px-3 py-2 text-neutral-400">{value}</td>
                <td className="px-3 py-2 text-neutral-400">{threat}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
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

function PainRow({ pain }: { pain: PainPoint }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 text-sm">
      <div className="flex flex-wrap items-start gap-2">
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
      {rec.rationale && (
        <p className="mt-2 text-sm leading-relaxed text-neutral-400">
          {rec.rationale}
          <Chips ids={rec.evidence_ids} />
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
