# ============================================================
# engine/generation/authoring.py
# 生成单元 v1 · authoring prompt 契约（Archify P1）
# 借鉴 Archify：authoring-contract 与 schema 分离但互指，字段口径完全一致。
#   - schema 本体：datasets/docs/0.7-生成单元schema-v1.md（权威）
#   - 本文件即"给生成引擎的 authoring prompt"，只产 prompt 常量，不含生成/校验逻辑。
# 对齐 schema v1 的字段：keyPoints 只装正向知识、forbidden_errors 允许空但不可缺省、
#   previousSpeech 只注入最近一口、curriculumAt 只读、difficulty 由服务层注入(不信任生成侧)。
# ============================================================

# ---------------- 公共护栏说明（两单元共用） ----------------
_COMMON_RULES = """【通用约束】
1. keyPoints：只装"该懂的正向目标知识"（如"量词'只'用于个体量词"）；不得把"纠错指令/错误特征(不能X/应替换)"塞进 keyPoints。
2. forbidden_errors：生成前的前置护栏，框死本回合"不该犯的偏误"；允许空数组（合法句场景），但字段必须存在，缺省即护栏失效。
3. context.previousSpeech：只用于锚定上一回合末尾"对话衔接"，不得承载任何纠错结论；未提供则不写。
4. context.curriculumAt：只读图谱位置引用，绝不参与写回、不得改动其内容。
5. self_language：非 zh 时必须在 context.languageDirective 给本单元目标语言说明。
6. 三个后置字段（dialogueSpec/interactiveSpec/pblSpec）：一律写 null，绝不生成其内容。
7. type 必须是下方【本单元要求】中给定的枚举值之一，禁止自由造词。
8. title 一句话以内、短；不得与 context.allTitles 重复（防图谱节点重名）。
9. 所有 string 字段不含前后多余空白；数组一律用字符串；JSON 严格合法（无注释、无尾逗号）。"""

# ---------------- explain 单元 authoring prompt ----------------
EXPLAIN_AUTHORING_PROMPT = """【任务】作为 HSK 中文教学专家，为"响应式诊断型教学"生成一个 explain（费曼讲解片段）生成单元 —— 这是对话回合里"对症当下这一口"的小片段，不是整节课。

【上下文槽】
- 目标知识点：{for_keypoint}（可空 → 给通用讲解）
- 教学目标：{teaching_objective}（一句话，驱动讲解不跑题；与 for_keypoint 若同时给出，必须一致）
- 学习者/图谱当前位置：{curriculum_at}（只读）
- 上一回合末尾（按需，可空）：{previous_speech}
- 本场已产出标题：{all_titles}
- 语言指令：{language_directive}（可空，默认中文）
- 联网参考资料（可选，仅供事实补充，不得照搬原文）：{reference_sources}

【本单元要求】type 取 "explain"，输出字段口径必须与 schema v1 完全一致：
{{
  "id": "本场内唯一（e.g. u-3）",
  "type": "explain",
  "title": "一句话标题（≤20字，不与 all_titles 重复）",
  "keyPoints": ["只装正向目标知识的条目标（≥1条）"],
  "forbidden_errors": ["本回合不该犯的偏误护栏（可为 []，但字段必须存在）"],
  "context": {{
    "previousSpeech": "上一回合末尾（若上下文给了才写，否则省略）",
    "allTitles": "逐项抄录上下文给的 all_titles",
    "languageDirective": "逐项抄录（若给了）",
    "curriculumAt": "逐字抄录上下文给的 curriculum_at"
  }},
  "self_language": "zh 或上下文指定的语言",
  "dialogueSpec": null,
  "interactiveSpec": null,
  "pblSpec": null,
  "for_keypoint": "目标知识点（与上下文一致，可空则写 null）",
  "teachingObjective": "一句话教学目标",
  "estimatedDuration": 90
}}
{constraints}"""

# ---------------- practice 单元 authoring prompt ----------------
PRACTICE_AUTHORING_PROMPT = """【任务】作为 HSK 中文教学专家，为"响应式诊断型教学"生成一个 practice（练习，接纠偏链）生成单元 —— 这是对话回合里针对"这一批偏误"的巩固练习。

【上下文槽】
- 关联知识点：{for_keypoints}（数组，可多知识点）
- 目标偏误（写回图谱依据）：{targets_errors}（可空 → 只巩固、不写回）
- 任务类型：{task_kind}（枚举：fill|mcq|rephrase|correct|open-ended）
- 学习者/图谱当前位置：{curriculum_at}（只读）
- 上一回合末尾（按需，可空）：{previous_speech}
- 本场已产出标题：{all_titles}
- 语言指令：{language_directive}（可空，默认中文）

【本单元要求】type 取 "practice"，输出字段口径必须与 schema v1 完全一致：
{{
  "id": "本场内唯一（e.g. u-4）",
  "type": "practice",
  "title": "一句话标题（≤20字，不与 all_titles 重复）",
  "keyPoints": ["只装正向目标知识的条目标（≥1条）"],
  "forbidden_errors": ["本回合不该犯的偏误护栏（可为 []，但字段必须存在）"],
  "context": {{
    "previousSpeech": "上一回合末尾（若上下文给了才写，否则省略）",
    "allTitles": "逐项抄录上下文给的 all_titles",
    "languageDirective": "逐项抄录（若给了）",
    "curriculumAt": "逐字抄录上下文给的 curriculum_at"
  }},
  "self_language": "zh 或上下文指定的语言",
  "dialogueSpec": null,
  "interactiveSpec": null,
  "pblSpec": null,
  "for_keypoints": "逐项抄录上下文给的 for_keypoints",
  "targets_errors": "逐项抄录上下文给的 targets_errors（可为 []，但字段必须存在）",
  "task_kind": "fill|mcq|rephrase|correct|open-ended 之一，取自上下文",
  "questionCount": 3,
  "difficulty": "留空由服务层按图谱掌握度注入（本单元不写死）"
}}
{constraints}"""

# 困难度不信任生成侧自报（0.7 §7.4）：实践上 difficulty 由服务层覆盖，
# 故 prompt 明确"留空由服务层注入"，避免难度虚标。
_DIFFICULTY_NOTE = "（difficulty 由服务层按当前图谱掌握度定，不信任生成侧自报——生成侧直接省略该字段或写 null）"


def build_explain_prompt(*, for_keypoint: str = "",
                         teaching_objective: str = "",
                         curriculum_at: str = "",
                         previous_speech: str = "",
                         all_titles=None,
                         language_directive: str = "",
                         self_language: str = "zh",
                         reference_sources: str = "") -> str:
    """组装 explain 单元的 authoring prompt（填入槽位 + 公共护栏）。
    reference_sources：联网检索的参考资料（可选；空则槽位显"（无）"）。"""
    if all_titles is None:
        all_titles = []
    if not for_keypoint and not teaching_objective:
        teaching_objective = "讲清目标知识点，用费曼式举例让学习者真正理解"
    return EXPLAIN_AUTHORING_PROMPT.format(
        for_keypoint=for_keypoint or "（空）",
        teaching_objective=teaching_objective,
        curriculum_at=curriculum_at or "（空）",
        previous_speech=previous_speech or "（空）",
        all_titles=", ".join(all_titles) or "（空）",
        language_directive=language_directive or "（默认中文）",
        reference_sources=reference_sources or "（无）",
        constraints=_COMMON_RULES,
    )


def build_practice_prompt(*, for_keypoints=None, targets_errors=None,
                          task_kind: str = "mcq", curriculum_at: str = "",
                          previous_speech: str = "", all_titles=None,
                          language_directive: str = "") -> str:
    """组装 practice 单元的 authoring prompt（填入槽位 + 公共护栏）。"""
    if for_keypoints is None:
        for_keypoints = []
    if targets_errors is None:
        targets_errors = []
    if all_titles is None:
        all_titles = []
    if task_kind not in ("fill", "mcq", "rephrase", "correct", "open-ended"):
        task_kind = "mcq"
    return PRACTICE_AUTHORING_PROMPT.format(
        for_keypoints=", ".join(for_keypoints) or "（空）",
        targets_errors=", ".join(targets_errors) or "（[]）",
        task_kind=task_kind,
        curriculum_at=curriculum_at or "（空）",
        previous_speech=previous_speech or "（空）",
        all_titles=", ".join(all_titles) or "（空）",
        language_directive=language_directive or "（默认中文）",
        constraints=_COMMON_RULES + "\n" + _DIFFICULTY_NOTE,
    )


# ---------------- dialogue 单元 authoring prompt ----------------
# 0.22 方向2 · D2.2 复用生成引擎扩写：把预置场景扩成更丰满的场景 brief
# （更多回合提示、目标表达、可替换词块），供动态扩展预置库/直注入主链。
# 输出是"扩写的场景 brief"（结构化 JSON，非 schema-v1 完整单元），
# 故不套 _COMMON_RULES 的 dialogueSpec 后置约束——本类型不产 schema-v1 单元。
DIALOGUE_AUTHORING_PROMPT = """【任务】作为 HSK 中文教学专家，把给定的中文学习场景扩写成一份"场景对话 brief"，用于引导学习者在该场景里开口习得中文。

【上下文槽】
- 场景 id：{scene_id}
- 目标语言指令：{language_directive}（可空，默认中文）
- 学习者 HSK 等级范围：{level}
- 本场景锚定知识点：{kp_ids}
- 原场景 seed（作为扩写基底）：{seed}

【要求】扩写要让学习者自然开口且覆盖锚定的知识点，输出严格 JSON：
{{
  "scene_id": "沿用上下文 scene_id（或 scene_id + '-ext'）",
  "type": "dialogue",
  "title": "一句英文标题（面向英语母语者，≤40 字符）",
  "title_zh": "一句中文标题（≤12 字）",
  "level": [1, 2],
  "kp_ids": ["逐项抄录上下文给的锚定知识点 id"],
  "seed": "开场情境（中文，比上下文更具体：地点/人物/要完成的事）",
  "turns": ["至少 3 条回合提示（中文，一步步推进对话，每条约一个话题/动作）"],
  "expressions": ["至少 3 条目标表达（中文短语/句型，学习者应在本场景用到的）"]
}}
{constraints}"""


_DIALOGUE_CONSTRAINTS = """【约束】
1. seed、turns、expressions 一律中文（内容载体），title 英文、title_zh 中文。
2. turns 不少于 3 条，按"开口→展开→收尾"渐进推进，聚焦本场景锚定知识点。
3. expressions 是本场景"学习者应说出的正面表达"，不是纠错清单。
4. 场景目标符合该 HSK 等级范围（不教超纲表达）。
5. JSON 严格合法（无注释、无尾逗号）；数组和字符串字段正确。"""


def build_dialogue_prompt(*, scene_id: str = "", level=None, kp_ids=None,
                          seed: str = "", language_directive: str = "") -> str:
    """组装 dialogue 场景扩写 prompt（填入槽位 + 专属约束）。"""
    if level is None:
        level = []
    if kp_ids is None:
        kp_ids = []
    return DIALOGUE_AUTHORING_PROMPT.format(
        scene_id=scene_id or "（空）",
        language_directive=language_directive or "（默认中文）",
        level="、".join(str(l) for l in (level or [])) or "（空）",
        kp_ids="、".join(kp_ids) or "（空）",
        seed=seed or "（空）",
        constraints=_DIALOGUE_CONSTRAINTS,
    )


# ---------------- why 原理 prompt（0.26 · 隐性"为什么"折叠块） ----------------
# 与生成单元不同：why 不是 schema-v1 单元，而是一条"逐错误对应的口语化原理"，
# 前端折叠显示、默认不打扰场景节奏。不套 _COMMON_RULES（无 keyPoints 等字段）。
WHY_CONSTRAINTS = """【约束】
1. items 逐条对应输入的错误（fragment/correction 逐字照抄，不改动；条数一致，不增删、不合并）。
2. reason 用一到两句口语化的原理解释"为什么错、为什么这样改"，就事论事、不堆术语、不写整体建议；按【语言指令】用目标语言写（例句片段保留中文原文）。
3. fragment/correction 始终保留中文原文，绝不翻译。
4. l1 字段：仅当该错误的成因与输入里的某条母语迁移假设相符、且置信度高时才写（一两句母语影响说明）；绝大多数情况省略该字段，不硬凑。
5. 切不可把"为什么"写成对学习者的指责或长篇语法讲义；默认是折叠内容，要读起来轻盈、点开即懂。"""

WHY_AUTHORING_PROMPT = """【任务】作为 HSK 中文教学专家，为学习者写错的那一小段中文写"为什么"——一段简短、口语化的原理：解释"为什么那样讲不对、为什么改成这样就对了"。这条内容会折叠展示，学习者主动点开才看，所以绝不要打扰或指责。

【语言指令】{language_directive}（可空，默认中文解释；非中文时 reason 用目标语言，但 fragment/correction 保持中文原文）

【识别到的偏误（fragment=错片段，correction=正确说法）】
{errors}

【母语迁移假设（可选，l1 字段仅在相符且高置信时使用）】
{l1_hypotheses}

【要求】输出严格 JSON object（结构逐字段照抄）：
{{
  "items": [
    {{
      "fragment": "逐字照抄输入的错误片段",
      "correction": "逐字照抄输入的修正",
      "reason": "一两句口语化的为什么（解释机制，用语言指令的语言）",
      "l1": "仅当该错与母语迁移假设相符且高置信时的母语影响说明，否则省略本字段"
    }}
  ]
}}
{constraints}"""

WHY_FIELDS = ("fragment", "correction", "reason", "l1")


def _fmt_errors(errors) -> str:
    """把识别到的偏误压缩为 prompt 行（逐字照抄 fragment/correction 进槽位）。"""
    rows = []
    for e in (errors or []):
        if not isinstance(e, dict):
            continue
        frag = str(e.get("fragment", "") or "")
        corr = str(e.get("correction", "") or "")
        typ = str(e.get("type", "") or "")
        conf = e.get("confidence")
        tag = f"（{typ}·置信{conf}）" if (conf is not None) else (f"（{typ}）" if typ else "")
        rows.append(f"- 错误片段【{frag}】→ 正确【{corr}】{tag}")
    return "\n".join(rows) or "（无）"


def _fmt_l1(l1_hypotheses) -> str:
    """把母语迁移假设压缩为 prompt 段（仅作 l1 字段候选，非绑定）。"""
    rows = []
    for h in (l1_hypotheses or []):
        if not isinstance(h, dict):
            continue
        anchor = str(h.get("l1_anchor", "") or "")
        corr = str(h.get("correction", "") or "")
        if corr:
            rows.append(f"- {anchor} ← 可能受此母语影响（对应修正：{corr}）")
        elif anchor:
            rows.append(f"- {anchor}")
    return "\n".join(rows) or "（无）"


def build_why_prompt(*, errors=None, l1_hypotheses=None,
                     language_directive: str = "") -> str:
    """组装 why 原理解释 prompt（填槽位 + 专属约束）。errors 为空 → 直接返回空（不生成）。"""
    if not errors:
        return ""
    return WHY_AUTHORING_PROMPT.format(
        language_directive=language_directive or "(默认中文解释，例句保留中文)",
        errors=_fmt_errors(errors),
        l1_hypotheses=_fmt_l1(l1_hypotheses),
        constraints=WHY_CONSTRAINTS,
    )


DIALOGUE_FIELDS = ("scene_id", "type", "title", "title_zh", "level",
                   "kp_ids", "seed", "turns", "expressions")


# ---------------- 契约字段口径清单（供校验器/测试引用，与 schema v1 一致） ----------------
# 用于确保 authoring 与 schema 口径同步；CRITICAL 字段不可缺省。
UNIT_BASE_FIELDS = ("id", "type", "title", "keyPoints", "forbidden_errors", "context")
EXPLAIN_FIELDS = UNIT_BASE_FIELDS + ("for_keypoint", "teachingObjective", "estimatedDuration")
PRACTICE_FIELDS = UNIT_BASE_FIELDS + (
    "for_keypoints", "targets_errors", "task_kind", "questionCount", "difficulty")
POSTPONED_FIELDS = ("dialogueSpec", "interactiveSpec", "pblSpec")  # 一律 null
SCENE_TYPES = ("explain", "practice", "dialogue", "interactive", "pbl")
PRACTICE_TASK_KINDS = ("fill", "mcq", "rephrase", "correct", "open-ended")