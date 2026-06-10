"use client";

import { useEffect, useState } from "react";
import { fetchStageQuality, type StageStat } from "@/lib/api";

const STAGE_LABEL: Record<string, string> = {
  collector: "采集 Collector",
  analyzer: "分析 Analyzer",
  writer: "渲染 Writer",
  degraded_writer: "降级 Writer",
  reviewer: "质检 Reviewer",
};

const VERDICT_COLOR: Record<string, string> = {
  pass: "text-emerald-300 bg-emerald-500/10",
  warn: "text-amber-300 bg-amber-500/10",
  fail: "text-rose-300 bg-rose-500/10",
};

export default function StageQuality() {
  const [stages, setStages] = useState<StageStat[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchStageQuality()
      .then((d) => setStages(d.stages))
      .catch((e) => setErr(String(e)))
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded) return <div className="text-sm text-neutral-500">加载中…</div>;
  if (err) return <div className="text-sm text-rose-400">加载失败：{err}</div>;
  if (stages.length === 0)
    return (
      <div className="text-sm text-neutral-500">
        暂无埋点数据 — 先跑一次分析，`logs/stage_quality.jsonl` 生成后这里显示各环节瓶颈。
      </div>
    );

  const maxElapsed = Math.max(1, ...stages.map((s) => s.avg_elapsed ?? 0));

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium text-neutral-400">各环节质量与瓶颈（平均耗时）</h2>
      <div className="space-y-2.5">
        {stages.map((s) => {
          const pct = Math.round(((s.avg_elapsed ?? 0) / maxElapsed) * 100);
          const slow = (s.avg_elapsed ?? 0) >= maxElapsed * 0.8;
          return (
            <div
              key={s.stage}
              className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-neutral-200">
                  {STAGE_LABEL[s.stage] ?? s.stage}
                  {slow && (
                    <span className="ml-2 rounded bg-rose-500/15 px-1.5 py-0.5 text-[11px] text-rose-300">
                      瓶颈
                    </span>
                  )}
                </span>
                <span className="text-xs text-neutral-500">
                  {s.avg_elapsed != null ? `${s.avg_elapsed}s` : "—"} · {s.runs} 次
                </span>
              </div>
              <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-white/5">
                <div
                  className={`h-full rounded-full ${slow ? "bg-rose-400/70" : "bg-sky-400/70"}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                {Object.entries(s.verdicts).map(([v, n]) => (
                  <span
                    key={v}
                    className={`rounded px-2 py-0.5 ${VERDICT_COLOR[v] ?? "text-neutral-400 bg-white/5"}`}
                  >
                    {v} {n}
                  </span>
                ))}
                {s.avg_tokens != null && s.avg_tokens > 0 && (
                  <span className="rounded bg-sky-500/10 px-2 py-0.5 text-sky-300">
                    🪙 ~{s.avg_tokens >= 1000 ? `${(s.avg_tokens / 1000).toFixed(1)}k` : s.avg_tokens} tokens/次
                  </span>
                )}
                {s.total_llm_calls != null && s.total_llm_calls > 0 && (
                  <span className="rounded bg-white/5 px-2 py-0.5 text-neutral-400">
                    {s.total_llm_calls} 次调用
                  </span>
                )}
                {s.last_metrics &&
                  Object.entries(s.last_metrics)
                    .slice(0, 5)
                    .map(([k, val]) => (
                      <span key={k} className="rounded bg-white/5 px-2 py-0.5 text-neutral-400">
                        {k}: {String(val)}
                      </span>
                    ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
