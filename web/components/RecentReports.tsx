"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchReports } from "@/lib/api";
import type { ReportIndexItem } from "@/lib/types";

export default function RecentReports() {
  const [items, setItems] = useState<ReportIndexItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    fetchReports()
      .then(setItems)
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded || items.length === 0) return null;

  const visibleItems = expanded ? items : items.slice(0, 5);
  const hiddenCount = Math.max(0, items.length - visibleItems.length);

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-neutral-400">情报档案</h2>
        {items.length > 5 && (
          <span className="text-xs text-neutral-600">
            {expanded ? `共 ${items.length} 条` : `显示 5 / ${items.length} 条`}
          </span>
        )}
      </div>
      <div className="space-y-2">
        {visibleItems.map((r) => (
          <Link
            key={r.report_id}
            href={`/report/${r.report_id}`}
            className="block rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-sm transition hover:border-sky-500/40 hover:bg-white/[0.04]"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-neutral-200">
                  {r.target_product}{" "}
                  <span className="text-neutral-500">
                    vs {r.competitors.join(", ")}
                  </span>
                </div>
                <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-neutral-400">
                  {r.summary || "暂无结论摘要"}
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-neutral-500">
                  <span className="rounded bg-white/5 px-2 py-0.5">
                    {r.analysis_focus.join(" / ")}
                  </span>
                  {r.runtime_profile && (
                    <span className="rounded bg-sky-500/10 px-2 py-0.5 text-sky-300">
                      {r.runtime_profile}
                    </span>
                  )}
                  {r.evidence_count != null && (
                    <span className="rounded bg-emerald-500/10 px-2 py-0.5 text-emerald-300">
                      {r.evidence_count} 条证据
                    </span>
                  )}
                  <span>{new Date(r.created_at).toLocaleString("zh-CN")}</span>
                </div>
              </div>
              <span className="shrink-0 rounded-full bg-white/5 px-2.5 py-1 text-xs text-neutral-400">
                {r.quality_score ?? "?"}/100
              </span>
            </div>
          </Link>
        ))}
      </div>
      {items.length > 5 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-3 w-full rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 text-sm text-neutral-400 transition hover:border-sky-500/40 hover:text-sky-300"
        >
          {expanded ? "收起到 5 条" : `展开其余 ${hiddenCount} 条`}
        </button>
      )}
    </section>
  );
}
