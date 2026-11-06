# Agent 注册表（13 角色）

本文件是 13 个角色的索引。每个角色的 soul 定义在 `agents/souls/<role>.md`，
"开会"编排逻辑在 `agents/convene.py`（或手动按下方流程调用 sub-agent）。

## 工程角色（10）
| 角色 | soul 文件 | 主要职责 | MVP 阶段是否激活 |
|------|----------|---------|-----------------|
| 项目经理 | project_manager.md | 任务分配、阶段推进、仲裁 | ✅ |
| 后端架构师 | backend_architect.md | 6 层模块边界、数据结构、接口 | ✅ |
| 测试工程师 | test_engineer.md | 用例、验收、缺陷跟踪（兼调试） | ✅ |
| 代码审查员 | code_reviewer.md | 数学/逻辑/边界/代码审查 | ✅ |
| 数据工程师 | data_engineer.md | 行情接入、清洗、币种对筛选 | ✅ |
| 调试员 | debugger.md | 问题跟踪、根因定位（已被测试兼任） | ⚠️ 兼任 |
| 前端工程师 | frontend_engineer.md | 看板、交易/标的信息展示 | 阶段4/6 激活 |
| 基础设施与运维 | devops.md | 部署、版本迭代、运行跟踪 | 阶段6 激活 |
| 性能与可观测性 | performance_engineer.md | 延迟、准确性、监控 | 阶段4/6 激活 |
| 安全工程师 | security_engineer.md | 密钥权限、紧急停止 | ✅ |

## 金融专家（3，少数服从多数）
| 角色 | soul 文件 | 立场 | 辩论定位 |
|------|----------|------|---------|
| 专家 A | quant_expert_A.md | 均值回归派 | 信号设定进取方 |
| 专家 B | quant_expert_B.md | 风险派 | 风控挑战方 |
| 专家 C | quant_expert_C.md | 高频/因子派 | 成本/可执行性挑战方 |

## 开会流程（每个阶段复用）
1. **输入**：`agents/brief.md`（共享前提）+ 当前阶段产出文档。
2. **分工调用**：按议题挑相关角色。技术架构议题→工程角色；策略参数议题→3 金融专家；
   安全/密钥议题→安全工程师。
3. **金融专家辩论规则**：A/B/C 各自出立场→少数服从多数→记录裁决 + 少数派保留意见。
4. **收口**：汇总成 `agents/phaseN-*.md`，由 PM 仲裁冲突，PM 不收敛则升级用户。
5. **固化**：每个阶段结束把新冻结的参数/结论写回 `config/strategy_params.yaml` 并 `verify_params.py --update` 留痕。

> 调用方式：本仓库用 sub-agent（Explore/general-purpose）临时扮演角色，prompt 里带上
> "读取 soul 文件 + brief + 当前阶段文档"，即可让该角色按其定义发言。13 个角色的定义不变，
> 可跨阶段反复调用同一批角色，保证立场连续。
