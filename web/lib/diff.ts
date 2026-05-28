import type { Report } from "./types";
import type { SupportStatus } from "./schema";

export interface CellChange {
  feature: string;
  product: string;
  fromStatus?: SupportStatus;
  toStatus?: SupportStatus;
  fromScore?: number;
  toScore?: number;
}

export interface ListChange {
  added: string[];
  removed: string[];
}

export interface ReportDiff {
  prevId: string;
  prevDate: string;
  currDate: string;
  qualityDelta: number | null;
  evidenceDelta: number;
  cellChanges: CellChange[];
  features: ListChange;
  recommendations: ListChange;
  competitors: ListChange;
}

function featureCells(r: Report): Map<string, { status: SupportStatus; score?: number }> {
  const m = new Map<string, { status: SupportStatus; score?: number }>();
  for (const f of r.schema_draft?.feature_tree?.features ?? []) {
    for (const [product, cell] of Object.entries(f.products)) {
      m.set(`${f.name}@@${product}`, {
        status: cell.support_status,
        score: cell.quality_score?.score,
      });
    }
  }
  return m;
}

function listDiff(prev: string[], curr: string[]): ListChange {
  const ps = new Set(prev);
  const cs = new Set(curr);
  return {
    added: curr.filter((x) => !ps.has(x)),
    removed: prev.filter((x) => !cs.has(x)),
  };
}

/** 找同 target、更早、最近的一份报告。 */
export function findPrevious<T extends { report_id: string; target_product: string; created_at: string }>(
  index: T[],
  currentId: string
): T | null {
  const cur = index.find((r) => r.report_id === currentId);
  if (!cur) return null;
  const candidates = index
    .filter(
      (r) =>
        r.report_id !== currentId &&
        r.target_product === cur.target_product &&
        r.created_at < cur.created_at
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  return candidates[0] ?? null;
}

export function computeDiff(prev: Report, curr: Report): ReportDiff {
  const prevCells = featureCells(prev);
  const currCells = featureCells(curr);
  const cellChanges: CellChange[] = [];

  for (const [key, c] of currCells) {
    const p = prevCells.get(key);
    if (!p) continue; // 新功能/产品归到 features/competitors diff
    if (p.status !== c.status || p.score !== c.score) {
      const [feature, product] = key.split("@@");
      cellChanges.push({
        feature,
        product,
        fromStatus: p.status,
        toStatus: c.status,
        fromScore: p.score,
        toScore: c.score,
      });
    }
  }

  const featNames = (r: Report) =>
    (r.schema_draft?.feature_tree?.features ?? []).map((f) => f.name);
  const recActions = (r: Report) =>
    (r.schema_draft?.recommendations ?? []).map((x) => x.action);

  return {
    prevId: prev.report_id,
    prevDate: prev.created_at,
    currDate: curr.created_at,
    qualityDelta:
      curr.quality_report?.quality_score != null && prev.quality_report?.quality_score != null
        ? curr.quality_report.quality_score - prev.quality_report.quality_score
        : null,
    evidenceDelta: curr.raw_evidence.length - prev.raw_evidence.length,
    cellChanges,
    features: listDiff(featNames(prev), featNames(curr)),
    recommendations: listDiff(recActions(prev), recActions(curr)),
    competitors: listDiff(prev.meta.competitors, curr.meta.competitors),
  };
}
