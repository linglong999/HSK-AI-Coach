> **⚠️ 已废弃（2026-09-08）** — 本报告为"子串疑似"版，误报较多，已被 `kp_meta_compare.md`（item 判定版）+ 附表 `kp_meta_compare_full.csv` 取代。请勿再以本报告作为对照依据。

# v1_4 知识点 ↔ 考纲 疑似映射报告

- v1_4 25 个知识点（教学抽象层）· 考纲 593 条（官方目录层）
- 两套命名体系字符串无交集，自动对照只能给**核心词疑似**，精确映射须人工裁定

## A. 精确疑似（核心词完整出现在考纲条目中，高置信）

## B. 疑似命中（核心词子串命中，需人工裁定）

- `kp-ba-sentence` (“把”字句) → 命中 14 条：hsk30-g3-012, hsk30-g3-027, hsk30-g3-067, hsk30-g3-068, hsk30-g3-069, hsk30-g4-055, hsk30-g4-056, hsk30-g4-057, hsk30-g4-058, hsk30-g5-051, hsk30-g5-052, hsk30-g6-034
- `kp-bei-sentence` (“被”字句) → 命中 4 条：hsk30-g3-070, hsk30-g3-071, hsk30-g4-059, hsk30-g5-055
- `kp-liangci` (量词（个/本/张/件…）) → 命中 28 条：hsk30-g1-012, hsk30-g1-013, hsk30-g1-014, hsk30-g2-011, hsk30-g2-012, hsk30-g2-013, hsk30-g2-075, hsk30-g2-077, hsk30-g2-078, hsk30-g3-012, hsk30-g3-013, hsk30-g3-014
- `kp-nengyuan-dongci` (能愿动词（能/会/可以/要/想）) → 命中 6 条：hsk30-g1-004, hsk30-g1-005, hsk30-g1-006, hsk30-g2-004, hsk30-g3-004, hsk30-g4-001
- `kp-bi-sentence` (比较句（比/A比B…）) → 命中 16 条：hsk30-g2-025, hsk30-g2-061, hsk30-g2-062, hsk30-g2-063, hsk30-g2-064, hsk30-g3-016, hsk30-g3-073, hsk30-g3-074, hsk30-g3-075, hsk30-g3-076, hsk30-g3-077, hsk30-g4-033
- `kp-he-yiyang` (“和…一样”比较) → 命中 2 条：hsk30-g3-074, hsk30-g3-075
- `kp-le-dynamic` (动态助词“了”（完成态）) → 命中 41 条：hsk30-g1-025, hsk30-g1-026, hsk30-g1-062, hsk30-g1-063, hsk30-g2-003, hsk30-g2-031, hsk30-g2-032, hsk30-g2-043, hsk30-g2-044, hsk30-g2-074, hsk30-g3-026, hsk30-g3-044
- `kp-zhe` (动态助词“着”（进行/持续态）) → 命中 25 条：hsk30-g1-025, hsk30-g1-062, hsk30-g1-063, hsk30-g2-031, hsk30-g2-032, hsk30-g2-060, hsk30-g2-072, hsk30-g2-074, hsk30-g3-072, hsk30-g3-078, hsk30-g3-079, hsk30-g4-014
- `kp-guo` (动态助词“过”（经历态）) → 命中 19 条：hsk30-g1-025, hsk30-g1-062, hsk30-g1-063, hsk30-g2-031, hsk30-g2-032, hsk30-g2-050, hsk30-g2-051, hsk30-g2-074, hsk30-g3-078, hsk30-g3-079, hsk30-g4-020, hsk30-g4-079
- `kp-jiuguo-jiegou` (结果补语（做完/听懂/学会）) → 命中 32 条：hsk30-g1-041, hsk30-g2-048, hsk30-g2-049, hsk30-g2-050, hsk30-g2-051, hsk30-g2-052, hsk30-g2-053, hsk30-g2-054, hsk30-g2-062, hsk30-g3-057, hsk30-g3-058, hsk30-g3-059
- `kp-quxiang-buyu` (趋向补语（回来/上去/下去）) → 命中 9 条：hsk30-g2-049, hsk30-g2-050, hsk30-g2-051, hsk30-g3-058, hsk30-g3-059, hsk30-g3-060, hsk30-g3-069, hsk30-g3-078, hsk30-g5-045
- `kp-de-di-de` (结构助词的地得（定语/状语/补语）) → 命中 6 条：hsk30-g1-024, hsk30-g2-030, hsk30-g2-065, hsk30-g6-010, hsk30-g6-018, hsk30-g79-065
- `kp-cunxian-ju` (存现句（有/在/是处所主语）) → 命中 5 条：hsk30-g1-054, hsk30-g1-055, hsk30-g2-060, hsk30-g3-078, hsk30-g3-079
- `kp-liandong-ju` (连动句) → 命中 5 条：hsk30-g1-056, hsk30-g1-057, hsk30-g2-036, hsk30-g3-072, hsk30-g5-053
- `kp-standing-shi` (兼语句（请/让/叫）) → 命中 4 条：hsk30-g2-066, hsk30-g4-060, hsk30-g4-061, hsk30-g4-062
- `kp-shide-sentence` (“是…的”强调句) → 命中 3 条：hsk30-g2-065, hsk30-g3-080, hsk30-g79-061
- `kp-dongci-shuangbin` (双宾语句（V+人+物）) → 命中 7 条：hsk30-g1-058, hsk30-g2-066, hsk30-g2-067, hsk30-g3-068, hsk30-g4-060, hsk30-g4-061, hsk30-g5-052
- `kp-zhuangyu-chezhi` (状语语序（时间/地点/方式）) → 命中 3 条：hsk30-g1-040, hsk30-g4-058, hsk30-g5-042
- `kp-zhongci-zhitou` (代词使用（人称/指示代词）) → 命中 23 条：hsk30-g1-008, hsk30-g1-009, hsk30-g1-010, hsk30-g1-035, hsk30-g1-036, hsk30-g1-038, hsk30-g2-006, hsk30-g2-007, hsk30-g2-008, hsk30-g3-007, hsk30-g3-008, hsk30-g3-009
- `kp-preposition-zaizai` (介词及其误用（在/从/给/向）) → 命中 64 条：hsk30-g1-017, hsk30-g1-021, hsk30-g1-022, hsk30-g1-064, hsk30-g1-065, hsk30-g2-021, hsk30-g2-022, hsk30-g2-023, hsk30-g2-024, hsk30-g2-025, hsk30-g2-026, hsk30-g2-027
- `kp-jietiaoyu-tiaojian` (关联词/条件复句（如果…就/因为…所以）) → 命中 103 条：hsk30-g1-059, hsk30-g1-060, hsk30-g1-061, hsk30-g2-027, hsk30-g2-029, hsk30-g2-068, hsk30-g2-069, hsk30-g2-070, hsk30-g2-071, hsk30-g3-030, hsk30-g3-082, hsk30-g3-083
- `kp-haishi-xuanze` (选择问句（还是）与正反问（…吗/…不…）) → 命中 10 条：hsk30-g1-047, hsk30-g1-048, hsk30-g1-049, hsk30-g2-020, hsk30-g2-029, hsk30-g2-042, hsk30-g2-056, hsk30-g2-068, hsk30-g3-065, hsk30-g5-066
- `kp-chengdu-jieci` (程度副词（很/太/非常/比较）) → 命中 8 条：hsk30-g1-015, hsk30-g2-014, hsk30-g3-016, hsk30-g3-062, hsk30-g4-006, hsk30-g5-005, hsk30-g6-006, hsk30-g79-011
- `kp-zhizhi-dao` (时量补语（看了一个小时）) → 命中 32 条：hsk30-g1-041, hsk30-g2-048, hsk30-g2-049, hsk30-g2-050, hsk30-g2-051, hsk30-g2-052, hsk30-g2-053, hsk30-g2-054, hsk30-g2-062, hsk30-g3-057, hsk30-g3-058, hsk30-g3-059
- `kp-zhongci-fugao` (范围副词（都/也/只/还）) → 命中 68 条：hsk30-g1-012, hsk30-g1-016, hsk30-g1-019, hsk30-g1-060, hsk30-g1-061, hsk30-g2-015, hsk30-g2-016, hsk30-g2-020, hsk30-g2-029, hsk30-g2-042, hsk30-g2-044, hsk30-g2-056

## C. v1_4 中无考纲疑似命中的知识点（需人工补/判定超纲）
