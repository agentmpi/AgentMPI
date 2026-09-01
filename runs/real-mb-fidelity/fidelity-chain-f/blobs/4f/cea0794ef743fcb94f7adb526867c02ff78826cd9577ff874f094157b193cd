Merge the two reports below into a single consolidated report.

Requirements:
- Preserve every distinct factual item. Each item carries a bracketed identifier
  such as [F-3-2]. Keep every identifier that appears in either input, exactly as
  written, attached to its item.
- You may compress wording, but you may not drop an item.
- Your output must be at most 900 tokens. If you cannot fit everything at full
  length, shorten the wording of items rather than removing any of them.

Return ONLY a JSON object: {"title": "<short>", "findings": ["[F-x-y] <item>", ...]}

--- REPORT A ---
{"source_rank": 4, "title": "Report from component group 4", "findings": ["[F-4-0] Component system-4.0 reported a measured throughput of 128 units per second under the nominal workload.", "[F-4-1] Component system-4.1 reported a measured throughput of 129 units per second under the degraded workload.", "[F-4-2] Component system-4.2 reported a measured throughput of 130 units per second under the saturated workload.", "[F-4-3] Component system-4.3 reported a measured throughput of 131 units per second under the nominal workload."]}

--- REPORT B ---
{"findings": ["[F-5-0] system-5.0: throughput 135 units/s, nominal workload.", "[F-5-1] system-5.1: throughput 136 units/s, degraded workload.", "[F-5-2] system-5.2: throughput 137 units/s, saturated workload.", "[F-5-3] system-5.3: throughput 138 units/s, nominal workload.", "[F-6-0] system-6.0: throughput 142 units/s, nominal workload.", "[F-6-1] system-6.1: throughput 143 units/s, degraded workload.", "[F-6-2] system-6.2: throughput 144 units/s, saturated workload.", "[F-6-3] system-6.3: throughput 145 units/s, nominal workload.", "[F-7-0] system-7.0: throughput 149 units/s, nominal workload.", "[F-7-1] system-7.1: throughput 150 units/s, degraded workload.", "[F-7-2] system-7.2: throughput 151 units/s, saturated workload.", "[F-7-3] system-7.3: throughput 152 units/s, nominal workload."], "title": "Consolidated report: component groups 5, 6 and 7"}