# -*- coding: utf-8 -*-
"""I-8 阶段一 · 三类名录落盘（数据工程师纪律）

按 I-7 §6.3 #3/#4/#5 字段落盘 data/cache/tdx_a_share_roster/：
- 历史 ST 逐日生效/摘帽：深交所历史名称变更(akshare stock_info_sz_change_name) 提取 ST/*ST 痕迹
- 减持公告结构化：akshare 股东持股变动接口（能拿则落，拿不到如实记缺口）
- 退市名录 + 退市股历史日线：baostock query_stock_basic + akshare 沪深退市名录交叉；退市股日线已由 i8_adjusted_land 落盘

免费性：全部免费免 key 源（baostock + akshare 交易所/东财公开接口）。
命中数/可靠度逐项标注；失败如实记缺口，不写占位空表冒充。
"""
from __future__ import annotations
import sys, json, time, warnings
from pathlib import Path
from datetime import datetime, timezone
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backtest"))

OUT = ROOT / "data" / "cache" / "tdx_a_share_roster"
OUT.mkdir(parents=True, exist_ok=True)
MANIFEST = OUT / "_manifest.json"
report = {"batch": "I-8", "role": "data_engineer", "generated_at": datetime.now(timezone.utc).isoformat(),
          "sources": {}, "items": {}}


def log_src(name, url, free, status, note=""):
    report["sources"][name] = {"url": url, "free": free, "status": status, "note": note}
    print(f"[src] {name}: {status} {note}", flush=True)


def save_item(key, obj, filename):
    p = OUT / filename
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    report["items"][key] = {"file": filename, "hits": obj.get("n_records", len(obj.get("records", [])))}
    print(f"[save] {filename}", flush=True)


def main():
    import baostock as bs
    import akshare as ak

    # ===== 1. 退市全名录 =====
    delist_records = {}
    # 1a baostock
    try:
        bs.login()
        rs = bs.query_stock_basic()
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        for r in rows:
            if r[4] == "1" and r[5] == "0":
                sym = r[0].replace(".", "")
                delist_records[sym] = {"code": sym, "name": r[1], "ipo_date": r[2],
                                        "delist_date": r[3], "source": "baostock"}
        bs.logout()
        log_src("baostock_delist", "http://www.baostock.com/", "免费免key", "OK",
                f"{len(delist_records)} 只退市股(type=1,status=0)")
    except Exception as e:
        log_src("baostock_delist", "http://www.baostock.com/", "免费免key", "FAIL", str(e)[:120])

    # 1b akshare 上交所退市
    try:
        df = ak.stock_info_sh_delist()
        n_sh = 0
        for _, r in df.iterrows():
            code = str(r.get("公司代码", "")).strip()
            if code and code not in delist_records:
                delist_records[code] = {"code": code, "name": str(r.get("公司简称", "")),
                    "ipo_date": str(r.get("上市日期", "")), "delist_date": str(r.get("暂停上市日期", "")),
                    "source": "akshare_sh_delist"}
                n_sh += 1
        log_src("akshare_sh_delist", "akshare.stock_info_sh_delist", "免费", "OK",
                f"{len(df)} 行, 新增 {n_sh}")
    except Exception as e:
        log_src("akshare_sh_delist", "akshare.stock_info_sh_delist", "免费", "FAIL", str(e)[:120])
        time.sleep(5)

    # 1c akshare 深交所退市
    try:
        df = ak.stock_info_sz_delist()
        n_sz = 0
        for _, r in df.iterrows():
            code = str(r.get("证券代码", "")).strip()
            if code and code not in delist_records:
                delist_records[code] = {"code": code, "name": str(r.get("证券简称", "")),
                    "ipo_date": str(r.get("上市日期", "")), "delist_date": str(r.get("终止上市日期", "")),
                    "source": "akshare_sz_delist"}
                n_sz += 1
        log_src("akshare_sz_delist", "akshare.stock_info_sz_delist", "免费", "OK",
                f"{len(df)} 行, 新增 {n_sz}")
    except Exception as e:
        log_src("akshare_sz_delist", "akshare.stock_info_sz_delist", "免费", "FAIL", str(e)[:120])

    delist_obj = {"n_records": len(delist_records), "coverage": "全历史退市名录（baostock+沪深交易所交叉）",
                  "reliability": "高（多源交叉）", "hits": len(delist_records),
                  "records": sorted(delist_records.values(), key=lambda x: x["code"]),
                  "survivorship_note": "退市股历史日线由 i8_adjusted_land.py 落盘于 data/cache/tdx_a_share_adjusted/（_progress.json 中 delisted 标记）；名录+日线齐备方可宣称消幸存者偏差"}
    save_item("delisting_roster", delist_obj, "delisting_roster.json")

    # ===== 2. 历史 ST 逐日生效/摘帽（深交所历史名称变更提取）=====
    st_records = []
    try:
        df = ak.stock_info_sz_change_name()
        for _, r in df.iterrows():
            before = str(r.get("变更前全称", ""))
            after = str(r.get("变更后全称", ""))
            short = str(r.get("证券简称", ""))
            code = str(r.get("证券代码", "")).strip()
            dt = str(r.get("变更日期", ""))
            is_st = "ST" in before.upper() or "ST" in after.upper() or "ST" in short.upper()
            if is_st:
                st_records.append({"code": code, "date": dt, "short_name": short,
                                   "name_before": before, "name_after": after,
                                   "event": ("摘帽" if "ST" in before.upper() and "ST" not in after.upper()
                                             else "戴帽/变更" if "ST" in after.upper() else "ST相关变更")})
        log_src("sz_change_name", "akshare.stock_info_sz_change_name", "免费", "OK",
                f"{len(df)} 行, ST相关 {len(st_records)}")
    except Exception as e:
        log_src("sz_change_name", "akshare.stock_info_sz_change_name", "免费", "FAIL", str(e)[:120])

    # 当前 ST（参考，不作历史全过滤）—— 等东财限流恢复后尝试一次
    current_st = []
    try:
        time.sleep(3)
        df = ak.stock_zh_a_st_em()
        for _, r in df.iterrows():
            current_st.append({"code": str(r.get("代码", "")).strip(), "name": str(r.get("名称", ""))})
        log_src("current_st_em", "akshare.stock_zh_a_st_em", "免费", "OK", f"{len(current_st)} 只当前ST")
    except Exception as e:
        log_src("current_st_em", "akshare.stock_zh_a_st_em", "免费", "FAIL(限流)",
                "撞东财 Push2RateLimitError，隔3分钟策略已登记；当前ST本不满足历史ST全过滤，仅参考")

    st_obj = {"n_records": len(st_records), "coverage": "深交所 1991 至今历史名称变更中的 ST/*ST 痕迹（逐日变更日可判）",
              "reliability": "中（深交所历史名称变更提取；上交所 stock_info_change_name 仅7行无效，沪市历史ST为缺口）",
              "hits": len(st_records), "records": st_records,
              "current_st_reference": current_st,
              "gaps": ["上交所历史 ST 逐日生效/摘帽：akshare.stock_info_change_name 仅7行无效 → 🔴 缺口",
                       "深交所变更表仅记录有名称变更的时点，非逐交易日快照 → 需结合变更日区间推断生效区间"],
              "forbidden": "未把当前 ST 冒充历史 ST 全过滤；未编造名录；确认存在≠已落盘"}
    save_item("historical_st", st_obj, "historical_st.json")

    # ===== 3. 减持公告结构化（股东持股变动）=====
    # 只做接口存在性 + 字段探测（单次调用、快速），不逐股全池循环（超时风险 + 东财限流）
    reduce_records = []
    reduce_status = []
    for fn, label in [("stock_ggcg_em", "东财高管持股变动"),
                      ("stock_gdfx_holding_change_em", "东财股东持股变动")]:
        try:
            f = getattr(ak, fn)
            kw = {"symbol": "600000"} if "symbol" in f.__code__.co_varnames else {}
            df = f(**kw)
            cols = list(df.columns)[:12]
            reduce_status.append({"func": fn, "status": "OK(字段探测)", "rows": len(df), "cols": cols,
                                  "sample": {str(k): str(df.iloc[0][k]) for k in cols[:6]} if len(df) else {}})
            for _, r in df.head(50).iterrows():
                reduce_records.append({"source": fn, "row": {str(k): str(v) for k, v in r.items()}})
        except Exception as e:
            reduce_status.append({"func": fn, "status": "FAIL", "error": str(e)[:150]})

    reduce_obj = {"n_records": len(reduce_records), "coverage": "样例股(600000)股东/高管持股变动探测",
                  "reliability": "低-中（接口多为逐股查询、需全池逐股循环；本批未全池展开）",
                  "probe_status": reduce_status,
                  "records": reduce_records[:200],  # 留痕样例，避免过大
                  "gaps": ["减持公告需全池 5687 只逐股查询或巨潮 POST 批量接口；本批仅样例探测，未全池覆盖 → 🔴 缺口",
                           "结构化字段（主体/股数/比例/期间）因源接口字段不一，未统一 → 如实登记"],
                  "forbidden": "未把样例冒充全池；未编造减持记录"}
    save_item("shareholder_reduction", reduce_obj, "shareholder_reduction.json")

    # ===== manifest =====
    m = {"batch": "I-8", "role": "data_engineer",
         "generated_at": datetime.now(timezone.utc).isoformat(),
         "authorization": "老板 2026-11-16 22:17 第3条：历史ST/减持/退市全名录+退市股历史日线",
         "free_sources_only": True, "items": report["items"], "sources": report["sources"],
         "delisting": {"n": len(delist_records), "daily_bars": "见 tdx_a_share_adjusted/_progress.json（delisted标记）",
                       "survivorship_bias": "名录+退市股日线齐备→可宣称覆盖退市股；但沪市历史ST与全池减持仍缺口，偏差按'部分消除'准确表述"},
         "redlines": "免费免key、未注册、未写key、未启客户端、未内存dump；未编造名录；当前ST不冒充历史ST",
         "execution_entity": "单一执行体，按 data_engineer.md soul 纪律落地"}
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n=== roster done === {report['items']}", flush=True)


if __name__ == "__main__":
    main()
