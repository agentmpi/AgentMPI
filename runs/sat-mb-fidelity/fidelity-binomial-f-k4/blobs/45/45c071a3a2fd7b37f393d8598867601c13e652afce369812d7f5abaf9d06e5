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
{"findings": ["[F-4-0] 128 nominal", "[F-4-1] 129 degraded", "[F-4-2] 130 saturated", "[F-4-3] 131 nominal", "[F-4-4] 132 degraded", "[F-4-5] 133 saturated", "[F-4-6] 134 nominal", "[F-4-7] 135 degraded", "[F-4-8] 136 saturated", "[F-4-9] 137 nominal", "[F-4-10] 138 degraded", "[F-4-11] 139 saturated", "[F-5-0] 135 nominal", "[F-5-1] 136 degraded", "[F-5-2] 137 saturated", "[F-5-3] 138 nominal", "[F-5-4] 139 degraded", "[F-5-5] 140 saturated", "[F-5-6] 141 nominal", "[F-5-7] 142 degraded", "[F-5-8] 143 saturated", "[F-5-9] 144 nominal", "[F-5-10] 145 degraded", "[F-5-11] 146 saturated"], "title": "Merged throughput report, component groups 4-5 (units/s; F-x-y = system-x.y)"}

--- REPORT B ---
{"findings": ["[F-6-0] system-6.0: 142 units/s, nominal workload.", "[F-6-1] system-6.1: 143 units/s, degraded workload.", "[F-6-2] system-6.2: 144 units/s, saturated workload.", "[F-6-3] system-6.3: 145 units/s, nominal workload.", "[F-6-4] system-6.4: 146 units/s, degraded workload.", "[F-6-5] system-6.5: 147 units/s, saturated workload.", "[F-6-6] system-6.6: 148 units/s, nominal workload.", "[F-6-7] system-6.7: 149 units/s, degraded workload.", "[F-6-8] system-6.8: 150 units/s, saturated workload.", "[F-6-9] system-6.9: 151 units/s, nominal workload.", "[F-6-10] system-6.10: 152 units/s, degraded workload.", "[F-6-11] system-6.11: 153 units/s, saturated workload.", "[F-7-0] system-7.0: 149 units/s, nominal workload.", "[F-7-1] system-7.1: 150 units/s, degraded workload.", "[F-7-2] system-7.2: 151 units/s, saturated workload.", "[F-7-3] system-7.3: 152 units/s, nominal workload.", "[F-7-4] system-7.4: 153 units/s, degraded workload.", "[F-7-5] system-7.5: 154 units/s, saturated workload.", "[F-7-6] system-7.6: 155 units/s, nominal workload.", "[F-7-7] system-7.7: 156 units/s, degraded workload.", "[F-7-8] system-7.8: 157 units/s, saturated workload.", "[F-7-9] system-7.9: 158 units/s, nominal workload.", "[F-7-10] system-7.10: 159 units/s, degraded workload.", "[F-7-11] system-7.11: 160 units/s, saturated workload."], "title": "Consolidated throughput report: component groups 6 and 7"}