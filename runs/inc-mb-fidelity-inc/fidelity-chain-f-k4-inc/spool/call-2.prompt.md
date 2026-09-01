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
{"source_rank": 5, "title": "Report from component group 5", "findings": ["[F-5-0] serial 167117 checksum DPWZ", "[F-5-1] serial 922494 checksum XZBF", "[F-5-2] serial 659867 checksum KWHH", "[F-5-3] serial 946685 checksum KFHP", "[F-5-4] serial 195045 checksum TTNN", "[F-5-5] serial 848090 checksum TBDB", "[F-5-6] serial 333719 checksum QRXK", "[F-5-7] serial 171028 checksum RWKK", "[F-5-8] serial 284986 checksum RHGZ", "[F-5-9] serial 878799 checksum NNLQ", "[F-5-10] serial 448391 checksum QGFQ", "[F-5-11] serial 781461 checksum QXVX"]}

--- REPORT B ---
{"findings": ["[F-6-0] serial 963083 checksum WZKC", "[F-6-1] serial 581176 checksum WKSC", "[F-6-2] serial 381338 checksum BXMT", "[F-6-3] serial 174344 checksum QZRC", "[F-6-4] serial 902293 checksum FJJJ", "[F-6-5] serial 993380 checksum MXWW", "[F-6-6] serial 442530 checksum XGXX", "[F-6-7] serial 343767 checksum TVZP", "[F-6-8] serial 922536 checksum WPFZ", "[F-6-9] serial 547823 checksum JHHZ", "[F-6-10] serial 917211 checksum TPGJ", "[F-6-11] serial 627971 checksum TNDN", "[F-7-0] serial 586491 checksum KZZH", "[F-7-1] serial 947267 checksum ZZHF", "[F-7-2] serial 445125 checksum ZKZG", "[F-7-3] serial 238784 checksum RLTZ", "[F-7-4] serial 334665 checksum MTSC", "[F-7-5] serial 707377 checksum BBSK", "[F-7-6] serial 418800 checksum NLXJ", "[F-7-7] serial 416088 checksum SZTR", "[F-7-8] serial 829851 checksum CVGW", "[F-7-9] serial 605400 checksum QLPZ", "[F-7-10] serial 886909 checksum BXWG", "[F-7-11] serial 594647 checksum NFNM"], "title": "Serials: groups 6 and 7"}