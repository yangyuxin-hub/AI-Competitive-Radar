"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchReports } from "@/lib/api";
import type { ReportIndexItem } from "@/lib/types";

export default function RecentReports() {
  const [items, setItems] = useState<ReportIndexItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetchReports()
      .then(setItems)
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded || items.length === 0) return null;

  return (
    <section>
      <h2 className="mb-3 text-sm font-medium text-neutral-400">情报档案</h2>
      <div className="space-y-2">
        {items.map((r) => (
          <Link
            key={r.report_id}
            href={`/report/${r.report_id}`}
            className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-sm transition hover:border-sky-500/40 hover:bg-white/[0.04]"
          >
            <div>
              <div className="text-neutral-200">
                {r.target_product}{" "}
                <span className="text-neutral-500">
                  vs {r.competitors.join(", ")}
                </span>
              </div>
              <div className="mt-0.5 text-xs text-neutral-600">
                {r.analysis_focus.join(" / ")} ·{" "}
                {new Date(r.created_at).toLocaleString("zh-CN")}
              </div>
            </div>
            <span className="rounded-full bg-white/5 px-2.5 py-1 text-xs text-neutral-400">
              {r.quality_score ?? "?"}/100
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}
