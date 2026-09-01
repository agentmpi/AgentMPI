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
{"source_rank": 1, "title": "Report from component group 1", "findings": ["[F-1-0] Component system-1.0 reported a measured throughput of 107 units per second under the nominal workload.", "[F-1-1] Component system-1.1 reported a measured throughput of 108 units per second under the degraded workload.", "[F-1-2] Component system-1.2 reported a measured throughput of 109 units per second under the saturated workload.", "[F-1-3] Component system-1.3 reported a measured throughput of 110 units per second under the nominal workload.", "[F-1-4] Component system-1.4 reported a measured throughput of 111 units per second under the degraded workload.", "[F-1-5] Component system-1.5 reported a measured throughput of 112 units per second under the saturated workload.", "[F-1-6] Component system-1.6 reported a measured throughput of 113 units per second under the nominal workload.", "[F-1-7] Component system-1.7 reported a measured throughput of 114 units per second under the degraded workload.", "[F-1-8] Component system-1.8 reported a measured throughput of 115 units per second under the saturated workload.", "[F-1-9] Component system-1.9 reported a measured throughput of 116 units per second under the nominal workload.", "[F-1-10] Component system-1.10 reported a measured throughput of 117 units per second under the degraded workload.", "[F-1-11] Component system-1.11 reported a measured throughput of 118 units per second under the saturated workload."]}

--- REPORT B ---
{"findings": ["[F-2-0] 114n [F-2-1] 115d [F-2-2] 116s [F-2-3] 117n [F-2-4] 118d [F-2-5] 119s [F-2-6] 120n [F-2-7] 121d [F-2-8] 122s [F-2-9] 123n [F-2-10] 124d [F-2-11] 125s", "[F-3-0] 121n [F-3-1] 122d [F-3-2] 123s [F-3-3] 124n [F-3-4] 125d [F-3-5] 126s [F-3-6] 127n [F-3-7] 128d [F-3-8] 129s [F-3-9] 130n [F-3-10] 131d [F-3-11] 132s", "[F-4-0] 128n [F-4-1] 129d [F-4-2] 130s [F-4-3] 131n [F-4-4] 132d [F-4-5] 133s [F-4-6] 134n [F-4-7] 135d [F-4-8] 136s [F-4-9] 137n [F-4-10] 138d [F-4-11] 139s", "[F-5-0] 135n [F-5-1] 136d [F-5-2] 137s [F-5-3] 138n [F-5-4] 139d [F-5-5] 140s [F-5-6] 141n [F-5-7] 142d [F-5-8] 143s [F-5-9] 144n [F-5-10] 145d [F-5-11] 146s", "[F-6-0] 142n [F-6-1] 143d [F-6-2] 144s [F-6-3] 145n [F-6-4] 146d [F-6-5] 147s [F-6-6] 148n [F-6-7] 149d [F-6-8] 150s [F-6-9] 151n [F-6-10] 152d [F-6-11] 153s", "[F-7-0] 149n [F-7-1] 150d [F-7-2] 151s [F-7-3] 152n [F-7-4] 153d [F-7-5] 154s [F-7-6] 155n [F-7-7] 156d [F-7-8] 157s [F-7-9] 158n [F-7-10] 159d [F-7-11] 160s"], "title": "Merged throughput report, component groups 2-7 (units/s; F-x-y = system-x.y; n/d/s = nominal/degraded/saturated workload)"}