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
{"findings": ["[F-0-0] 100, nominal", "[F-0-1] 101, degraded", "[F-0-2] 102, saturated", "[F-0-3] 103, nominal", "[F-0-4] 104, degraded", "[F-0-5] 105, saturated", "[F-0-6] 106, nominal", "[F-0-7] 107, degraded", "[F-0-8] 108, saturated", "[F-0-9] 109, nominal", "[F-0-10] 110, degraded", "[F-0-11] 111, saturated", "[F-1-0] 107, nominal", "[F-1-1] 108, degraded", "[F-1-2] 109, saturated", "[F-1-3] 110, nominal", "[F-1-4] 111, degraded", "[F-1-5] 112, saturated", "[F-1-6] 113, nominal", "[F-1-7] 114, degraded", "[F-1-8] 115, saturated", "[F-1-9] 116, nominal", "[F-1-10] 117, degraded", "[F-1-11] 118, saturated", "[F-2-0] 114, nominal", "[F-2-1] 115, degraded", "[F-2-2] 116, saturated", "[F-2-3] 117, nominal", "[F-2-4] 118, degraded", "[F-2-5] 119, saturated", "[F-2-6] 120, nominal", "[F-2-7] 121, degraded", "[F-2-8] 122, saturated", "[F-2-9] 123, nominal", "[F-2-10] 124, degraded", "[F-2-11] 125, saturated"], "title": "Consolidated throughput report, component groups 0-2 (F-x-y = component system-x.y; value = measured throughput in units/s under the named workload)"}

--- REPORT B ---
{"findings": ["[F-3-0] Component system-3.0 reported a measured throughput of 121 units per second under the nominal workload.", "[F-3-1] Component system-3.1 reported a measured throughput of 122 units per second under the degraded workload.", "[F-3-2] Component system-3.2 reported a measured throughput of 123 units per second under the saturated workload.", "[F-3-3] Component system-3.3 reported a measured throughput of 124 units per second under the nominal workload.", "[F-3-4] Component system-3.4 reported a measured throughput of 125 units per second under the degraded workload.", "[F-3-5] Component system-3.5 reported a measured throughput of 126 units per second under the saturated workload.", "[F-3-6] Component system-3.6 reported a measured throughput of 127 units per second under the nominal workload.", "[F-3-7] Component system-3.7 reported a measured throughput of 128 units per second under the degraded workload.", "[F-3-8] Component system-3.8 reported a measured throughput of 129 units per second under the saturated workload.", "[F-3-9] Component system-3.9 reported a measured throughput of 130 units per second under the nominal workload.", "[F-3-10] Component system-3.10 reported a measured throughput of 131 units per second under the degraded workload.", "[F-3-11] Component system-3.11 reported a measured throughput of 132 units per second under the saturated workload."], "source_rank": 3, "title": "Report from component group 3"}