import type { RunEvent } from "../types";

export function subscribeRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError: (error: Event) => void
): EventSource {
  const source = new EventSource(`/api/runs/${runId}/events`);
  const names = ["run_started", "analyzer_step", "llm_usage", "node_completed", "final", "error"];

  for (const name of names) {
    source.addEventListener(name, (evt) => {
      const message = evt as MessageEvent<string>;
      onEvent({
        event: name,
        data: JSON.parse(message.data) as Record<string, unknown>
      });
      if (name === "final" || name === "error") {
        source.close();
      }
    });
  }

  source.onerror = onError;
  return source;
}
