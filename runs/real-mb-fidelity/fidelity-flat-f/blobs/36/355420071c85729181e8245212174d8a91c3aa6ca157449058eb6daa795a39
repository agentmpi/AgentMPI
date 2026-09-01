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
{"findings": ["[F-0-0] Component system-0.0: throughput 100 units/s under nominal workload.", "[F-0-1] Component system-0.1: throughput 101 units/s under degraded workload.", "[F-0-2] Component system-0.2: throughput 102 units/s under saturated workload.", "[F-0-3] Component system-0.3: throughput 103 units/s under nominal workload.", "[F-1-0] Component system-1.0: throughput 107 units/s under nominal workload.", "[F-1-1] Component system-1.1: throughput 108 units/s under degraded workload.", "[F-1-2] Component system-1.2: throughput 109 units/s under saturated workload.", "[F-1-3] Component system-1.3: throughput 110 units/s under nominal workload.", "[F-2-0] Component system-2.0: throughput 114 units/s under nominal workload.", "[F-2-1] Component system-2.1: throughput 115 units/s under degraded workload.", "[F-2-2] Component system-2.2: throughput 116 units/s under saturated workload.", "[F-2-3] Component system-2.3: throughput 117 units/s under nominal workload.", "[F-3-0] Component system-3.0: throughput 121 units/s under nominal workload.", "[F-3-1] Component system-3.1: throughput 122 units/s under degraded workload.", "[F-3-2] Component system-3.2: throughput 123 units/s under saturated workload.", "[F-3-3] Component system-3.3: throughput 124 units/s under nominal workload.", "[F-4-0] Component system-4.0: throughput 128 units/s under nominal workload.", "[F-4-1] Component system-4.1: throughput 129 units/s under degraded workload.", "[F-4-2] Component system-4.2: throughput 130 units/s under saturated workload.", "[F-4-3] Component system-4.3: throughput 131 units/s under nominal workload."], "title": "Consolidated report: component groups 0-4"}

--- REPORT B ---
{"findings": ["[F-5-0] Component system-5.0 reported a measured throughput of 135 units per second under the nominal workload.", "[F-5-1] Component system-5.1 reported a measured throughput of 136 units per second under the degraded workload.", "[F-5-2] Component system-5.2 reported a measured throughput of 137 units per second under the saturated workload.", "[F-5-3] Component system-5.3 reported a measured throughput of 138 units per second under the nominal workload."], "source_rank": 5, "title": "Report from component group 5"}