"use client";

import { useState } from "react";
import AnalyzeFlow from "@/components/AnalyzeFlow";
import Checklist from "@/components/Checklist";
import RecentReports from "@/components/RecentReports";
import type { RunArgs } from "@/lib/api";
import type { StageReport } from "@/lib/types";

type ChecklistMode = "idle" | "running" | "done" | "error" | "cancelled";

interface CurrentRun {
  clientRunId: string | null;
  mode: ChecklistMode;
  reportId: string | null;
  runArgs: RunArgs | null;
  stageReports: StageReport[];
  message: string | null;
}

const EMPTY_RUN: CurrentRun = {
  clientRunId: null,
  mode: "idle",
  reportId: null,
  runArgs: null,
  stageReports: [],
  message: null,
};

export default function Workbench() {
  const [currentRun, setCurrentRun] = useState<CurrentRun>(EMPTY_RUN);

  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0 space-y-12">
        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <AnalyzeFlow
            onRunStart={({ clientRunId, args }) => {
              setCurrentRun({
                clientRunId,
                mode: "running",
                reportId: null,
                runArgs: args,
                stageReports: [],
                message: null,
              });
            }}
            onStageReport={({ clientRunId, report }) => {
              setCurrentRun((prev) => {
                if (prev.clientRunId !== clientRunId) return prev;
                return { ...prev, stageReports: [...prev.stageReports, report] };
              });
            }}
            onRunDone={({ clientRunId, reportId }) => {
              setCurrentRun((prev) => {
                if (prev.clientRunId !== clientRunId) return prev;
                return { ...prev, mode: "done", reportId, message: null };
              });
            }}
            onRunError={({ clientRunId, message }) => {
              setCurrentRun((prev) => {
                if (prev.clientRunId !== clientRunId) return prev;
                return { ...prev, mode: "error", message };
              });
            }}
            onRunCancel={({ clientRunId }) => {
              setCurrentRun((prev) => {
                if (prev.clientRunId !== clientRunId) return prev;
                return { ...prev, mode: "cancelled", message: "已取消" };
              });
            }}
            onReset={() => setCurrentRun(EMPTY_RUN)}
          />
        </section>
        <RecentReports />
      </div>

      <aside className="lg:sticky lg:top-8 lg:self-start">
        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
          <div className="mb-4">
            <h2 className="text-sm font-semibold text-neutral-200">交付验收 · 项目把控</h2>
            <p className="mt-0.5 text-xs text-neutral-500">
              {currentRun.mode === "idle" ? "等待本次分析开始" : "跟随当前分析动态更新"}
            </p>
          </div>
          <Checklist
            mode={currentRun.mode}
            reportId={currentRun.reportId}
            liveStageReports={currentRun.stageReports}
            runArgs={currentRun.runArgs}
            message={currentRun.message}
          />
        </div>
      </aside>
    </div>
  );
}
