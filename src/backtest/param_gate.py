"""
M9-a · 参数校验 CI（回测门禁）

设计依据：
  - agents/phase3-project-plan.md M9-a（verify_params 接入 CI，回测前强制校验参数一致性）
  - config/verify_params.py（SHA256 指纹口径：排除 audit 段、保留 live_trading 段）
  - 验收标准 1：参数被改未留痕时 CI 阻断回测

原则（沿用用户"以计算结果为准、不瞎猜"）：
  不重新发明指纹算法 —— 直接 import config/verify_params.py 的
  _body_fingerprint()/_recorded_checksums()/verify()，
  保证 CI 与人工校验、脚本审计口径**完全一致**（单一事实源）。
  本模块只做"能不能进回测"的门禁判定，不写文件、不改参数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

# 复用人工/脚本校验的同一套指纹逻辑（避免 CI 与审计口径分叉）
from config import verify_params  # config/ 下有 __init__ 或可被 sys.path 解析


@dataclass
class ParamCheckResult:
    """参数校验结果（回测门禁 + CI 共用）。"""
    ok: bool
    actual_fp: str
    recorded_fps: List[str]
    reason: str

    def summary(self) -> str:
        status = "✅ 参数与冻结记录一致" if self.ok else "❌ 参数被改且未走审计流程"
        return (f"{status}\n  实际指纹：{self.actual_fp}\n"
                f"  记录指纹数：{len(self.recorded_fps)}")


def check_params() -> ParamCheckResult:
    """校验当前策略参数是否仍为经审计的冻结版。

    - ok=True：实际指纹命中任一已记录校验和（含 verify_params --update 追加的 audit）。
    - ok=False：实际指纹不在任何已记录校验和中 → 参数被改且未留痕 → 必须阻断回测。
    这是 M9-a 验收标准 1 的核心门禁。
    """
    actual = verify_params._body_fingerprint()
    recorded = verify_params._recorded_checksums()
    if not recorded:
        return ParamCheckResult(False, actual, recorded,
                                "尚无有效校验和（audit 段为空），无法确认冻结版")
    if actual in recorded:
        return ParamCheckResult(True, actual, recorded, "指纹命中已记录校验和")
    return ParamCheckResult(False, actual, recorded,
                            "实际指纹不在任何已记录校验和中 → 参数被改且未 --update 留痕")


def ensure_params_for_backtest() -> ParamCheckResult:
    """回测前强制门禁：校验失败即抛异常阻断回测（CI 阻断的落点）。

    Backtester.run() 调用它；CI 冒烟也直接调它。失败时异常信息即"阻断原因"。
    """
    res = check_params()
    if not res.ok:
        raise RuntimeError(
            "M9-a 参数门禁阻断回测：\n" + res.summary() +
            "\n处置：参数若经正式变更流程，请运行 "
            "`python config/verify_params.py --update` 追加 audit 留痕后重跑。"
        )
    return res


__all__ = ["ParamCheckResult", "check_params", "ensure_params_for_backtest"]
