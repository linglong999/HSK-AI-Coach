# 首批教师仲裁队列（待标注）

> 2026-09-29 冻结源：仓库提交 `070e6dd8a5e65e2de763c66f2ffd5a45991b8be0`。队列由现有字段筛出，既不构成教师判定，也不自动更改源金标。执行方法见 [`teacher_arbitration_protocol.md`](teacher_arbitration_protocol.md)。

| 来源 | 源文件 SHA-256 | 筛选规则 | 数量 | 当前处理 |
|---|---|---|---:|---|
| 识别金标 | `145626A0596DB9C434AE8DB9EC2E4608606513A1F63D4C86D0A2F06C3E804E94` | `golden_v1_4.json` 的 `seed_golden + adversarial` 中 `golden_status=disputed` | 1 | 优先核对删除型冗余的可判性与跨度 |
| 复述验证 | `9452A7F5DC18F1B5EFEBAA8EB2F5FCD195777D2D4091DE1A8570A2CCB19A292B` | `retell_verification.json` 的 `items` 中 `need_arbitration=true` | 26 | 优先核对等价解与空洞/部分覆盖边界 |

首批识别 ID：`HSK1-ADV-004`。

首批复述 ID：`HSK1-ERR-001:partial`、`HSK1-ERR-001:hollow`、`HSK1-ERR-002:partial`、`HSK1-ERR-002:hollow`、`HSK1-ERR-003:partial`、`HSK1-ERR-003:hollow`、`HSK2-ERR-005:partial`、`HSK2-ERR-005:hollow`、`HSK2-ERR-006:hollow`、`HSK2-ERR-008:hollow`、`HSK1-ERR-023:hollow`、`HSK2-ERR-025:hollow`、`HSK2-ERR-030:hollow`、`HSK2-ERR-031:hollow`、`MUCGEC-5:partial`、`MUCGEC-5:hollow`、`MUCGEC-4:partial`、`MUCGEC-4:hollow`、`MUCGEC-29:partial`、`MUCGEC-29:hollow`、`MUCGEC-84:partial`、`MUCGEC-84:hollow`、`MUCGEC-90:partial`、`MUCGEC-90:hollow`、`MUCGEC-147:partial`、`MUCGEC-147:hollow`。

推荐先审 `HSK1-ADV-004`、`HSK2-ERR-025:hollow`、`MUCGEC-29:partial` 和 `MUCGEC-29:hollow`，因为现有数据说明已记录边界争议。其余 35 条 `reviewed` 识别样本也仍是作者自审，后续分层双人标注必须覆盖，不能因未列入此“争议优先队列”就视为教师认可。
