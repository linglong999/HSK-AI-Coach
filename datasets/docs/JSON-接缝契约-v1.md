# HSK-AI-Coach · 后端 JSON 接缝契约 v1

- **版本**：v1（2026-08-28）
- **活着**：这是前端壳（M10）与后端的**唯一数据契约**。前端只消费本 doc 定义的结构，不得依赖任何未列出的字段。
- **层级**：`Router.process()` 是**模式一**（纠错闭环）主入口；`Router.verify_rephrase()` 是**复述验证**入口。两者输出均为**纯可序列化 dict**（`json.dumps` 直接可用，无 dataclass / 无 Python 对象）。
- **兼容**：契约 v1 与 2.1/2.2/2.3/2.4 已签核 schema 对齐；任何引擎内部字段若不在本表即视为内部实现，前端不可依赖。

---

## 顶部结构（Router.process 返回值）

```jsonc
{
  "contract_version": "v1",
  "learner_id": "string",
  "user_level": "string",           // "HSK1".."HSK6"
  "native_lang": "string",
  "input_text": "string",            // 本次输入原句
  "errors": [ /* 识别→讲解→图谱 → 已确认偏误列表，见 §1 */ ],
  "uncertain": [ /* 进入待确认队列的偏误，见 §1 */ ],
  "has_error": true,                 // bool：是否有任何 confirmed 偏误
  "graph_size": 42,                  // int：当前图谱节点数（len(graph)）
  "review_queue": [ /* 复习队列，见 §4 */ ],
  "degraded": [ /* 结构化降级记录，见 §5 */ ],
  "meta": { "start_ts": "...", "end_ts": "...", "elapsed_ms": 0 }
}
```

### §1. errors[] —— 一个"已确认偏误"的处理闭环

一个偏误经「识别 → 图谱写入 → 费曼讲解」三段的完整结果。**前端渲染一个"偏误卡"直接消费这一项。**

```jsonc
{
  "error": {
    "fragment": "苹果很多",           // 最小偏误片段（2.1 约束7 口径）
    "correction": "很多苹果",         // 修正建议
    "type": "语法",                  // 词汇|语法|语用|汉字
    "type_confident": true,          // 类型是否高置信
    "confidence": 0.95,              // 0-1 置信度初值
    "knowledge_point_id": "HSK1-xxxx" // 命中 HSK 清单（空=进待映射队列）
  },
  "explanation": { /* 见 §2 四段式讲解 */ },
  "graph_write": { /* 见 §3 图谱写入状态 */ },
  "verification": null               // 复述验证为独立二次输入，process() 内恒 null
}
```

### §2. explanation —— 费曼讲解（2.2 v0.3）

```jsonc
// 正常：
{
  "explanation": "四段式正文：①指出错误 ②解释原因 ③给出正确句 ④类比/举例",
  "key_points": [ {"id": "kp-1", "text": "要点1" }, {"id": "kp-2", "text": "要点2" } ],
  "keywords": ["关键词1", "关键词2"],
  "uncertain_note": "待确认提示（无则空字符串）",
  "free_generated": false
}
// 讲解引擎降级（4.1/4.2 兜底）：
{ "_degraded": true, "explanation": "降级回退文本", "key_points": [] }
```

- `key_points` 是**复述验证唯一点来源**（2.3 对齐）。
- 四段式为 3.5-P1 固化模板：**必须含第③段正确句**；歧义句先点明按哪句读再给正确句。

### §3. graph_write —— 图谱写入状态（2.4 写接口）

```jsonc
// 节点 upsert（uncertain=false 且 kp 命中）：
{ "status": "node_upsert", "kp_id": "HSK1-xxxx" }
// 进入待确认队列：
{ "status": "to_queue" | "idempotent_skip", "item_key"?: "fragment|语法|kp" }
// 写失败（4.1 降级，不崩整体）：
{ "status": "write_failed:<异常摘要>" }
```

### §4. review_queue[] —— 复习队列（按 priority 降序）

```jsonc
{
  "kp_id": "HSK1-xxxx",
  "priority": 1.53,
  "node": {
    "id": "HSK1-xxxx",
    "knowledge_point": "量词'只'",
    "level": "HSK1",
    "error_types": { "量词": 3 },
    "error_count": 3,
    "mastery": 0.138,
    "last_learnt_at": "2026-08-27T08:00:00Z",  // 可为 null（首建未复习）
    "created_at": "2026-08-25T01:02:03Z"
  }
}
```

### §5. degraded[] —— 结构化降级记录（4.1/4.2）

```jsonc
[
  { "stage": "recognize", "reason": "消息摘要", "fatal": false, "detail?": "..." },
  { "stage": "explain",   "error_index": 0, "reason": "...", "fatal": false },
  { "stage": "graph_write"|"graph_save"|"confusion_edge"|"review_queue", "reason": "..." }
]
```

- `fatal: true` 仅识别阶段主失败（无任何偏误结果，链条提前返回——此时 `errors=[]`）。
- **识别层降级透传**：识别引擎自身降级（如 LLM 不可用走规则回退）也以 `{stage:"recognize", fatal:false}` 记录——此时 `errors=[]` 但 `uncertain` 可能有规则层候选，链条继续。
- 其余均为局部降级，**其余链路照常返回**。前端可据此展示"部分完成 + 局部降级"而**不整体报错**。

---

## 复述验证 · verify_rephrase 返回（2.3 v0.3）

```jsonc
{
  "verdict": "pass" | "partial" | "fail",
  "covered_points": 2,        // int
  "total_points": 3,          // int
  "coverage_ratio": 0.6667,   // 0-1 rounded 4
  "point_judgements": [ {"id": 1, "text": "要点文本", "is_covered": true, "evidence": "复述落实处" } ],
  "flowery_but_empty": false, // 对抗轴：流利但空洞
  "feedback": "string（学习者引导语）",
  "consecutive_fail": 1,      // 同一任务连续失败计树（按 kp 分键，跨任务不串）
  "action": null | "retry_simpler" | "suggest_teacher",  // N=2 降难度重讲 / N=4 建议找老师
  "write_status": { "status": "node_update" | "verdict_to_queue" | "..." },
  "degraded": false
}
```

---

## 正确句（无偏误）返回

`process()` 对正确 / 0-confirmed 输入：`errors=[]`、`has_error=false`、`uncertain` 可能有低置信候选（进待确认队列）、`review_queue` 仍返回当前队列。前端据此走「泛讲解/无错」分支。文件讲解（模式二）走 `file_mode.explain_selection`，其返回另见 file_mode 内注释（scope: M10 前端若要文档讲解分支再对齐）。

---

## 前端消费守则

1. **只读本契约字段**；未列字段=内部实现，禁止依赖。
2. `errors[i].explanation.key_points` 是验证复述的唯一点来源，前端展示复述框时直接回传。
3. `degraded[]` 非空 → 前端可展示"部分完成"黄条，但**不得把已成功部分隐藏**。
4. `contract_version` 用于升级判等；前端据此做向后兼容分支。