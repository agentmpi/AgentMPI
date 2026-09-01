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
{"findings": ["[F-0-0] Component system-0.0: throughput 100 units/s under nominal workload.", "[F-0-1] Component system-0.1: throughput 101 units/s under degraded workload.", "[F-0-2] Component system-0.2: throughput 102 units/s under saturated workload.", "[F-0-3] Component system-0.3: throughput 103 units/s under nominal workload.", "[F-1-0] Component system-1.0: throughput 107 units/s under nominal workload.", "[F-1-1] Component system-1.1: throughput 108 units/s under degraded workload.", "[F-1-2] Component system-1.2: throughput 109 units/s under saturated workload.", "[F-1-3] Component system-1.3: throughput 110 units/s under nominal workload."], "title": "Consolidated report: component groups 0-1"}

--- REPORT B ---
{"findings": ["[F-2-0] Component system-2.0: measured throughput 114 units/second under nominal workload.", "[F-2-1] Component system-2.1: measured throughput 115 units/second under degraded workload.", "[F-2-2] Component system-2.2: measured throughput 116 units/second under saturated workload.", "[F-2-3] Component system-2.3: measured throughput 117 units/second under nominal workload.", "[F-3-0] Component system-3.0: measured throughput 121 units/second under nominal workload.", "[F-3-1] Component system-3.1: measured throughput 122 units/second under degraded workload.", "[F-3-2] Component system-3.2: measured throughput 123 units/second under saturated workload.", "[F-3-3] Component system-3.3: measured throughput 124 units/second under nominal workload."], "title": "Consolidated report from component groups 2 and 3"}