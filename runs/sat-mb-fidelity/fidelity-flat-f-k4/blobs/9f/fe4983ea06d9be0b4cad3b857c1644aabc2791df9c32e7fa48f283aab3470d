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
{"findings": ["[F-0-0] 100 units/s, nominal", "[F-0-1] 101 units/s, degraded", "[F-0-2] 102 units/s, saturated", "[F-0-3] 103 units/s, nominal", "[F-0-4] 104 units/s, degraded", "[F-0-5] 105 units/s, saturated", "[F-0-6] 106 units/s, nominal", "[F-0-7] 107 units/s, degraded", "[F-0-8] 108 units/s, saturated", "[F-0-9] 109 units/s, nominal", "[F-0-10] 110 units/s, degraded", "[F-0-11] 111 units/s, saturated", "[F-1-0] 107 units/s, nominal", "[F-1-1] 108 units/s, degraded", "[F-1-2] 109 units/s, saturated", "[F-1-3] 110 units/s, nominal", "[F-1-4] 111 units/s, degraded", "[F-1-5] 112 units/s, saturated", "[F-1-6] 113 units/s, nominal", "[F-1-7] 114 units/s, degraded", "[F-1-8] 115 units/s, saturated", "[F-1-9] 116 units/s, nominal", "[F-1-10] 117 units/s, degraded", "[F-1-11] 118 units/s, saturated"], "title": "Consolidated throughput report, component groups 0-1 (F-x-y = component system-x.y; each item is its measured throughput under the named workload)"}

--- REPORT B ---
{"findings": ["[F-2-0] Component system-2.0 reported a measured throughput of 114 units per second under the nominal workload.", "[F-2-1] Component system-2.1 reported a measured throughput of 115 units per second under the degraded workload.", "[F-2-2] Component system-2.2 reported a measured throughput of 116 units per second under the saturated workload.", "[F-2-3] Component system-2.3 reported a measured throughput of 117 units per second under the nominal workload.", "[F-2-4] Component system-2.4 reported a measured throughput of 118 units per second under the degraded workload.", "[F-2-5] Component system-2.5 reported a measured throughput of 119 units per second under the saturated workload.", "[F-2-6] Component system-2.6 reported a measured throughput of 120 units per second under the nominal workload.", "[F-2-7] Component system-2.7 reported a measured throughput of 121 units per second under the degraded workload.", "[F-2-8] Component system-2.8 reported a measured throughput of 122 units per second under the saturated workload.", "[F-2-9] Component system-2.9 reported a measured throughput of 123 units per second under the nominal workload.", "[F-2-10] Component system-2.10 reported a measured throughput of 124 units per second under the degraded workload.", "[F-2-11] Component system-2.11 reported a measured throughput of 125 units per second under the saturated workload."], "source_rank": 2, "title": "Report from component group 2"}