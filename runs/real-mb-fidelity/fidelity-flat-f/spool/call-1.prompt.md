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
{"source_rank": 0, "title": "Report from component group 0", "findings": ["[F-0-0] Component system-0.0 reported a measured throughput of 100 units per second under the nominal workload.", "[F-0-1] Component system-0.1 reported a measured throughput of 101 units per second under the degraded workload.", "[F-0-2] Component system-0.2 reported a measured throughput of 102 units per second under the saturated workload.", "[F-0-3] Component system-0.3 reported a measured throughput of 103 units per second under the nominal workload."]}

--- REPORT B ---
{"findings": ["[F-1-0] Component system-1.0 reported a measured throughput of 107 units per second under the nominal workload.", "[F-1-1] Component system-1.1 reported a measured throughput of 108 units per second under the degraded workload.", "[F-1-2] Component system-1.2 reported a measured throughput of 109 units per second under the saturated workload.", "[F-1-3] Component system-1.3 reported a measured throughput of 110 units per second under the nominal workload."], "source_rank": 1, "title": "Report from component group 1"}