"""M9-b · 前端看板（非关键路径）。

渲染 M8 回测输出的核心指标：净值 / 价差 / z 曲线 / 持仓 / 告警。
设计依据：agents/phase3-project-plan.md M9-b（看板展示核心指标，非关键路径）。
依赖 M8 产出（backtest/output/series.json + golden_value.json）；缺失字段容错，
非关键路径不阻断主流程。
"""
