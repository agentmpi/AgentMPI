import type { RunInfo, Trace } from "./types";

const json = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { error?: string };
      if (body.error) detail = body.error;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
};

export const listRuns = () => json<{ runs: RunInfo[] }>("/api/runs").then((r) => r.runs);

export const getTrace = (run: string) =>
  json<Trace>(`/api/trace?run=${encodeURIComponent(run)}`);

export const getPayload = (run: string, handle: string, budget = 1200, op = "headtail") =>
  json<{ handle: string; tokens: number; summary: string | null; view_tokens: number; body: string }>(
    `/api/object?run=${encodeURIComponent(run)}&id=${encodeURIComponent(handle)}&budget=${budget}&op=${op}`,
  );
