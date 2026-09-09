Translate these Chinese ({{source_lang}})->Vietnamese ({{target_lang}}) subtitles for an Urban / Modern / Workplace video or drama with scene-level context.{{style_clause}}

IMPORTANT: Output ONLY the translation. Do NOT think, explain, or comment. No greetings, no analysis, no markdown. Return EXACTLY numbered lines, one per input item:
Format: N. translated text

### Urban & Modern Translation Rules:
1. Names & Places:
   - Character names: Translate to standard Hán-Việt (e.g. 李明 -> Lý Minh, 陆远秋 -> Lục Viễn Thu).
   - Foreign / Western names transcribed in Chinese: Restore the canonical Latin spelling (e.g. 凯尔 -> Kyle, 大卫 -> David).
   - Brands & Apps: Keep familiar names (e.g. 微信 -> WeChat, 朋友圈 -> Moments / vòng bạn bè, 热搜 -> Hot search / bảng tìm kiếm).

2. Workplace & Social Hierarchy & Address Continuity (Xưng hô công sở & xã hội - Quy tắc nhất quán):
   - Dialogue Roles: Identify who is speaking to whom in each exchange before assigning pronouns.
   - Address forms: Match corporate status and interpersonal warmth:
     * Superior to subordinate: anh/chị - em, tôi - cậu/cô.
     * Subordinate to superior: em - sếp/anh/chị, tôi - tổng giám đốc.
     * Colleagues/peers: mình - cậu, anh - em, tôi - anh.
     * Lovers/Couples: anh - em consistently.
     * Confrontation/Hostility/Stalking (Đối đầu, bám đuôi, đe dọa):
       - Opponents in disputes, threats, or harassment MUST use "mày - tao" (or cold "tôi - anh/cô"). NEVER use polite "cậu - tôi" or affectionate "anh - em" in hostile scenes!
       - In threats (e.g. "你等着", "找死"), translate forcefully ("Mày cứ đợi đấy" / "Cô cứ đợi đấy"), NEVER soft ("Em cứ đợi đấy").
       - When a female character confronts or rejects a stalker/ex/harasser (e.g. introducing boyfriend "这就是我现男友", demanding answers "你听着没"): MUST refer to herself as "tôi" (e.g. "Đây chính là bạn trai hiện tại của tôi!"), NEVER xưng "em" with the stalker/harasser.
     * Slang & Colloquial: 哥们 (gēmen) addressed to a female or friend -> cô nương / em gái / bạn à / này (NEVER "ông bạn" or "người anh em"). 妹子 -> em gái / cô em. 学长/学姐 -> học trưởng/anh/chị khóa trên (em khóa dưới xưng em). 对象 -> người yêu / bạn trai / bạn gái.
   - Strict Pair Continuity (Chống lật ngôi): When character A addresses character B, the chosen pronoun pair MUST NOT fluctuate across dialogue cues. Keep the chosen address register (e.g. "anh - em" for senior/junior or couples; "mày - tao" for opponents) consistently across the entire interaction. NOTE: If automated diarization tags merge multiple speakers (e.g. protagonist and stalker share SPEAKER_00), do NOT force "anh - em" onto lines clearly spoken by or to the antagonist/stalker.
   - AVOID archaic/wuxia diction (DO NOT use "các hạ", "tại hạ", "ngươi", "hắn", "bản tọa" in modern settings).

3. Subtitle Compactness:
   - Make dialogue flow smoothly and naturally in modern spoken Vietnamese. Avoid stiff Chinese grammatical structures ("một cách", "đối với", "tiến hành").
   - Resolve omitted subjects from scene context.

{{context_guidance}}

Never merge, omit, reorder, or split cue numbers.