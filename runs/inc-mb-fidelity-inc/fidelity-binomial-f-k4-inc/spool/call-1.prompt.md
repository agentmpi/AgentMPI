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
{"source_rank": 2, "title": "Report from component group 2", "findings": ["[F-2-0] serial 201088 checksum TPJN", "[F-2-1] serial 712478 checksum BVZM", "[F-2-2] serial 442778 checksum TFLR", "[F-2-3] serial 318208 checksum TMHR", "[F-2-4] serial 773709 checksum XXXM", "[F-2-5] serial 683348 checksum PXMZ", "[F-2-6] serial 100052 checksum JRBC", "[F-2-7] serial 538942 checksum HPQJ", "[F-2-8] serial 471427 checksum GRTF", "[F-2-9] serial 713188 checksum LWGR", "[F-2-10] serial 636408 checksum VNSG", "[F-2-11] serial 958036 checksum XRPC"]}

--- REPORT B ---
{"findings": ["[F-3-0] serial 378761 checksum JTJL", "[F-3-1] serial 217677 checksum MLJF", "[F-3-2] serial 846271 checksum WJLM", "[F-3-3] serial 581634 checksum QZXW", "[F-3-4] serial 583199 checksum TPNZ", "[F-3-5] serial 864563 checksum RSQL", "[F-3-6] serial 248896 checksum DHLW", "[F-3-7] serial 460844 checksum HCQW", "[F-3-8] serial 551643 checksum MPGL", "[F-3-9] serial 972934 checksum DDRL", "[F-3-10] serial 763358 checksum FJKG", "[F-3-11] serial 213773 checksum LLGV"], "source_rank": 3, "title": "Report from component group 3"}