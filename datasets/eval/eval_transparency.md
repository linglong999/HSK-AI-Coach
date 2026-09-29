# 识别评测 · 三集拆分指标与置信区间（透明度报告）

> 历史结果，更正于 2026-09-29：数据为 `datasets/eval/eval_results.json`（version `golden_v1_4_v0.2`）的 36 条旧版模型输出，不是当前 `golden_v1_4_v0.3` 的复跑结果。旧文使用 percentile bootstrap，导致 0/17、0/2、2/2 等边界结果出现不合理的零宽度区间；下表与细项已改用 Wilson score 95% 区间。自建/定向构造语料并非随机抽样，此区间不能用于总体泛化或学习增益宣称。

| 集 | 样本数 | 命中率 recall | 类型识别率 type_acc | 误报/过纠率 |
|---|:--:|:--:|:--:|:--:|
| 种子偏误集 | 15 | 0.93 [0.70, 0.99] (n=15) | 0.93 [0.69, 0.99] (n=14) | — |
| 干净对照集 | 17 | — | — | 0.00 [0.00, 0.18] (n=17) |
| 对抗·偏误项 | — | 1.00 [0.34, 1.00] (n=2) | — | — |
| 对抗·应干净项(overcorrection) | — | — | — | 0.00 [0.00, 0.66] (n=2) |

## 对照：原整体口径（F1 混池，非主结论）

- 精确率 1.000 · 召回率 0.941 · F1 0.970

> 说明：混池 F1 会把「干净误报」与「对抗过纠」折进检测分，掩盖分集差异。这里的区间是旧版数据的描述性不确定性展示；标签仍为作者自审，且现版金标已变更，不得当作当前性能门槛。

## 逐集细项（与 engine/eval_metrics.py 同口径）

```json
{
  "error": {
    "subset": "error",
    "n": 15,
    "recall": {
      "value": 0.9333,
      "n": 15,
      "ci": [
        0.7018,
        0.9881
      ],
      "method": "Wilson score 95%"
    },
    "type_acc": {
      "value": 0.9286,
      "n": 14,
      "ci": [
        0.6853,
        0.9873
      ],
      "method": "Wilson score 95%"
    }
  },
  "clean": {
    "subset": "clean",
    "n": 17,
    "clean_fp_rate": {
      "value": 0.0,
      "n": 17,
      "ci": [
        0.0,
        0.1843
      ],
      "method": "Wilson score 95%"
    }
  },
  "adversarial": {
    "n": 4,
    "error_ratio": 0.5,
    "overcorrection_rate": {
      "value": 0.0,
      "n": 2,
      "ci": [
        0.0,
        0.6576
      ],
      "method": "Wilson score 95%"
    },
    "recall_on_error": {
      "value": 1.0,
      "n": 2,
      "ci": [
        0.3424,
        1.0
      ],
      "method": "Wilson score 95%"
    }
  }
}
```
