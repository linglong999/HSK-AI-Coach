# datasets/ocr_corrections

OCR 校正数据目录（P0.7）。RAG 索引重建时会自动喂入校正语料（`ingest_corrections`），
检索层零改动，实现"喂更好的语料，不改检索"。本目录缺失时静默跳过，不影响运行。

## 约定

- 本目录可放一个或多个 `.json` 文件；每个文件为**对象或对象数组**。
- 每条记录字段（中英均可，按需填）：
  - `原文` / `original`：OCR 识别出的原文
  - `校正` / `corrected`：人工校正后的标准文本（**必填**，缺失则该条跳过）
  - `kp_id`：关联的考纲语法点 id（可选）
  - `level`：HSK 等级，如 `HSK3`（可选）
- 采集流程：跑只读扫描脚本 `tools/ocr_corrections_scan.py`（若已生成），
  将 `_grammar_appendix_ocr.md` 的候选条目导出为待核清单 `ocr_corrections_inbox.json`；
  人工逐条核验后移入本目录 → 重跑 `build_hsk_index()` 即生效。

## 状态

已有真实实测数据 `ocr_corrections_real.json`（68 条，RapidOCR 逐字扫 HSK30 认读字表），
由 `tools/ocr_corrections_scan.py` 采集；重跑 `build_hsk_index()` 即摄入生效。