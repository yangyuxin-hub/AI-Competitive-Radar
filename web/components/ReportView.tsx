"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Report } from "@/lib/types";
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
  { id: "matrix", label: "功能矩阵" },
  { id: "pricing", label: "定价" },
  { id: "swot", label: "SWOT" },
  { id: "pains", label: "用户痛点" },
  { id: "recs", label: "改进建议" },
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

        {/* Overview */}
        <section className="space-y-3">
          <SectionTitle id="overview">概览</SectionTitle>
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
          </tr>
        </thead>
        <tbody>
          {features.map((f) => (
            <tr key={f.feature_id} className="border-t border-white/10">
              <td className="px-3 py-3 align-top">
                <div className="font-medium text-neutral-100">{f.name}</div>
                {f.gap?.reason && (
                  <div className="mt-1 text-xs text-neutral-500">
                    {f.gap.winner && (
                      <span className="text-emerald-400/80">胜出 {f.gap.winner} · </span>
                    )}
                    {f.gap.reason}
                    <Chips ids={f.gap.evidence_ids} />
                  </div>
                )}
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
      <div className="flex items-start gap-2">
        {pain.frequency?.level && (
          <span className="mt-0.5 shrink-0 rounded bg-white/5 px-2 py-0.5 text-xs text-neutral-400">
            {pain.frequency.level}
          </span>
        )}
        <div className="text-neutral-300">
          {pain.description}
          <Chips ids={pain.evidence_ids ?? pain.frequency?.evidence_ids} />
        </div>
      </div>
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
    </div>
  );
}
