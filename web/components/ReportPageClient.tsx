"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchReport, fetchReports } from "@/lib/api";
import type { Report } from "@/lib/types";
import { findPrevious, computeDiff, type ReportDiff } from "@/lib/diff";
import ReportView from "./ReportView";
import ReportDiffView from "./ReportDiff";

export default function ReportPageClient({ id }: { id: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [diff, setDiff] = useState<ReportDiff | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [prevDate, setPrevDate] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchReport(id).then(setReport).catch((e) => setError(String(e)));
  }, [id]);

  // 找同 target 的上一份，准备 diff
  useEffect(() => {
    if (!report) return;
    fetchReports()
      .then(async (index) => {
        const prev = findPrevious(index, id);
        if (!prev) return;
        setPrevDate(prev.created_at);
        const prevReport = await fetchReport(prev.report_id);
        setDiff(computeDiff(prevReport, report));
      })
      .catch(() => {});
  }, [report, id]);

  return (
    <div className="min-h-screen bg-neutral-950 font-sans text-neutral-100">
      <main className="mx-auto max-w-3xl px-6 py-10">
        <div className="mb-6 flex items-center justify-between">
          <Link href="/" className="text-sm text-neutral-500 transition hover:text-sky-300">
            ← 返回工作台
          </Link>
          {diff && (
            <button
              onClick={() => setShowDiff((v) => !v)}
              className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-1.5 text-xs text-sky-300 transition hover:bg-sky-500/20"
            >
              {showDiff ? "隐藏对比" : `对比上一份 (${prevDate ? new Date(prevDate).toLocaleDateString("zh-CN") : ""})`}
            </button>
          )}
        </div>

        {error && <p className="text-red-400">{error}</p>}
        {!report && !error && <p className="text-neutral-500">加载中…</p>}

        {showDiff && diff && (
          <div className="mb-8">
            <ReportDiffView diff={diff} />
          </div>
        )}

        {report && <ReportView report={report} />}
      </main>
    </div>
  );
}
