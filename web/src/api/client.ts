import type {
  DomainsResponse,
  FollowupResponse,
  IntakeResponse,
  RunMeta,
  RunRecord,
  RunSettings
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function getDomains(): Promise<DomainsResponse> {
  return request<DomainsResponse>("/api/domains");
}

export function postIntake(message: string, domainKey: string): Promise<IntakeResponse> {
  return request<IntakeResponse>("/api/intake", {
    method: "POST",
    body: JSON.stringify({ message, domain_key: domainKey })
  });
}

export function createRun(meta: RunMeta, settings: RunSettings): Promise<{ run_id: string; status: string }> {
  return request<{ run_id: string; status: string }>("/api/runs", {
    method: "POST",
    body: JSON.stringify({ meta, settings })
  });
}

export function getRun(runId: string): Promise<RunRecord> {
  return request<RunRecord>(`/api/runs/${runId}`);
}

export function postFollowup(runId: string, message: string): Promise<FollowupResponse> {
  return request<FollowupResponse>(`/api/runs/${runId}/followups`, {
    method: "POST",
    body: JSON.stringify({ message })
  });
}
