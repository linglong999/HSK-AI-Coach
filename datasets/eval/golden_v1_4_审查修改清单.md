# golden_v1_4.json 审查修改清单（二语教师仲裁稿）

> 审查人：二语教学视角仲裁
> 日期：2026-08-23
> 对象：`golden_v1_4.json`（32 种子 + 4 对抗 = 36 条）+ `README.md`
> 结论摘要：**36 条中 4 条为疑似假阳性/过度纠偏（P0，须改判或移集），2 条语用金标缺语境（P1），1 条含术语硬错误（P1），另有 span 约定、correction 粒度、severity 校准等系统性问题。**
> README 统计口径（语法13/词汇3/语用2/汉字1/干净13；等级 8/11/9/4；对抗 11.1%）经程序核验**全部属实，无需改动**。

---

## P0 · 标注错误（假阳性 / 过度纠偏）——会毒化评测，最优先

| # | item_id | 原句 | 问题 | 修改建议 |
|---|---------|------|------|----------|
| 1 | HSK3-ERR-019 | 他把我的手机丢了。 | **非偏误**。"把+NP+V+了"是合法把字句（"我把钥匙丢了"为母语者常句）。且与对抗集 #35「我把饭吃了」（判为 clean）**自相矛盾**——同构句式一个判错一个判对。explanation"'丢'单独不成立"不成立。 | 改判 clean（或移入对抗集作误报测试）；`golden_status→disputed` |
| 2 | HSK4-ERR-016 | 请你把这个问题认真考虑一下。 | **非偏误**。"把+NP+状语+V+一下"完全合法（把房间打扫一下）。标注者自述"宜改一般宾语序**更自然**"——是风格偏好，不是错误。 | 改判 clean 或删除；`golden_status→disputed` |
| 3 | HSK4-ERR-014 | 我被老师表扬了，很高兴。 | **非偏误**。话题链中后句主语承前省略是合法汉语语法（"他考上了大学，特别高兴"同构），"导致歧义"不成立——默认解读即话题"我"。 | 改判 clean；`golden_status→disputed` |
| 4 | HSK3-ERR-032 | 他对环境很关心。 | **存疑偏误**。"对+NP+很+关心"为合法介宾状语（"他对这件事很关心"母语者常用）。"关心直接带宾语、不用对…关心"说法过度。 | 与第二位标注者复核；至少 `golden_status→disputed`，建议移出金集或改判 clean |

> 这 4 条若不改，评测会**惩罚"不误改正确句"的引擎**，直接歪曲 overcorrection 指标——与 README 用对抗样本测 overcorrection 的设计目标冲突。

## P1 · 语用类金标缺语境 —— 判定不确定

| # | item_id | 原句 | 问题 | 修改建议 |
|---|---------|------|------|----------|
| 5 | HSK2-ERR-031 | 你几岁？ | 缺听话人语境：若问**儿童**则原句完全正确，此时"纠正"反而是 overcorrection。explanation"不礼貌"过重，实为"语体不当/对成人显得轻慢"。且多数 HSK 教材把"你几岁？"当标准句式教。 | ① schema 增加 `context` 字段（如"对成年陌生人"）；② "不礼貌"改"语体不当/对成人不得体"；③ severity 2→1；④ 补语境前 `golden_status→disputed` |
| 6 | HSK1-ERR-003 | 你喝水吧？ | 若表**提议**（劝对方喝水）则原句正确；金标隐含"是非疑问意图"未声明。另 error_type=语用 偏拔高：吧/吗混淆更宜归"语法-语气词误用"（语用通常指社会得体性）。 | ① 补 `context`（"询问对方是否喝水"）；② error_type 语用→语法（或保留语用但必须有语境）；③ severity 2→1 |

## P1 · 术语 / 字段硬错误

| # | 位置 | 现状 | 问题 | 修改建议 |
|---|------|------|------|----------|
| 7 | HSK2-ERR-008 | sub_type=`compliment_structure`；explanation 含"复述结构""主谓补语结构" | `compliment` 是 **complement（补语）拼写错误**；"复述"（retell）应为"**述补**"；本句是状态/评述补语结构，"主谓补语结构""程度结构"表述均不准 | sub_type→`complement_structure`；explanation 改为"评述补语（V得C）结构错位：自评能力应用'中文说得（不）是怎么样'"；severity 3→2 |
| 8 | #19/#23/#32 的 error_span | "把…丢了"（U+2026）vs "是...的""对...很关心"（3×U+002E） | 三个非连续 span **都不是 original 子串**，且省略号字符不统一——span-level F1 无法直接匹配 | README 定义非连续 span 书写约定（统一用"…"），并在评测脚本侧做归一化 |
| 9 | 多条 correction 字段 | 多数为 span 替换串（一只猫、比我高），但 #19"他弄丢了我的手机"、#23"她是老师"、#36"我喝水了"为**整句级** | 粒度不一，下游按 span 计算时会错位 | 统一约定：correction=span 替换串；如需整句参考解，另设 `full_correction` 字段（corrected 已承担此角色，亦可直接弃用整句级 correction） |

## P2 · 分类与严重度校准

| # | item_id | 问题 | 修改建议 |
|---|---------|------|----------|
| 10 | HSK2-ERR-030（我爸爸开汽车很大） | error_type=词汇 不妥：缺"的"是虚词缺失/句法问题；sub_type=`pos_error` 名不副实 | error_type→语法；sub_type→`attributive_de_missing`（explanation 本身写得对，保留） |
| 11 | #6 / #8 | severity=3/high，但两句可懂度极高、零沟通障碍 | severity 3→2，impact→medium |
| 12 | HSK3-ERR-012（虽然他很累，但是他去上班） | 一个 span 内含**两个修正点**（加"还是"＋加"了"），conjunction 与 aspect 归因混淆，污染 type-level recall | 拆为两条，或 sub_type 改为 `aspect_error`（"还是"属语势加强，可归并说明） |
| 13 | 全表 sub_type | 拼音（liangci_error、shi_de_misuse）与英文混用；clean 有 clean_sentence/clean_de/clean_pragmatics/clean_adversarial 四种写法 | 定受控词表，统一英文 snake_case（如 `measure_word_error`、`shi_de_emphasis_misuse`）；clean 统一为 `clean`＋用 note 说明对照点 |

## P2 · 数据质量小项

| # | 位置 | 问题 | 修改建议 |
|---|------|------|----------|
| 14 | HSK1-ERR-002 explanation | "形容词'很多'作定语"——"很多"不是形容词，是数量短语/不定量词 | 改"数量短语'很多'作定语应前置" |
| 15 | HSK1-ERR-023（她是的老师） | 偏误形态不自然，更像错字；真实学习者语料中罕见 | 可保留，建议换更典型的"是…的"误用例（如"我是昨天来了"） |
| 16 | HSK1-ADV-004（我喝水了水） | 重复型"偏误"更像口误/转写错误，非典型学习者偏误 | 对抗价值可保留，建议 explanation 注明"模拟冗余/录入型噪声" |
| 17 | HSK3-ERR-009（因为下雨，所以我带了伞） | "因为…所以…"连用常被引擎误判——它其实是绝佳的误报测试句 | 建议从普通 clean 移入对抗集（clean_adversarial） |

## 结构性 / 流程性问题

| # | 问题 | 修改建议 |
|---|------|----------|
| 18 | **golden_status 形同虚设**：36 条全部 reviewed，README 却自述 reviewed="我建议可过，非最终仲裁"——字段语义与 README 冲突，且 reviewed/pending_review/disputed 三态从未实际使用 | 本次仲裁后：P0 四条 + #31 + #3 改 `disputed`；schema 增加 `arbitrator`/`arbitration_date` 字段；README 明确 reviewed 的定义 |
| 19 | schema 无 `context` 字段，语用类（#3、#31）判定无法复现 | error_type=语用 时 `context` 必填 |
| 20 | README 数据结构节称 error_type 枚举=词汇/语法/语用/汉字，但未声明 clean 样本为 null | README 补一句"干净样本 error_type=null" |
| 21 | 汉字类仅 1 条（#26），虽有正例对照 #27，class-level 指标仍不可算 | 扩充优先序：汉字 > 词汇 > 语用（README 已提，此处确认优先级） |

---

## 修改优先级一览

1. **立即改**（影响指标有效性）：P0 四条改判/移集；#31、#3 补语境或改 disputed；#8 术语修正
2. **下一版改**：span 省略号约定、correction 粒度统一、severity 校准（#6/#8）、#30 重分类、#12 拆条
3. **流程固化**：golden_status 三态真正启用 + context 字段 + sub_type 受控词表

> 附记：本次审查所用程序化校验（span 子串检查、correction 粒度检查、README 统计核对）可直接固化为 CI 脚本，每版数据集提交前自动跑。
