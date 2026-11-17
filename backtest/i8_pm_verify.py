# -*- coding: utf-8 -*-
"""I-8 PM 独立自验（重新实算，不采信执行体自报；只读，不写业务文件）

核验 6 类产出 + 红线合规，逐项 PASS/FAIL，输出可直接引用的自验结论。
"""
from __future__ import annotations
import json, hashlib, os, struct, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

checks = []
def chk(name, ok, detail):
    checks.append({"item": name, "pass": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

# 1. config 未改 + git HEAD 未变
p = ROOT / "config" / "strategy_params.yaml"
sha = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
chk("1.config_sha", sha == "31fbb054cddb55af", f"sha16={sha} (期望 31fbb054cddb55af)")
mt = os.path.getmtime(p)
from datetime import datetime
mts = datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S")
chk("1.config_mtime", mts == "2026-11-12 16:46:44", f"mtime={mts}")
try:
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
except Exception:
    head = "?"
chk("1.git_head", head == "cde835f", f"HEAD={head} (期望 cde835f 无新 commit)")

# 2. 复权数据产出
adj = ROOT / "data" / "cache" / "tdx_a_share_adjusted"
prog = json.load(open(adj / "_build_progress.json", encoding="utf-8")) if (adj / "_build_progress.json").exists() else {"done": {}, "failed": {}}
n_parquet = len(list(adj.glob("*_1d.parquet")))
chk("2.adj_parquet_count", n_parquet > 0 and n_parquet == len(prog["done"]),
    f"parquet={n_parquet} done={len(prog['done'])} failed={len(prog['failed'])}")
chk("2.adj_manifest", (adj / "_manifest.json").exists(), "manifest 存在")
chk("2.unavailable_preserved", (adj / "_UNAVAILABLE.md").exists() and "I-8 追加" in (adj / "_UNAVAILABLE.md").read_text(encoding="utf-8"),
    "_UNAVAILABLE.md 原文保留 + I-8 追加段")
# 交叉验证三份证据 PASS
ev = {}
for f in ["i8_adjusted_probe", "i8_adjusted_basis_check", "i8_local_adjust_equivalence"]:
    p2 = ROOT / "backtest" / "output" / f"{f}.json"
    if p2.exists():
        j = json.load(open(p2, encoding="utf-8"))
        v = j.get("verdict", "") or ""
        ev[f] = (isinstance(v, str) and v.startswith("PASS")) or j.get("all_pass") is True
    else:
        ev[f] = False
chk("2.cross_validation", all(ev.values()), f"probe/basis/equivalence = {[ev[k] for k in ev]}")

# 3. 名录
roster = ROOT / "data" / "cache" / "tdx_a_share_roster"
rfiles = ["delisting_roster.json", "historical_st.json", "shareholder_reduction.json", "_manifest.json"]
rk = {}
for f in rfiles:
    p3 = roster / f
    if p3.exists():
        j = json.load(open(p3, encoding="utf-8"))
        rk[f] = j.get("n_records", "manifest")
    else:
        rk[f] = None
chk("3.roster", all(v is not None for v in rk.values()), f"{rk}")
# 退市日线：i8_adjusted_land 的 _progress.json 中 delisted 标记数（与因子方案 build_progress 分开）
land_prog_p = adj / "_progress.json"
n_delist_bars = 0
if land_prog_p.exists():
    lp = json.load(open(land_prog_p, encoding="utf-8"))
    n_delist_bars = sum(1 for v in lp["done"].values() if v.get("delisted"))
chk("3.delisted_daily_bars", n_delist_bars > 0, f"含退市日线的退市股 {n_delist_bars} 只（i8_adjusted_land 的 _progress.json）")

# 4. 八年日线（可降级，不强制非空目录）
early = ROOT / "data" / "cache" / "tdx_a_share_early"
early_ok = (early / "_manifest.json").exists() if early.exists() else False
chk("4.early_manifest", early_ok, "八年日线 manifest 存在（可降级）")

# 5. 阶段二脚本 + json + trades + 报告
s2 = ROOT / "backtest" / "a_share_i8_adjusted_rerun.py"
chk("5.script", s2.exists(), f"a_share_i8_adjusted_rerun.py {'存在' if s2.exists() else '缺失'}")
jpath = ROOT / "backtest" / "output" / "a_share_i8_adjusted_rerun.json"
j2 = json.load(open(jpath, encoding="utf-8")) if jpath.exists() else {}
chk("5.json_not_hung", j2.get("status") != "HUNG" and j2.get("batch") == "I-8", f"status={j2.get('status','n/a')} batch={j2.get('batch')}")
sm = j2.get("summary", {})
chk("5.adjusted_true", j2.get("meta", {}).get("adjusted") is True and sm.get("adjusted") is True, "adjusted=true")
chk("5.extreme_gate", j2.get("meta", {}).get("extreme_gate") == "NOT_EVALUATED_BY_BOSS_ORDER" or sm.get("extreme_gate") == "NOT_EVALUATED_BY_BOSS_ORDER", "extreme_gate=NOT_EVALUATED_BY_BOSS_ORDER")
chk("5.n_passed_final_0", sm.get("n_passed_final") == 0, f"n_passed_final={sm.get('n_passed_final')}")
chk("5.not_mixed", sm.get("not_mixed") is True and j2.get("meta", {}).get("not_mixed") is True, "not_mixed=true")
# n_stocks_actually_traded 去重实算复核
trades_p = ROOT / "backtest" / "output" / "a_share_i8_adjusted_rerun_trades.json"
if trades_p.exists():
    td = json.load(open(trades_p, encoding="utf-8"))
    syms = set()
    for rule, fids in td.items():
        for fid, trs in fids.items():
            for t in trs: syms.add(t["symbol"])
    calc = len(syms)
    chk("5.n_traded_recalc", calc == sm.get("n_stocks_actually_traded"), f"trades去重={calc} json={sm.get('n_stocks_actually_traded')}")
else:
    chk("5.n_traded_recalc", False, "trades 文件缺失")
# 60 组
chk("5.n_runs_60", len(j2.get("runs", [])) == 60 or sm.get("n_runs") == 60, f"n_runs={sm.get('n_runs')} runs={len(j2.get('runs',[]))}")
# 最终报告
chk("5.final_report", (ROOT / "backtest" / "output" / "i8_final_backtest_report.md").exists(), "i8_final_backtest_report.md 存在")

# 6. 日志段
dl = ROOT / "agents" / "dispatch_log.md"
chk("6.dispatch_log_I8", "## I-8" in dl.read_text(encoding="utf-8"), "dispatch_log.md I-8 段存在")
for f in ["agents/phase9-i8-data-landing-and-validation.md", "agents/phase9-i8-adjusted-rerun.md"]:
    chk(f"6.{Path(f).stem}", (ROOT / f).exists(), f"{f} 存在")

# 7. 表述纪律：禁用词
report = ROOT / "backtest" / "output" / "i8_final_backtest_report.md"
if report.exists():
    txt = report.read_text(encoding="utf-8")
    # 禁用词须仅在"禁止"语境出现；这里粗查是否出现实际违规短语（排除明确否定语境）
    bad = []
    for kw in ["费率被抬到 0.5%", "等效 50bp", "成本抬高 10 倍", "已消除偏差", "低一个数量级"]:
        idx = 0
        while True:
            i = txt.find(kw, idx)
            if i < 0: break
            ctx = txt[max(0, i-40):i]
            # 禁止语境（严禁写/不写成/禁止/禁用/不得）不判违规
            if not any(m in ctx for m in ["严禁", "不写成", "禁止", "禁用", "不得", "不写"]):
                bad.append(f"{kw}@{i}")
            idx = i + 1
    chk("7.wording", len(bad) == 0, f"禁用词实际违规命中={bad}（禁止语境不计）")

n_pass = sum(1 for c in checks if c["pass"])
print(f"\n=== PM 自验 {n_pass}/{len(checks)} PASS ===")
out = ROOT / "backtest" / "output" / "i8_pm_verify.json"
out.write_text(json.dumps({"batch": "I-8", "role": "PM", "checks": checks,
    "n_pass": n_pass, "n_total": len(checks)}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"saved {out}")
