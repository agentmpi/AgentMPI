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
{"findings": ["[F-0-0]100n [F-0-1]101d [F-0-2]102s [F-0-3]103n [F-0-4]104d [F-0-5]105s [F-0-6]106n [F-0-7]107d [F-0-8]108s [F-0-9]109n [F-0-10]110d [F-0-11]111s", "[F-1-0]107n [F-1-1]108d [F-1-2]109s [F-1-3]110n [F-1-4]111d [F-1-5]112s [F-1-6]113n [F-1-7]114d [F-1-8]115s [F-1-9]116n [F-1-10]117d [F-1-11]118s", "[F-2-0]114n [F-2-1]115d [F-2-2]116s [F-2-3]117n [F-2-4]118d [F-2-5]119s [F-2-6]120n [F-2-7]121d [F-2-8]122s [F-2-9]123n [F-2-10]124d [F-2-11]125s", "[F-3-0]121n [F-3-1]122d [F-3-2]123s [F-3-3]124n [F-3-4]125d [F-3-5]126s [F-3-6]127n [F-3-7]128d [F-3-8]129s [F-3-9]130n [F-3-10]131d [F-3-11]132s", "[F-4-0]128n [F-4-1]129d [F-4-2]130s [F-4-3]131n [F-4-4]132d [F-4-5]133s [F-4-6]134n [F-4-7]135d [F-4-8]136s [F-4-9]137n [F-4-10]138d [F-4-11]139s"], "title": "Consolidated throughput report, component groups 0-4 (F-x-y = component system-x.y; value = measured throughput in units/s; n/d/s = nominal/degraded/saturated workload)"}

--- REPORT B ---
{"findings": ["[F-5-0] Component system-5.0 reported a measured throughput of 135 units per second under the nominal workload.", "[F-5-1] Component system-5.1 reported a measured throughput of 136 units per second under the degraded workload.", "[F-5-2] Component system-5.2 reported a measured throughput of 137 units per second under the saturated workload.", "[F-5-3] Component system-5.3 reported a measured throughput of 138 units per second under the nominal workload.", "[F-5-4] Component system-5.4 reported a measured throughput of 139 units per second under the degraded workload.", "[F-5-5] Component system-5.5 reported a measured throughput of 140 units per second under the saturated workload.", "[F-5-6] Component system-5.6 reported a measured throughput of 141 units per second under the nominal workload.", "[F-5-7] Component system-5.7 reported a measured throughput of 142 units per second under the degraded workload.", "[F-5-8] Component system-5.8 reported a measured throughput of 143 units per second under the saturated workload.", "[F-5-9] Component system-5.9 reported a measured throughput of 144 units per second under the nominal workload.", "[F-5-10] Component system-5.10 reported a measured throughput of 145 units per second under the degraded workload.", "[F-5-11] Component system-5.11 reported a measured throughput of 146 units per second under the saturated workload."], "source_rank": 5, "title": "Report from component group 5"}