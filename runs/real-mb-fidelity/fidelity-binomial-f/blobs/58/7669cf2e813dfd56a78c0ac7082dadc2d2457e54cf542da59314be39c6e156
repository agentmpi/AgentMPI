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
{"source_rank": 2, "title": "Report from component group 2", "findings": ["[F-2-0] Component system-2.0 reported a measured throughput of 114 units per second under the nominal workload.", "[F-2-1] Component system-2.1 reported a measured throughput of 115 units per second under the degraded workload.", "[F-2-2] Component system-2.2 reported a measured throughput of 116 units per second under the saturated workload.", "[F-2-3] Component system-2.3 reported a measured throughput of 117 units per second under the nominal workload."]}

--- REPORT B ---
{"findings": ["[F-3-0] Component system-3.0 reported a measured throughput of 121 units per second under the nominal workload.", "[F-3-1] Component system-3.1 reported a measured throughput of 122 units per second under the degraded workload.", "[F-3-2] Component system-3.2 reported a measured throughput of 123 units per second under the saturated workload.", "[F-3-3] Component system-3.3 reported a measured throughput of 124 units per second under the nominal workload."], "source_rank": 3, "title": "Report from component group 3"}