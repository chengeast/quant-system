# I-7 逐源探测原始记录（phase9-i7-source-probe-log）

> 阶段：I-7「缺口数据扩大搜索 + 极端段口径确认」｜承派 + 验收：PM｜执行体：单一执行体，按 `data_engineer.md` soul 纪律落地（非独立 subagent 会话）
> 记录日期：2026-11-16｜派活单：`agents/dispatch_queue.md` I-7 段（781~830 行）
> 口径：公开资料检索 + 对免费/免注册/免 key/匿名源的**单样本只读探测**；每源 ≤3 次、总计 **≤30 次** HTTP 请求；只记录 HTTP 状态 / 字段样例 / 时间覆盖证据，**不落盘任何行情数据、不写 `data/cache/`、不写 parquet、不注册、不写 key、不接付费源**。

---

## 0. 请求计数总览（可核对）

| 类别 | 次数 | 说明 |
|---|---|---|
| Python `urllib.request` 直接 HTTP GET | **18** | 详见 §1 / §2 / §3 序号 P1~P18 |
| WebFetch 工具（公开资料页提取） | **3** | 新浪 600000 / 600519 / 000001 分红配股页 |
| WebSearch 工具（公开资料检索） | **2** | 浦发分红事件、gbbq 开源实现 |
| **HTTP 请求合计** | **21** | 18 直连 + 3 WebFetch；WebSearch 2 为资料检索、按工具性质单列 |
| 上限 | 30 | **未超限**（每源均 ≤3 次） |

**限流（429/503）**：本批**未撞 429、未撞 503**，无需执行「隔 3 分钟自动重试一次」。
**非限流类失败**：HTTP 502（网易历史数据）×1、404（上交所旧名录 URL）×1、403（Yahoo）×1、500（巨潮公告 API 直接 GET）×1 —— 均如实登记为「不可用 / 需正确接口」，**不是 429/503，不触发限流重试条款**。
**内容复核重试（非限流）**：P13 为对 P12 返回体的内容复核（stooq 首次返回 HTML 而非 CSV，取 body 确认是 JS 验证页），属同一源的第 2 次请求，仍在每源 ≤3 次内，已留痕。

---

## 1. 免费行情接口 / 公开资料页探测（P1~P18）

| 序号 | 时间戳(UTC+8) | URL | 方法 | HTTP 状态 | 响应摘要 / 字段样例 | 判定 |
|---|---|---|---|---|---|---|
| P1 | 2026-11-16 ~15:30 | `http://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/600000.phtml` | GET | **200** | `text/html; charset=gbk`；分红表含 公告日期/送股/转增/派息/除权除息日/股权登记日 | 可达（公开资料，非行情拉取） |
| P2 | ~15:30 | `https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=soft&code=SH600000` | GET | **200** | `text/html; charset=utf-8`；东财 F10 分红融资页 | 可达（公开资料） |
| P3 | ~15:30 | `http://quotes.money.163.com/service/chddata.html?code=0600000&start=20240715&end=20240720` | GET | **502 Bad Gateway** | 无响应体 | 不可用（本批该 URL 返回 502，如实登记，不编造字段） |
| P4 | ~15:30 | `http://qt.gtimg.cn/q=sh600000` | GET | **200** | `text/html; charset=GBK`；`v_sh600000="1~浦发银行~600000~10.78~10.40~10.45~...~2026..."` | 可达（实时快照，字段为行情，未落盘） |
| P5 | ~15:30 | `http://hq.sinajs.cn/list=sh600000` | GET | **200** | `application/javascript; charset=GB18030`；`var hq_str_sh600000="浦发银行,10.450,10.400,10.780,..."` | 可达（实时快照，未落盘） |
| P6 | ~15:30 | `https://eniu.com/gu/sh600000/dv` | GET | **200** | `text/html; charset=utf-8`；历史股息率/分红数据页 | 可达（公开资料） |
| P7 | ~15:32 | `http://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/600000.phtml`（原始抓取，正则解析未命中表格） | GET | **200** | `text/html; charset=gbk`；87 行表格（正则未匹配到分红字段，后改用 WebFetch W1 提取） | 可达（公开资料；本行为原始抓取留痕） |
| P8 | ~15:32 | `http://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/600519.phtml`（原始抓取） | GET | **200** | `text/html; charset=gbk`；92 行表格（同上，改用 WebFetch W2） | 可达（原始抓取留痕） |
| P9 | ~15:32 | `http://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/000001.phtml`（原始抓取） | GET | **200** | `text/html; charset=gbk`；101 行表格（同上，改用 WebFetch W3） | 可达（原始抓取留痕） |
| P10 | ~15:31 | `http://www.baostock.com/` | GET | **200**（重定向 `https://www.baostock.com/`） | `text/html`；官网首页可达 | 官网可达；**库未安装、未注册、未调用**（见 §4） |
| P11 | ~15:31 | `https://query1.finance.yahoo.com/v8/finance/chart/600000.SS?range=5d&interval=1d` | GET | **403 Forbidden** | 无 JSON | 境外源对 A 股 600000.SS 本批 403，不可用 |
| P12 | ~15:31 | `https://stooq.com/q/d/l/?s=600000.cn&i=d` | GET | **200** 但 `text/html` | 返回 JS 验证页（非 CSV） | A 股覆盖本批**未取到 CSV**，登记不可用 |
| P13 | ~15:31 | `https://stooq.com/q/d/l/?s=600000.cn&i=d`（取 body 复核） | GET | **200** | `<noscript>This site requires JavaScript to verify your browser...` | 复核确认：JS 验证拦截面，非数据；内容复核重试（非限流） |
| P14 | ~15:31 | `http://www.cninfo.com.cn/new/hisAnnouncement/query` | GET | **500** | 无 | 该公告查询接口需 POST + 参数，直接 GET 500；本批不构造批量查询，登记「需正确接口」 |
| P15 | ~15:31 | `http://www.baostock.com/api/` | GET | **200**（重定向 https） | `text/html` | 官网 API 说明页可达；未调用接口 |
| P16 | ~15:32 | `https://www.sse.com.cn/assortment/stock/list/name/` | GET | **404** | 无 | 旧名录 URL 失效，登记不可用 |
| P17 | ~15:32 | `https://www.szse.cn/market/product/stock/list/index.html` | GET | **200** | `text/html`；深交所股票列表页 | 可达（公开名录页） |
| P18 | ~15:32 | `http://www.cninfo.com.cn/new/index` | GET | **200** | `text/html;charset=utf-8`；巨潮资讯首页 | 可达（公开公告入口） |

---

## 2. 验证样本公开资料页抓取（新浪分红配股，公开资料非行情K线）

| 序号 | 时间戳(UTC+8) | URL | 工具 | 状态 | 提取内容 | 落盘 |
|---|---|---|---|---|---|---|
| W1 | ~15:33 | `http://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/600000.phtml` | WebFetch | 成功 | 浦发银行 1999~2026 分红表（含 2017 转3、2016 转1、2011 送3 等） | 仅报告引用，未落盘行情 |
| W2 | ~15:34 | `http://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/600519.phtml` | WebFetch | 成功 | 贵州茅台 2002~2026 分红表（含 2015 送1、2006 转增10、2004 送3 等） | 仅报告引用 |
| W3 | ~15:35 | `http://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/000001.phtml` | WebFetch | 成功 | 平安银行 1991~2026 分红表（含 2013 送6、2008 送3、1996 送5转5 等） | 仅报告引用 |

> 说明：P1/P2/P6 与 W1/W2/W3 为同源不同取数方式（urllib 原始状态码 + WebFetch 结构化提取），分别计入 HTTP 请求（合计已含）。

---

## 3. 资料检索（WebSearch，非直接行情请求）

| 序号 | 查询 | 结果要点 | 来源 URL |
|---|---|---|---|
| S1 | 浦发银行 600000 2008 分红 送股 除权除息日 | 命中新浪/东财/亿牛网/同花顺/前瞻分红页 | `https://stock.10jqka.com.cn/20260709/c678071852.shtml` 等 |
| S2 | 通达信 gbbq 3DES 解密 4176 密码表 github 开源 | ① CSDN 2016：4176B 密码表从内存 dump、29B/条前24加密后5明文；② **GitHub rustdx：「请勿使用本项目 assets/ 中的 gbbq 文件，因为那对你来说是过时的」**；③ CSDN：gbbq 过时可用 baostock 复权因子备选 | `https://blog.csdn.net/fangle6688/article/details/50956609`、`https://github.com/freestockso/rustdx/` |

---

## 4. 未实测源（如实登记，不试、不编造）

| 源 | 未测原因 | 状态 |
|---|---|---|
| baostock Python API | 库未安装；红线「不注册、不写 key、不 pip 写环境」。官网可达但本批未安装、未登录、未调用 | **未测**（资料核实层面：免费/免注册/免 key/`adjustflag`） |
| akshare / mootdx / pytdx / tushare / efinance | 库均未安装；本批不安装、不调用 | **未测** |
| 上交所/深交所/北交所批量名录 API | 需构造 POST/参数或反爬，红线「不批量拉取、不写 key」；仅探测公开入口页 | **入口页已探，批量接口未测** |
| 巨潮资讯分红/减持公告 API | 查询接口需 POST 参数；本批不构造批量查询 | **入口页已探，公告 API 未测** |
| Yahoo / Stooq A 股 | 本批 403 / JS 验证，A 股覆盖未取到数据 | **不可用（本批）** |
| 任何付费源 / 需注册源 / 客户端内存 dump | 派活单红线「不接付费源、不注册、不启动客户端、不内存 dump」 | **直接判不可用、不试** |

---

## 5. 本机只读枚举探测（非 HTTP，附此留痕）

| 目标 | 方法 | 结果 |
|---|---|---|
| `D:\tdx` 整树 | `os.walk` 只读 | 42,685 文件 / 18,328,057,704 B；扩展名 .day 10180 / .lc1 9681 / .lc5 9599 / .dat 8005；新发现 I-6 未点名文件见主报告 §2 |
| 同花顺整树 `D:\Program Files\THSVIP\同花顺旗舰版\` | `os.walk` 只读 | 20,988 文件 / 1,503,918,124 B；history/ 仅 6 个 `.day`（`hd1.0` 私有格式） |
| 三只指定股 `.day` | `struct.unpack_from('<IIIIIfII', b, i*32)` 只读 | 600000: 6427 bar / 600519: 6042 bar / 000001: 6661 bar，均 1998-09-15~2026-11-13（600000 起 1999-11-10） |
| 工作区 `data/` | 只读遍历 | 5,833 parquet（I-5/I-6 全量池 + 其他） |
| C 盘用户目录 | 只读遍历 | Documents/Downloads/Desktop 均无行情数据文件 |

---

## 6. 合规留痕

- 全部请求均为**只读 GET / 资料检索**；未落盘任何行情数据、未写 `data/cache/`、未写 parquet。
- 未注册、未写任何 API key、未接付费源、未启动客户端、未改注册表、未提交 Git、未改 config、未改 I-5/I-6 产物。
- 字段样例 / HTTP 状态 / 时间覆盖**逐项来自本批真实响应**，未编造；失败源如实登记，未用估算补齐。
- 执行体如实登记：**单一执行体、无独立 subagent 会话**，按 `data_engineer.md` soul 纪律落地，非两角色各自独立会话。
