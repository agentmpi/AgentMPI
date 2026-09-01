Merge the two reports below into a single consolidated report.

Requirements:
- Preserve every distinct factual item. Each item carries a bracketed identifier
  such as [F-3-2]. Keep every identifier that appears in either input, exactly as
  written, attached to its item.
- You may compress wording, but you may not drop an item.
- Your output must be at most 450 tokens. If you cannot fit everything at full
  length, shorten the wording of items rather than removing any of them.

Return ONLY a JSON object: {"title": "<short>", "findings": ["[F-x-y] <item>", ...]}

--- REPORT A ---
{"findings": ["[F-0-0]100n [F-0-1]101d [F-0-2]102s [F-0-3]103n [F-0-4]104d [F-0-5]105s [F-0-6]106n [F-0-7]107d [F-0-8]108s [F-0-9]109n [F-0-10]110d [F-0-11]111s", "[F-1-0]107n [F-1-1]108d [F-1-2]109s [F-1-3]110n [F-1-4]111d [F-1-5]112s [F-1-6]113n [F-1-7]114d [F-1-8]115s [F-1-9]116n [F-1-10]117d [F-1-11]118s"], "title": "Consolidated throughput report, component groups 0-1 (F-x-y = component system-x.y; value = measured throughput in units/s; n/d/s = nominal/degraded/saturated workload)"}

--- REPORT B ---
{"findings": ["[F-2-0] 114 u/s, nominal", "[F-2-1] 115 u/s, degraded", "[F-2-2] 116 u/s, saturated", "[F-2-3] 117 u/s, nominal", "[F-2-4] 118 u/s, degraded", "[F-2-5] 119 u/s, saturated", "[F-2-6] 120 u/s, nominal", "[F-2-7] 121 u/s, degraded", "[F-2-8] 122 u/s, saturated", "[F-2-9] 123 u/s, nominal", "[F-2-10] 124 u/s, degraded", "[F-2-11] 125 u/s, saturated", "[F-3-0] 121 u/s, nominal", "[F-3-1] 122 u/s, degraded", "[F-3-2] 123 u/s, saturated", "[F-3-3] 124 u/s, nominal", "[F-3-4] 125 u/s, degraded", "[F-3-5] 126 u/s, saturated", "[F-3-6] 127 u/s, nominal", "[F-3-7] 128 u/s, degraded", "[F-3-8] 129 u/s, saturated", "[F-3-9] 130 u/s, nominal", "[F-3-10] 131 u/s, degraded", "[F-3-11] 132 u/s, saturated"], "title": "Merged throughput report, component groups 2-3 (F-x-y = component system-x.y; values are measured throughput in units/s under the named workload)"}