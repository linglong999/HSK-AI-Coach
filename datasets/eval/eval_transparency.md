# 识别评测 · 三集拆分指标与置信区间（透明度报告）

> 数据：`datasets\eval\eval_results.json`（version golden_v1_4_v0.2），逐条 36 项，离线重算（不调用 LLM）。
> 方法：指标按三集拆分报告，不再与干净/对抗混成一个点估计；每个比例给 percentile bootstrap 95% CI。样本量小（<10 级），CI 偏宽属正常，应避免据此下强结论。

| 集 | 样本数 | 命中率 recall | 类型识别率 type_acc | 误报/过纠率 |
|---|:--:|:--:|:--:|:--:|
| 种子偏误集 | 15 | 0.93 [0.80, 1.00] (n=15) | 0.93 [0.79, 1.00] (n=14) | — |
| 干净对照集 | 17 | — | — | 0.00 [0.00, 0.00] (n=17) |
| 对抗·偏误项 | — | 1.00 [1.00, 1.00] (n=2) | — | — |
| 对抗·应干净项(overcorrection) | — | — | — | 0.00 [0.00, 0.00] (n=2) |

## 对照：原整体口径（F1 混池，非主结论）

- 精确率 1.000 · 召回率 0.941 · F1 0.970

> 说明：混池 F1 会把「干净误报」与「对抗过纠」折进检测分，掩盖分集差异；分集报告以消除该混叠。样本量小时 CI 偏宽属正常，勿据单一数字下强结论。

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
        0.8,
        1.0
      ],
      "n_boot": 2000,
      "method": "percentile bootstrap 95%"
    },
    "type_acc": {
      "value": 0.9286,
      "n": 14,
      "ci": [
        0.7857,
        1.0
      ],
      "n_boot": 2000,
      "method": "percentile bootstrap 95%"
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
        0.0
      ],
      "n_boot": 2000,
      "method": "percentile bootstrap 95%"
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
        0.0
      ],
      "n_boot": 2000,
      "method": "percentile bootstrap 95%"
    },
    "recall_on_error": {
      "value": 1.0,
      "n": 2,
      "ci": [
        1.0,
        1.0
      ],
      "n_boot": 2000,
      "method": "percentile bootstrap 95%"
    }
  }
}
```
