"""M9 验收脚本（phase3 M9 验收标准 1-3）

M9-a 参数校验 CI（关键收尾，与 M8 绑定）：
  1. 参数被改未留痕时 CI 阻断回测
  2. 参数已 --update 留痕时放行回测
M9-b 前端看板（非关键路径）：
  3. 看板正确渲染核心指标（净值/价差/z曲线/持仓/告警）

以计算结果为准、不瞎猜：每条验收都真实执行验证，不通过就如实报 ❌。
"""

import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings
warnings.filterwarnings("ignore")

from src.backtest.param_gate import check_params, ensure_params_for_backtest

PASS_CNT = 0


def _mark(name: str, ok: bool, detail: str = "") -> None:
    global PASS_CNT
    mark = "✅" if ok else "❌"
    if ok:
        PASS_CNT += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail else ""))


# ============================================================================
# M9-a 参数校验 CI
# ============================================================================

def check_param_gate_blocks_unaudited() -> None:
    """验收 1：参数被改且未留痕 → 门禁阻断（抛异常）。

    手法：在临时副本上改一个参数主体字段（不动 audit 段）→ 指纹与所有
    已记录校验和都不符 → ensure_params_for_backtest 必须抛 RuntimeError。
    不污染真实 config 文件。
    """
    import config.verify_params as vp

    real_path = vp.PARAMS_FILE
    tmp = tempfile.NamedTemporaryFile("r+", delete=False, suffix=".yaml")
    shutil.copyfile(real_path, tmp.name)
    tmp.close()

    # 指向临时副本改参数主体（signal.entry_sigma: 3.0 → 3.5），不 --update
    vp.PARAMS_FILE = Path(tmp.name)
    try:
        text = Path(tmp.name).read_text(encoding="utf-8")
        text = text.replace("entry_sigma: 3.0", "entry_sigma: 3.5")
        Path(tmp.name).write_text(text, encoding="utf-8")
        res = check_params()
        blocked = not res.ok
        try:
            ensure_params_for_backtest()
            gate_raised = False
        except RuntimeError:
            gate_raised = True
        _mark("M9-a 验收1 改参数未留痕→阻断",
              blocked and gate_raised,
              f"actual={res.actual_fp[:12]}… reason={res.reason}")
    finally:
        vp.PARAMS_FILE = real_path
        Path(tmp.name).unlink(missing_ok=True)


def check_param_gate_allows_audited() -> None:
    """验收 2：参数已 --update 留痕 → 放行（不抛异常 + 指纹命中）。

    手法：在临时副本上改参数主体 + 跑 update() 追加 audit 校验和 →
    指纹已进 recorded → ensure 通过。验证"正式变更流程留痕后 CI 放行"。
    """
    import config.verify_params as vp

    real_path = vp.PARAMS_FILE
    tmp = tempfile.NamedTemporaryFile("r+", delete=False, suffix=".yaml")
    shutil.copyfile(real_path, tmp.name)
    tmp.close()

    vp.PARAMS_FILE = Path(tmp.name)
    try:
        text = Path(tmp.name).read_text(encoding="utf-8")
        text = text.replace("entry_sigma: 3.0", "entry_sigma: 3.5")
        Path(tmp.name).write_text(text, encoding="utf-8")
        vp.update()  # 追加 audit 校验和（正式变更流程留痕）
        res = check_params()
        allowed = res.ok
        try:
            ensure_params_for_backtest()
            gate_passed = True
        except RuntimeError:
            gate_passed = False
        _mark("M9-a 验收2 改参数已留痕→放行",
              allowed and gate_passed,
              f"latest_fp={vp._recorded_checksums()[-1][:12]}…")
    finally:
        vp.PARAMS_FILE = real_path
        Path(tmp.name).unlink(missing_ok=True)


def check_backtest_wires_param_gate() -> None:
    """验收补充：Backtester.run 已内置参数门禁（改参数未留痕时 run 抛异常）。

    手法：临时把 config 指向改参数未留痕的副本 → Backtester.run() 应在
    门禁处抛 RuntimeError（不进入 9841 bar 主循环），证明"CI 阻断回测"
    真实发生在回测入口而非旁路。
    """
    import config.verify_params as vp
    from src.backtest import Backtester, BacktestConfig

    real_path = vp.PARAMS_FILE
    tmp = tempfile.NamedTemporaryFile("r+", delete=False, suffix=".yaml")
    shutil.copyfile(real_path, tmp.name)
    tmp.close()

    vp.PARAMS_FILE = Path(tmp.name)
    try:
        text = Path(tmp.name).read_text(encoding="utf-8")
        text = text.replace("exit_sigma: 0.5", "exit_sigma: 9.9")
        Path(tmp.name).write_text(text, encoding="utf-8")  # 改主体不留痕
        bt = Backtester(BacktestConfig(seed=42))
        try:
            bt.run()
            raised = False
        except RuntimeError as e:
            raised = "参数门禁" in str(e) or "M9-a" in str(e)
        _mark("M9-a 验收·回测入口内置门禁", raised)
    finally:
        vp.PARAMS_FILE = real_path
        Path(tmp.name).unlink(missing_ok=True)


# ============================================================================
# M9-b 前端看板（非关键路径）
# ============================================================================

def check_dashboard_renders_metrics() -> None:
    """验收 3：看板渲染 M8 输出的核心指标（净值/价差/z/持仓/告警）。

    手法：dashboard/render_dashboard.py 读 golden_value.json + M8 逐 bar
    序列，产出 dashboard/index.html，校验 HTML 含各指标区块且可被浏览器
    解析（非空 + 关键锚点齐全）。非关键路径，验收仅"能渲染"，不阻塞。
    """
    from dashboard.render_dashboard import render_dashboard, out_path

    html = render_dashboard()
    required_anchors = [
        "净值", "价差", "z", "持仓", "告警", "M8", "ETH_USDT", "SOL_USDT",
    ]
    missing = [a for a in required_anchors if a not in html]
    size_ok = len(html) > 2000
    p = out_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    _mark("M9-b 验收3 看板渲染核心指标", (not missing) and size_ok,
          f"缺失锚点={missing} html_size={len(html)}")


# ============================================================================
# 主入口
# ============================================================================

def main() -> int:
    print("=" * 70)
    print("M9 验收 · 参数校验 CI（M9-a 关键）+ 前端看板（M9-b 非关键）")
    print("=" * 70)

    print("\n[M9-a] 参数校验 CI")
    check_param_gate_blocks_unaudited()
    check_param_gate_allows_audited()
    check_backtest_wires_param_gate()

    print("\n[M9-b] 前端看板（非关键路径）")
    check_dashboard_renders_metrics()

    total = 4
    print("\n" + "=" * 70)
    print(f"M9 验收结论: {'✅ 全绿' if PASS_CNT == total else f'❌ {PASS_CNT}/{total}'}")
    print("=" * 70)
    return 0 if PASS_CNT == total else 1


if __name__ == "__main__":
    sys.exit(main())
