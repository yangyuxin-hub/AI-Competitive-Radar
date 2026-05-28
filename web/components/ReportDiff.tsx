"use client";

import type { ReportDiff } from "@/lib/diff";
import { SUPPORT_META } from "@/lib/schema";
import type { SupportStatus } from "@/lib/schema";

function Delta({ v, suffix = "" }: { v: number | null; suffix?: string }) {
  if (v == null) return <span className="text-neutral-500">—</span>;
  const tone = v > 0 ? "text-emerald-400" : v < 0 ? "text-red-400" : "text-neutral-400";
  const sign = v > 0 ? "+" : "";
  return (
    <span className={`font-mono ${tone}`}>
      {sign}
      {v}
      {suffix}
    </span>
  );
}

function StatusBit({ s }: { s?: SupportStatus }) {
  if (!s) return <span className="text-neutral-600">—</span>;
  const m = SUPPORT_META[s];
  return <span className={m.tone}>{m.icon}</span>;
}

export default function ReportDiffView({ diff }: { diff: ReportDiff }) {
  const noChange =
    diff.cellChanges.length === 0 &&
    diff.features.added.length === 0 &&
    diff.features.removed.length === 0 &&
    diff.recommendations.added.length === 0 &&
    diff.recommendations.removed.length === 0 &&
    diff.competitors.added.length === 0 &&
    diff.competitors.removed.length === 0;

  return (
    <div className="space-y-5 rounded-2xl border border-sky-500/20 bg-sky-500/[0.03] p-5">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="text-neutral-400">
          对比上一份 ·{" "}
          {new Date(diff.prevDate).toLocaleString("zh-CN")} →{" "}
          {new Date(diff.currDate).toLocaleString("zh-CN")}
        </span>
        <span className="text-neutral-400">
          质检 <Delta v={diff.qualityDelta} />
        </span>
        <span className="text-neutral-400">
          证据 <Delta v={diff.evidenceDelta} suffix=" 条" />
        </span>
      </div>

      {noChange && (
        <p className="text-sm text-neutral-500">两份报告在功能、建议、竞品上没有结构性变化。</p>
      )}

      {diff.cellChanges.length > 0 && (
        <div>
          <div className="mb-2 text-sm font-medium text-neutral-200">功能矩阵变化</div>
          <div className="space-y-1">
            {diff.cellChanges.map((c, i) => (
              <div
                key={i}
                className="flex flex-wrap items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-1.5 text-sm"
              >
                <span className="text-neutral-300">{c.feature}</span>
                <span className="text-neutral-500">· {c.product}:</span>
                <span className="flex items-center gap-1">
                  <StatusBit s={c.fromStatus} />
                  {c.fromScore != null && (
                    <span className="text-xs text-neutral-500">{c.fromScore}/5</span>
                  )}
                </span>
                <span className="text-neutral-600">→</span>
                <span className="flex items-center gap-1">
                  <StatusBit s={c.toStatus} />
                  {c.toScore != null && (
                    <span className="text-xs text-neutral-300">{c.toScore}/5</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <ChangeList title="竞品" change={diff.competitors} />
      <ChangeList title="功能维度" change={diff.features} />
      <ChangeList title="改进建议" change={diff.recommendations} />
    </div>
  );
}

function ChangeList({
  title,
  change,
}: {
  title: string;
  change: { added: string[]; removed: string[] };
}) {
  if (change.added.length === 0 && change.removed.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-sm font-medium text-neutral-200">{title}变化</div>
      <div className="space-y-1 text-sm">
        {change.added.map((x, i) => (
          <div key={`a-${i}`} className="text-emerald-400">
            + {x}
          </div>
        ))}
        {change.removed.map((x, i) => (
          <div key={`r-${i}`} className="text-red-400/80">
            − {x}
          </div>
        ))}
      </div>
    </div>
  );
}
