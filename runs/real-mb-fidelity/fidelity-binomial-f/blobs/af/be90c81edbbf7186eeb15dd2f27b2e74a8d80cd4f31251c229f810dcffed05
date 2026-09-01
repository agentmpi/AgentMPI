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
{"findings": ["[F-5-0] Component system-5.0 reported a measured throughput of 135 units per second under the nominal workload.", "[F-5-1] Component system-5.1 reported a measured throughput of 136 units per second under the degraded workload.", "[F-5-2] Component system-5.2 reported a measured throughput of 137 units per second under the saturated workload.", "[F-5-3] Component system-5.3 reported a measured throughput of 138 units per second under the nominal workload."], "source_rank": 5, "title": "Report from component group 5"}