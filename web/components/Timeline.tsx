"use client";

import { useEffect, useState } from "react";
import { fetchTimeline, type Timeline as TimelineData, type TimelineEntry } from "@/lib/api";

const STAGE_LABEL: Record<string, string> = {
  collector: "采集 Collector",
  analyzer: "分析 Analyzer",
  writer: "渲染 Writer",
  degraded_writer: "降级 Writer",
  reviewer: "质检 Reviewer",
};

const STATUS_STYLE: Record<string, { dot: string; chip: string; label: string }> = {
  ok: { dot: "bg-emerald-400", chip: "text-emerald-300 bg-emerald-500/10", label: "通过" },
  degraded: { dot: "bg-amber-400", chip: "text-amber-300 bg-amber-500/10", label: "待修复" },
  failed: { dot: "bg-rose-400", chip: "text-rose-300 bg-rose-500/10", label: "失败" },
};

function ProducedChips({ produced }: { produced: TimelineEntry["produced"] }) {
  const items = Object.entries(produced ?? {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .slice(0, 4);
  if (items.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
      {items.map(([k, v]) => (
        <span key={k} className="rounded bg-white/5 px-2 py-0.5 text-neutral-400">
          {k}: {String(v)}
        </span>
      ))}
    </div>
  );
}

function GapRow({ gap }: { gap: TimelineEntry["gaps"][number] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
      <span className="rounded bg-rose-500/10 px-2 py-0.5 text-rose-300">{gap.gap_type ?? "gap"}</span>
      <span className="text-neutral-500">→ 打回</span>
      <span className="rounded bg-sky-500/10 px-2 py-0.5 font-mono text-sky-300">{gap.owner_node ?? "?"}</span>
      {gap.task_key && <span className="font-mono text-neutral-500">{gap.task_key}</span>}
      {gap.fixable === false && (
        <span className="rounded bg-white/5 px-1.5 py-0.5 text-neutral-400">不可补·诚实标注</span>
      )}
    </div>
  );
}

export default function Timeline() {
  const [data, setData] = useState<TimelineData | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchTimeline()
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded) return <div className="text-sm text-neutral-500">加载中…</div>;
  if (err) return <div className="text-sm text-rose-400">加载失败：{err}</div>;
  if (!data || data.entries.length === 0)
    return (
      <div className="text-sm text-neutral-500">
        暂无时间线数据 — 先跑一次分析，`logs/stage_quality.jsonl` 生成后这里按节点逐段展示。
      </div>
    );

  const { entries, summary, run_id } = data;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-neutral-300">运行时间线（逐节点 · 重试单列）</h2>
          <p className="mt-0.5 text-xs text-neutral-500">run: {run_id ?? "—"}</p>
        </div>
        <div className="flex flex-wrap gap-1.5 text-[11px]">
          <span className="rounded bg-white/5 px-2 py-0.5 text-neutral-400">{summary.nodes} 段</span>
          <span className="rounded bg-white/5 px-2 py-0.5 text-neutral-400">{summary.total_elapsed_sec}s</span>
          {summary.open_gaps > 0 && (
            <span className="rounded bg-rose-500/10 px-2 py-0.5 text-rose-300">{summary.open_gaps} 个缺口</span>
          )}
          {summary.worst_status && (
            <span className={`rounded px-2 py-0.5 ${STATUS_STYLE[summary.worst_status]?.chip ?? "text-neutral-400 bg-white/5"}`}>
              {STATUS_STYLE[summary.worst_status]?.label ?? summary.worst_status}
            </span>
          )}
        </div>
      </div>

      <ol className="relative space-y-2.5 border-l border-white/10 pl-5">
        {entries.map((e) => {
          const st = STATUS_STYLE[e.status] ?? STATUS_STYLE.ok;
          const cs = e.checks_summary;
          return (
            <li key={e.seq} className="relative rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3">
              <span className={`absolute -left-[27px] top-4 h-3 w-3 rounded-full ring-2 ring-neutral-950 ${st.dot}`} />
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-neutral-200">
                  <span className="mr-2 text-neutral-500">{e.seq}.</span>
                  {STAGE_LABEL[e.stage] ?? e.stage}
                  {typeof e.attempt === "number" && e.attempt > 1 && (
                    <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] text-amber-300">
                      第 {e.attempt} 次
                    </span>
                  )}
                </span>
                <span className="flex items-center gap-2">
                  <span className={`rounded px-2 py-0.5 text-[11px] ${st.chip}`}>{st.label}</span>
                  <span className="text-xs text-neutral-500">{e.elapsed_sec != null ? `${e.elapsed_sec}s` : "—"}</span>
                </span>
              </div>

              <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                {cs.pass > 0 && <span className="rounded bg-emerald-500/10 px-2 py-0.5 text-emerald-300">门 {cs.pass} 过</span>}
                {cs.warn > 0 && <span className="rounded bg-amber-500/10 px-2 py-0.5 text-amber-300">{cs.warn} 警告</span>}
                {cs.fail > 0 && <span className="rounded bg-rose-500/10 px-2 py-0.5 text-rose-300">{cs.fail} 失败</span>}
              </div>

              <ProducedChips produced={e.produced} />

              {e.gaps.length > 0 && (
                <div className="mt-2 space-y-1.5 border-t border-white/5 pt-2">
                  {e.gaps.map((g, i) => (
                    <GapRow key={g.task_key ?? i} gap={g} />
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
