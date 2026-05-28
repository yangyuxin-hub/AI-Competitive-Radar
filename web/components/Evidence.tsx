"use client";

import { createContext, useContext, useEffect, useState } from "react";
import type { Evidence } from "@/lib/types";

const EvidenceCtx = createContext<{
  open: (id: string) => void;
  has: (id: string) => boolean;
}>({ open: () => {}, has: () => false });

export function EvidenceProvider({
  evidence,
  children,
}: {
  evidence: Evidence[];
  children: React.ReactNode;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const map = new Map(evidence.map((e) => [e.evidence_id, e]));
  const active = activeId ? map.get(activeId) ?? null : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setActiveId(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <EvidenceCtx.Provider value={{ open: setActiveId, has: (id) => map.has(id) }}>
      {children}
      {active && <EvidencePanel evidence={active} onClose={() => setActiveId(null)} />}
    </EvidenceCtx.Provider>
  );
}

export function Chip({ id }: { id: string }) {
  const { open, has } = useContext(EvidenceCtx);
  const known = has(id);
  return (
    <button
      onClick={() => known && open(id)}
      disabled={!known}
      title={known ? "查看证据" : "证据缺失"}
      className={`mx-0.5 inline-flex items-center rounded px-1.5 py-0.5 align-baseline font-mono text-[11px] transition ${
        known
          ? "bg-sky-500/15 text-sky-400 hover:bg-sky-500/30"
          : "bg-white/5 text-neutral-600"
      }`}
    >
      {id}
    </button>
  );
}

export function Chips({ ids }: { ids?: string[] }) {
  if (!ids || ids.length === 0) return null;
  return (
    <span className="ml-1 inline-flex flex-wrap gap-0.5 align-baseline">
      {ids.map((id) => (
        <Chip key={id} id={id} />
      ))}
    </span>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded bg-white/5 px-2 py-0.5 text-xs text-neutral-400">{children}</span>
  );
}

function EvidencePanel({
  evidence,
  onClose,
}: {
  evidence: Evidence;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto border-l border-white/10 bg-neutral-950 p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <span className="font-mono text-sm text-sky-400">{evidence.evidence_id}</span>
          <button onClick={onClose} className="text-neutral-500 hover:text-white">
            ✕ <span className="text-xs">Esc</span>
          </button>
        </div>
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap gap-2">
            <Tag>{evidence.product}</Tag>
            <Tag>{evidence.claim_type}</Tag>
            <Tag>{evidence.source_bias}</Tag>
            <Tag>可信度 {evidence.source_reliability}</Tag>
          </div>
          <div>
            <div className="mb-1 text-xs text-neutral-500">结论</div>
            <p className="text-neutral-200">{evidence.claim}</p>
          </div>
          <div>
            <div className="mb-1 text-xs text-neutral-500">原文片段</div>
            <blockquote className="border-l-2 border-sky-500/40 pl-3 italic text-neutral-300">
              {evidence.extracted_snippet}
            </blockquote>
          </div>
          <a
            href={evidence.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-block break-all text-xs text-sky-500 hover:underline"
          >
            {evidence.source_url}
          </a>
          <div className="text-xs text-neutral-500">
            来源类型 {evidence.source_type} · 观测 {evidence.observed_at} · 新鲜度{" "}
            {evidence.source_freshness}
          </div>
        </div>
      </div>
    </div>
  );
}
