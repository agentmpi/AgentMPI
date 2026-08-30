import microbench from "../../public/results/microbench.json";
import translation from "../../public/results/translation.json";
import collab from "../../public/results/collab.json";
import fault from "../../public/results/fault.json";
import scale from "../../public/results/scale.json";
import scale32 from "../../public/results/scale32.json";

export const results = { microbench, translation, collab, fault, scale, scale32 };

export function ms(row: { timing?: { median_s: number } } | undefined): string {
  if (!row?.timing) return "—";
  return `${(row.timing.median_s * 1000).toFixed(1)} ms`;
}

export function findRow(kernel: string, n: number) {
  return results.microbench.rows.find((r) => r.kernel === kernel && r.n === n);
}
