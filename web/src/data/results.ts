import microbench from "../../public/results/microbench.json";
import translation from "../../public/results/translation.json";
import collab from "../../public/results/collab.json";
import fault from "../../public/results/fault.json";
import scale from "../../public/results/scale.json";
import scale32 from "../../public/results/scale32.json";
import cursorScale from "../../public/results/cursor_scale.json";

export const results = {
  microbench,
  translation,
  collab,
  fault,
  scale,
  scale32,
  cursorScale,
};

export function cursorScaleSummary() {
  const fables = results.cursorScale.items.reduce((n, rank) => n + rank.items.length, 0);
  return {
    n: results.cursorScale.n,
    completed: results.cursorScale.completed,
    fables,
    missing: results.cursorScale.missing_ranks.length,
    sample: results.cursorScale.items[0]?.items ?? [],
  };
}

export function ms(row: { timing?: { median_s: number } } | undefined): string {
  if (!row?.timing) return "—";
  return `${(row.timing.median_s * 1000).toFixed(1)} ms`;
}

export function findRow(kernel: string, n: number) {
  return results.microbench.rows.find((r) => r.kernel === kernel && r.n === n);
}
