# F-2 运维规范 Git 配置 · 现状盘点 / 规范化 / 验证记录

> 老板 2026-11-10 插队指令「派运维规范配置，消除后续 push 隐患」；派活来源：秘书 `agents/dispatch_queue.md` 批次 F-2 段。
> 本批只做 F-2，不抢跑 F-1 专家复议、不跑回测、不改业务代码。

## 0. 元信息与落实方式（如实标注）

| 项 | 内容 |
|---|---|
| 批次 | F-2 |
| 接单日期 | 2026-11-10 |
| 承派对象 | 运维工程师 `agents/souls/devops.md`（主，职责「版本迭代、分支策略、部署流程」；限制「不修改业务代码，只保障运行环境与部署流程」）；调试员 `agents/souls/debugger.md`（备，未启用） |
| 验收 | PM（`agents/souls/project_manager.md`，职责 1 分派 / 职责 4 裁决；限制「不写实现代码，不替其他角色做专业判断」） |
| 工作目录 | `/c/Users/Administrator/WorkBuddy AI/2026-11-06-20-40-51`；`git rev-parse --show-toplevel` = `C:/Users/Administrator/WorkBuddy AI/2026-11-06-20-40-51`（`TOPLEVEL_EXIT=0`） |
| 环境 | `git --version` = `git version 2.55.0.windows.3`；`command -v timeout` = `/usr/bin/timeout`（`TIMEOUT_RC=0`） |

**落实方式（如实标注，沿用 E-2 处理方式）**：PM 以 `ToolSearch` 检索「启动独立子代理 subagent 委派任务 / agent 子代理 独立会话 dispatch」，仅返回 `mcp__space-library__library_search` 与 `domain_search` 两个资料检索工具，**本会话确无独立子代理委派通道**。故按派活单允许的兜底方式：由 PM 依 `devops.md` soul 组织落实，**未取得该角色独立原始回报**。但本批的**配置盘点、规范化、验证均真实执行了 git 命令**（原始输出见 §1 / §3 / §4），并非只写文档；所有事实以盘上命令输出为准，未编造任何输出。

---

## 1. 只读盘点（步骤 1，原始输出，均为改前）

### 1.1 派活单指定命令

命令：`git config --show-origin --get-regexp 'http\..*proxy|https\..*proxy|credential|remote\..*url'`

```
file:D:/Program Files/WorkBuddyAI/resources/vendor/PortableGit/etc/gitconfig	credential.helper manager
file:C:/Users/Administrator/.gitconfig	credential.git@snsoft.vip:8017/project/dept03/XiangYu/XiangYuExport.git.helper manager-core
file:C:/Users/Administrator/.gitconfig	credential.git@snsoft.vip:8017/project/dept03/XiangYu/XiangYuExport.git.username <脱敏>
file:C:/Users/Administrator/.gitconfig	credential.http://www.snsoft.vip:8017/project/dept03/XiangYu/XiangYuExport.git.helper manager-core
file:C:/Users/Administrator/.gitconfig	credential.http://www.snsoft.vip:8017/project/dept03/XiangYu/XiangYuExport.git.username <脱敏>
file:C:/Users/Administrator/.gitconfig	credential.http://www.snsoft.vip:8017.provider generic
file:C:/Users/Administrator/.gitconfig	credential.git@120.238.247.245:8017/project/dept03/XiangYu/XiangYuExport.git.helper manager-core
file:C:/Users/Administrator/.gitconfig	credential.git@120.238.247.245:8017/project/dept03/XiangYu/XiangYuExport.git.username <脱敏>
file:C:/Users/Administrator/.gitconfig	credential.http://120.238.247.245:8017/project/dept03/XiangYu/XiangYuExport.git.helper manager-core
file:C:/Users/Administrator/.gitconfig	credential.http://120.238.247.245:8017/project/dept03/XiangYu/XiangYuExport.git.username <脱敏>
file:C:/Users/Administrator/.gitconfig	credential.http://120.238.247.245:8017.provider generic
file:C:/Users/Administrator/.gitconfig	http.proxy socks5://127.0.0.1:10808
file:C:/Users/Administrator/.gitconfig	https.proxy socks5://127.0.0.1:10808
file:.git/config	remote.origin.url https://github.com/chengeast/quant-system.git
CFG_RE_EXIT=0
```

> **脱敏说明（如实）**：上述输出中 4 行 `credential.*.username` 的值以 `<脱敏>` 替代。这些值是针对**其他内网仓库**（`snsoft.vip` / `120.238.247.245`）的用户名/邮箱，**不是令牌/密码**；脱敏是为避免扩散与本项目无关的第三方个人信息，并非因属凭据。配置键名、`helper`/`provider` 值、来源文件路径均逐字保留，不影响技术判断。另执行了 `git config --show-origin --get-regexp 'remote\.'`（`CFG_REMOTE_EXIT=0`），输出为 `file:.git/config remote.origin.url https://github.com/chengeast/quant-system.git` 与 `file:.git/config remote.origin.fetch +refs/heads/*:refs/remotes/origin/*`。

### 1.2 层级定位（精确正则回读，改前）

命令：`git config --show-origin --get-regexp '^(http\.proxy|https\.proxy|credential\.helper|remote\.origin\.url)$'`

```
file:D:/Program Files/WorkBuddyAI/resources/vendor/PortableGit/etc/gitconfig	credential.helper manager
file:C:/Users/Administrator/.gitconfig	http.proxy socks5://127.0.0.1:10808
file:C:/Users/Administrator/.gitconfig	https.proxy socks5://127.0.0.1:10808
file:.git/config	remote.origin.url https://github.com/chengeast/quant-system.git
CFG_EXACT_EXIT=0
```

命令：`git config --get-all credential.helper`

```
manager
GETALL_CRED_EXIT=0
```

### 1.3 remote / HEAD / 日志 / status / 连通性（改前）

```
$ git remote -v
origin	https://github.com/chengeast/quant-system.git (fetch)
origin	https://github.com/chengeast/quant-system.git (push)
REMOTE_V_EXIT=0
$ git rev-parse --short HEAD
f6f27dd
HEAD_EXIT=0
$ git rev-parse HEAD
f6f27dd93af03e4ccf6b392735192976d45124dd
$ git log --oneline -3
f6f27dd 第十轮：3 金融专家复议 C-2b 结果，2:1 裁决存档冻结（暂停统计套利上线评估）
1ab33e6 补记 D-2 提交验收段：暂存区核对 + commit 907713d + push 未生效如实记录
907713d 第九轮：AVAX/ADA 扩池网格 125 组实测 + 本轮汇总报告 + .bat 启停修复
$ git status --short
 M agents/dispatch_queue.md
?? agents/f1_expert_A_opinion.md
STATUS_EXIT=0
$ timeout 30 git ls-remote origin refs/heads/main
f6f27dd93af03e4ccf6b392735192976d45124dd	refs/heads/main
LSREMOTE_EXIT=0
$ git rev-list --left-right --count HEAD...origin/main
0	0
REVLIST_EXIT=0
```

### 1.4 环境变量代理（脱敏）

显式列举 8 个标准代理变量（`printenv` 逐项读取）：

```
http_proxy=http://127.0.0.1:55483
https_proxy=http://127.0.0.1:55483
HTTP_PROXY=http://127.0.0.1:55483
HTTPS_PROXY=http://127.0.0.1:55483
all_proxy=<unset>
ALL_PROXY=<unset>
no_proxy=<unset>
NO_PROXY=<unset>
```

> `env | grep -i proxy` 亦真实执行（`ENVPROXY_GREP_EXIT=0`），另命中 `CODEBUDDY_SERVICE_PROXY_URL=http://127.0.0.1:55463/internal/hooks/services/invoke`、`WORKBUDDY_PROXY_SOURCE=system`，以及内部 MCP 配置变量 `CODEBUDDY_MCP_CONFIG`（**含 Bearer 令牌**）。按硬约束「不得把任何令牌/密码写入仓库、日志、纪要」，含令牌变量的值**不写入任何产物**；此处仅列与 git 网络代理直接相关的标准变量。

### 1.5 盘点结论

- **最主要不一致**：git 层 `http.proxy` / `https.proxy` = `socks5://127.0.0.1:10808`（来源 **global** `C:/Users/Administrator/.gitconfig`），而环境变量代理为 `http://127.0.0.1:55483` —— **方案（socks5 vs http）与端口（10808 vs 55483）均不一致**。与 `agents/dispatch_log.md` D-2 / E-2 段记录一致。
- `credential.helper = manager`：来源 **system** 级（`D:/Program Files/WorkBuddyAI/resources/vendor/PortableGit/etc/gitconfig`），`--get-all` 仅 1 个值（非多值）。
- `remote.origin.url = https://github.com/chengeast/quant-system.git`：来源 **local**（`.git/config`），标准 HTTPS 形态，**未嵌入任何凭据**。
- **local 层改前无任何 proxy / credential 配置**：`git config --local --get-regexp 'proxy|credential'` → `LOCAL_BEFORE_EXIT=1`、无输出。
- 远端 `main` = `f6f27dd93af03e4ccf6b392735192976d45124dd`，与本地 `HEAD=f6f27dd` 一致，`rev-list` = `0 0` → **老板手动推送已闭环，本批前本地即无待推 commit**（看板 E-2 行「远端状态以老板操作为准」至此核实）。
- 工作区既存 ` M agents/dispatch_queue.md`（秘书看板）与 `?? agents/f1_expert_A_opinion.md`（F-1 专家 A 发言）两项，均非本批产物、PM 未触碰。

---

## 2. 规范配置（步骤 2，逐项修改与影响范围）

**决策依据**：socks5 位于 **global**，改 global 会影响老板其他仓库（该 global 中另有 9 条针对 `snsoft.vip` / `120.238.247.245` 内网仓库的 credential 配置，改代理可能破坏其直连）。按派活单「优先 `--local` 仓库级配置」「确需 `--global` 改动须逐项列明影响范围并回报秘书，不得静默改全局」，**只做 `--local` 覆盖，不改 global**。

| # | 命令 | 层级 | 改前 | 改后 | 影响范围 |
|---|---|---|---|---|---|
| 1 | `git config --local http.proxy "http://127.0.0.1:55483"` | `--local` → `.git/config` | 无（继承 global `socks5://127.0.0.1:10808`） | `http://127.0.0.1:55483` | **仅本仓库**；`.git/config` 不入版本控制、不会 push 出去 |
| 2 | `git config --local https.proxy "http://127.0.0.1:55483"` | `--local` → `.git/config` | 无（继承 global `socks5://127.0.0.1:10808`） | `http://127.0.0.1:55483` | 同上 |

命令 EXIT：`SET_HTTP_PROXY_EXIT=0`、`SET_HTTPS_PROXY_EXIT=0`。

改后 local 层即时回读：

```
$ git config --local --get-regexp 'proxy|credential'
http.proxy http://127.0.0.1:55483
https.proxy http://127.0.0.1:55483
LOCAL_AFTER_EXIT=0
```

**未做的动作及理由（避免越权 / 遵守硬约束）**：

- **不改 `--global`**：`http.proxy`/`https.proxy` 的 socks5 值保留在 `C:/Users/Administrator/.gitconfig`；影响范围为老板所有其他仓库。按「不得静默改全局」本批不动，逐项列明影响范围并在日志/汇报中回报秘书，列为需老板拍板项（§6）。
- **不改 `credential.helper`**：system 级 `manager` 配置本身正确且为唯一值；`credential.helper` 属多值型配置，在 local 重复设置会导致 GCM 被重复调用，故不新增、不覆盖。
- **凭据读取问题的处置（如实说明能做什么、不能做什么）**：D-2 的 `fatal: could not read Username for 'https://github.com': terminal prompts disabled` 属**非交互会话下 GCM 无有效缓存凭据 / 无法弹窗**的凭据供给问题，非 git 配置错误。把令牌/PAT 写入 URL 或配置既属「把令牌写入仓库」又属「绕过认证」，硬约束禁止，PM 不做。本批配置层唯一可修的是消除 socks5 方案阻断（使 GCM 的 .NET `ServicePointManager` 不再因「不支持具有 socks5 方案的代理」而报错）；**凭据有效性须由老板在交互终端登录一次或提供有效凭据，决策/操作权属老板**。
- **不改 remote 目标**：`remote.origin.url` 保持原址（标准 HTTPS、未嵌入凭据），未改到任何未经确认地址。
- **未动** `.env`、`.gitignore`、`.bat`、`src/`、`config/strategy_params.yaml`、任何报告/脚本；**未写入任何令牌/密码**；未跑回测；未提交业务改动。

---

## 3. 改后回读（步骤 3）

### 3.1 派活单宽正则（改后）

命令：`git config --show-origin --get-regexp 'http\..*proxy|https\..*proxy|credential|remote\..*url'`

`CFG_RE_EXIT=0`。输出共 16 行配置：前 14 行与 §1.1 **逐字相同**（含同样的脱敏口径，此处不重复誊写），末尾新增的 local 2 行原文（真实回读）为：

```
file:.git/config	http.proxy http://127.0.0.1:55483
file:.git/config	https.proxy http://127.0.0.1:55483
```

改后全部 4 行 proxy 项的完整 `--show-origin` 原文见 §3.2，可据此逐项核对。

### 3.2 所有 proxy 项（含来源层级，用于判断 socks5 残留）

命令：`git config --show-origin --get-regexp 'proxy'`

```
file:C:/Users/Administrator/.gitconfig	http.proxy socks5://127.0.0.1:10808
file:C:/Users/Administrator/.gitconfig	https.proxy socks5://127.0.0.1:10808
file:.git/config	http.proxy http://127.0.0.1:55483
file:.git/config	https.proxy http://127.0.0.1:55483
CFG_PROXY_ALL_EXIT=0
```

### 3.3 本仓库生效值（最高优先级）

```
http.proxy => http://127.0.0.1:55483
https.proxy => http://127.0.0.1:55483
credential.helper => manager
```

### 3.4 socks5 残留判定

- **本仓库生效值：已无 socks5**（`--local` 优先于 `--global`，生效值均为 `http://127.0.0.1:55483`，与环境变量口径一致）。
- **`--show-origin` 全量视图：仍有 socks5 残留 2 行**，位于 global `C:/Users/Administrator/.gitconfig`。**保留理由**：改 global 会影响老板其他仓库且未经授权，按派活单「不得静默改全局」未动；已逐项列明影响范围并在日志与汇报中回报秘书。
- `git remote -v` 改后仍为 `https://github.com/chengeast/quant-system.git`（fetch / push 同址），**未改 remote 目标**。

---

## 4. 验证（步骤 3，原文与 EXIT）

```
=== [5] timeout 30 git ls-remote origin refs/heads/main（push 前连通性） ===
fatal: unable to access 'https://github.com/chengeast/quant-system.git/': schannel: failed to receive handshake, SSL/TLS connection failed
LSREMOTE_EXIT=128
=== [6] 至多 1 次 push：timeout 120 git push origin main ===
fatal: unable to access 'https://github.com/chengeast/quant-system.git/': schannel: failed to receive handshake, SSL/TLS connection failed
PUSH_EXIT=128
=== [7] push 后远端头 / rev-list / HEAD / status ===
fatal: unable to access 'https://github.com/chengeast/quant-system.git/': schannel: failed to receive handshake, SSL/TLS connection failed
LSREMOTE_AFTER_EXIT=128
0	0
REVLIST_EXIT=0
f6f27dd
HEAD_EXIT=0
--- git status --short ---
 M agents/dispatch_queue.md
?? agents/f1_expert_A_opinion.md
STATUS_EXIT=0
```

- `git push origin main` **仅执行 1 次**（`timeout 120`），`PUSH_EXIT=128`，原文如上。**失败不是超时**（瞬时返回 fatal，未触及 120s）。
- 失败后**未重试 / 未 force / 未改 remote / 未绕过认证 / 未再改任何 git 配置（含未回滚）**；本地 `HEAD=f6f27dd` 保留，`rev-list` = `0 0`，交老板手动推送。
- 上述 `git status --short` 为**写入本批两个产物之前**的基线；写入后的最终工作区状态见 §7 补记。

---

## 5. 结果分析（严格区分事实与推断）

**事实（盘上命令输出）**：

1. 改前 `ls-remote` = `f6f27dd93af03e4ccf6b392735192976d45124dd`、`LSREMOTE_EXIT=0`（读通道可用）。
2. 改后 `ls-remote`（push 前）、`push`（唯一 1 次）、`ls-remote`（push 后）**连续 3 次**均报 `schannel: failed to receive handshake, SSL/TLS connection failed`，`EXIT=128`。
3. 改后本仓库生效代理为 `http://127.0.0.1:55483`，与环境变量一致；global 仍为 socks5。
4. `rev-list` = `0 0`、`HEAD=f6f27dd`，本批前即无待推 commit。

**推断（标注为推断，非结论，不据此再改配置）**：改用 `http://127.0.0.1:55483` 后，Windows Schannel 后端经该 http 代理到 github.com 的 TLS 握手未能完成；而改前的 socks5 代理在本机 libcurl 下可完成匿名读。由此呈现真实两难：socks5 读通但 .NET GCM 不支持其方案（D-2 的 `ServicePointManager 不支持具有 socks5 方案的代理`），http/55483 口径一致却在本机 schannel 下握手失败。本批**不做超出盘上事实的因果断言**。

**达成情况**：配置盘点 ✅、规范化动作真实执行 ✅、改后回读 ✅、验证原文与 EXIT 如实 ✅、push 至多 1 次 ✅、两产物落盘 ✅；但**「消除后续 push 隐患」的连通性目标未达成** —— 改后 push 仍 `EXIT=128`，且读通道由 `EXIT=0` 退化为 `EXIT=128`。如实上报，不虚报通过。

---

## 6. 剩余风险与建议 / 需老板拍板项

1. **（重点）改后读通道退化**：本批 2 项 `--local` 改动经验证使 `ls-remote` 由 `EXIT=0` 变为 `EXIT=128`。按硬约束「失败后不重试、不再改配置、不绕过认证」，PM **未回滚**，当前 `.git/config` 保留改后状态。**是否回滚、是否属需立即纠正状态，属老板/秘书决策权**（回滚将产生新的 git 配置写操作）。
2. **global socks5 残留**：`C:/Users/Administrator/.gitconfig` 仍有 2 行 socks5，影响老板其他仓库；是否统一修正为可用 http 代理，需老板拍板（会影响其他仓库，PM 未越权）。
3. **凭据供给未实质解决**：`could not read Username` 需有效 GCM 凭据 / 老板交互登录一次；PM 不注入令牌、不绕过认证。
4. **可用代理地址待确认**：`http://127.0.0.1:55483` 在本机 schannel 下对 github TLS 握手失败；是否存在可用 http 代理端口，需老板/秘书提供后另批验证（本批 push 已用满 1 次，不再测）。
5. **工作区既存两项**：`agents/dispatch_queue.md`（秘书看板）、`agents/f1_expert_A_opinion.md`（F-1 发言）为本批前既存、非本批改动，PM 未触碰；「工作区除本批两产物外干净」按此口径理解。
6. **是否再次手动推送属老板决策权**：远端已与本地一致（`0 0`），本批实际无待推内容。
7. **无独立子代理通道**：未取得 devops 角色独立原始回报（见 §0），已如实标注。

---

## 7. 补记（产物写入后工作区复核）

命令：`git status --short`（产物写入后只读复核，真实回读、非推断）

```
 M agents/dispatch_log.md
 M agents/dispatch_queue.md
?? agents/f1_expert_A_opinion.md
?? agents/f2-devops-git-config.md
STATUS_EXIT=0
```

- **本批产物 2 项**：`M agents/dispatch_log.md`（F-2 段追加）、`?? agents/f2-devops-git-config.md`（新建）。
- **本批前既存、非本批改动 2 项**：`M agents/dispatch_queue.md`（秘书看板）、`?? agents/f1_expert_A_opinion.md`（F-1 专家 A 发言），PM 未触碰。
- 生效配置复核（只读）：`http.proxy` / `https.proxy` 仍为 `http://127.0.0.1:55483`，与 §3 一致，**写入产物过程中未再改动任何 git 配置**。

---

## 8. F-2-fix 复验与规范化补记（秘书验收打回返工，2026-11-10）

> 派活来源：秘书 `agents/dispatch_queue.md` 批次 **F-2-fix** 段。F-2 验收「不通过，打回返工」：F-2 把本仓库 `--local` 的 `http.proxy`/`https.proxy` 改成 `http://127.0.0.1:55483` 后，`git ls-remote origin refs/heads/main` 由改前 `EXIT=0`（远端 `f6f27dd93af03e4ccf6b392735192976d45124dd`）退化为 `fatal: schannel: failed to receive handshake, SSL/TLS connection failed`、`EXIT=128`，与老板「消除后续 push 隐患」目标相反。本批为秘书**正式授权的返工**，回滚 F-2 引入的 2 项 local 配置不算越权。
> 本批小批 1 件：只做**回滚 + 规范化建议**，不再做代理实验；除授权的两条 `--unset` 外**不再写任何 git 配置**；**不执行 push**（F-2 已用满 1 次）。

### 8.0 元信息与落实方式（如实标注）

| 项 | 内容 |
|---|---|
| 批次 | F-2-fix |
| 接单日期 | 2026-11-10 |
| 承派对象 | 运维工程师 `agents/souls/devops.md`（主，职责「版本迭代、分支策略、部署流程」；限制「不修改业务代码，只保障运行环境与部署流程」）；调试员 `agents/souls/debugger.md`（备，未启用） |
| 验收 | PM（`agents/souls/project_manager.md`）汇总，秘书亲自回读验收 |
| 工作目录 | `/c/Users/Administrator/WorkBuddy AI/2026-11-06-20-40-51`；`git rev-parse --show-toplevel` = `C:/Users/Administrator/WorkBuddy AI/2026-11-06-20-40-51`（`TOPLEVEL_EXIT=0`）；`git rev-parse --short HEAD` = `f6f27dd`（`HEAD_EXIT=0`） |

**落实方式（如实标注，沿用 E-2 / F-2 处理方式）**：PM 以 `ToolSearch` 检索「启动独立子代理 subagent 委派任务 / agent 子代理 独立会话 dispatch / spawn subagent independent agent task」，仅返回 `domain_search`（远程资料检索）、`mcp__space-library__library_search`（文档库检索）、`conversation_search`（历史会话检索）三个工具，**本会话确无独立子代理委派通道**。故按派活单允许的兜底方式：由 PM 依 `devops.md` soul 组织落实，**未取得该角色独立原始回报**。但本批**回滚与复验命令均真实执行、输出真实回读**（原始输出见 §8.1 / §8.2），并非只写文档；所有事实以盘上命令输出为准，未编造任何输出。

### 8.1 回滚（步骤 1，原始输出与 EXIT）

仅执行派活单授权的 2 条写命令，未动 `--global` / remote / credential / `.env`：

```
$ git config --local --get-regexp 'proxy|credential'（回滚前回读）
http.proxy http://127.0.0.1:55483
https.proxy http://127.0.0.1:55483
LOCAL_BEFORE_EXIT=0
$ git config --local --unset http.proxy
UNSET_HTTP_EXIT=0
$ git config --local --unset https.proxy
UNSET_HTTPS_EXIT=0
$ git config --local --get-regexp 'proxy|credential'（回滚后回读）
（无输出）
LOCAL_AFTER_EXIT=1
```

- 回滚前 local 层确有 F-2 写入的 2 项 `http://127.0.0.1:55483`；两条 `--unset` 均 `EXIT=0`。
- 回滚后 local 层 `proxy|credential` **无输出**、`EXIT=1`（`git config --get-regexp` 无匹配时返回 1，即 local 层已无任何 proxy / credential 配置）。

### 8.2 硬验收复验（步骤 2，原始输出与 EXIT）

```
=== [V1] git config --local --get-regexp 'proxy|credential' ===
（无输出）
V1_LOCAL_EXIT=1
=== [V2] git config --show-origin --get-regexp 'proxy' ===
file:C:/Users/Administrator/.gitconfig	http.proxy socks5://127.0.0.1:10808
file:C:/Users/Administrator/.gitconfig	https.proxy socks5://127.0.0.1:10808
V2_ORIGIN_EXIT=0
=== [V3] git config --get http.proxy ===
socks5://127.0.0.1:10808
V3_HTTP_EXIT=0
=== [V4] git config --get https.proxy ===
socks5://127.0.0.1:10808
V4_HTTPS_EXIT=0
=== [V5] timeout 30 git ls-remote origin refs/heads/main ===
f6f27dd93af03e4ccf6b392735192976d45124dd	refs/heads/main
V5_LSREMOTE_EXIT=0
=== [V6] git rev-list --left-right --count HEAD...origin/main ===
0	0
V6_REVLIST_EXIT=0
=== [V7] git rev-parse --short HEAD ===
f6f27dd
V7_HEAD_EXIT=0
=== [V8] git status --short ===
 M agents/dispatch_log.md
 M agents/dispatch_queue.md
?? agents/f1_expert_A_opinion.md
?? agents/f2-devops-git-config.md
V8_STATUS_EXIT=0
```

**逐项核对**：
- **V1**：local 层 `proxy|credential` 无输出（应无 proxy 输出）✅，`EXIT=1`（无匹配）。
- **V2**：`--show-origin` 全量 proxy 视图**仅剩 global 2 行 socks5**，无任何 `file:.git/config` 行 → F-2 的 2 项 local 覆盖已彻底移除 ✅。
- **V3/V4**：生效值 `http.proxy` / `https.proxy` 均恢复 `socks5://127.0.0.1:10808`，与改前「继承 global socks5」的可用基线一致 ✅。
- **V5（最关键）**：`timeout 30 git ls-remote origin refs/heads/main` → **`f6f27dd93af03e4ccf6b392735192976d45124dd	refs/heads/main`、`V5_LSREMOTE_EXIT=0`** ✅ **读通道已恢复 `EXIT=0`，远端 hash 与派活单要求、与 F-2 §1.3 改前记录完全一致**。
- **V6**：`rev-list HEAD...origin/main` = `0	0` ✅（本地与远端一致，无待推/待拉 commit）。
- **V7**：`HEAD=f6f27dd` ✅。
- **V8**：`git status --short` 如实 4 项（见 §8.7 口径），`EXIT=0`。

> 秘书验收项「若复验仍不为 `EXIT=0`，如实上报、不再改任何配置、不重试、交秘书」：**本次 `V5_LSREMOTE_EXIT=0`，未触发该失败分支**；本批未重试任何命令、未再改任何 git 配置。

### 8.3 实测结论与 proxy 黑名单

**实测链（三次状态，均为盘上命令输出）**：

| 状态 | 本仓库生效代理 | `ls-remote` | 出处 |
|---|---|---|---|
| 改前（F-2 §1.3 / 秘书验收） | `socks5://127.0.0.1:10808`（继承 global，local 无覆盖） | `EXIT=0`，远端 `f6f27dd93af03e4ccf6b392735192976d45124dd` | F-2 文档 §1.3 |
| F-2 改后 | `http://127.0.0.1:55483`（local 覆盖） | `EXIT=128`（`schannel: failed to receive handshake, SSL/TLS connection failed`），连续 3 次网络操作均失败 | F-2 文档 §4 / 秘书验收 |
| F-2-fix 回滚后 | `socks5://127.0.0.1:10808`（global，local 已清空） | `EXIT=0`，远端 `f6f27dd93af03e4ccf6b392735192976d45124dd` | 本批 §8.2 V5 |

> **本仓库 proxy 黑名单：`http://127.0.0.1:55483`（已实测退化，禁止覆盖）**。该值在本仓库、本机、当前环境下经实测使读通道由 `EXIT=0` 退化为 `EXIT=128`；作为运维规范，本仓库**不得**再把 `--local`（或任何层级）的 `http.proxy` / `https.proxy` 覆盖为此值。此为基于实测的保守禁用结论，**不外推为「该端口在所有环境均不可用」的普遍因果律**。

### 8.4 真正 push 隐患的定位（严格区分事实与推断）

**事实（盘上 / 日志可回读）**：
1. D-2 两次 push 均 `EXIT=128`，错误含 `ServicePointManager 不支持具有 socks5 方案的代理` 与 `could not read Username for 'https://github.com': terminal prompts disabled`；E-2 单次 push `EXIT=124` 超时；最终由**老板手动推送闭环**（F-2 §1.3 与本报告 §8.2 V5/V6 复核：远端 `main` = `f6f27dd93af03e4ccf6b392735192976d45124dd` = 本地 `HEAD`，`rev-list 0 0`）。
2. 改前 socks5 基线下 `ls-remote` 匿名读通道 `EXIT=0`（F-2 §1.3、本批 V5 复核）。
3. F-2 把 local 覆盖为 `http://127.0.0.1:55483` 后，读通道退化为 `EXIT=128`（F-2 §4）。
4. 本批回滚后读通道恢复 `EXIT=0`（V5），`rev-list 0 0`。

**推断（标注为推断，非结论，不据此改配置、不下无依据因果断言）**：
- D-2 的 `could not read Username ... terminal prompts disabled` 指向**非交互会话下 GCM（`credential.helper=manager`）无有效缓存凭据 / 无法弹窗**的**凭据供给问题**，而非 git 代理配置错误。这是对错误文本与会话性质的解读，**不是经本批复验实证的因果结论**；本批未采集 GCM 弹窗/缓存状态，不宣称已证实。
- 结合 F-2 §5 记录的**两难**：socks5 读通（匿名 `ls-remote` 可用），但 .NET GCM 的 `ServicePointManager` 不支持其方案（D-2 报错）；而 `http://127.0.0.1:55483` 与环境变量口径一致，却在本机 Schannel 下对 github TLS 握手失败。二者均无法在本批环境内同时满足「读通 + GCM 可认证」。
- **结论边界**：本批能确定的只是「回滚 local 覆盖恢复了读通道」；**真正的 push 隐患（凭据供给）并未由本批解决**，也不存在「改某个代理值即可消除 push 隐患」的盘上依据。故本批不再做任何代理实验，把处置交给 §8.5 的自检清单与 §8.6 的老板拍板项。

### 8.5 不写入任何令牌/密码的可执行建议 + push 前自检清单

**凭据供给（不写令牌的正规做法）**：由老板在**交互终端**（可弹窗、非 `terminal prompts disabled` 会话）对 `https://github.com/chengeast/quant-system.git` 完成一次 GitHub 登录 / GCM 凭据缓存（`credential.helper=manager` 已就位），使后续非交互会话能从凭据管理器读到有效凭据。**全程不把任何令牌 / PAT / 密码写入 URL、配置、`.env`、报告、日志或纪要**；PM/运维不注入凭据、不绕过认证。

**push 前自检清单（每次推送前依序执行，全部通过才进行单次 push）**：

| # | 命令 / 动作 | 通过标准 |
|---|---|---|
| ① | `git config --get http.proxy`（及 `https.proxy`） | 生效值仍为可用基线 `socks5://127.0.0.1:10808`，**未被覆盖为黑名单 `http://127.0.0.1:55483`** |
| ② | `git ls-remote origin refs/heads/main` 预检（带超时） | **`EXIT=0`**，远端 hash 与预期一致；非 0 立即停，不 push、不重试、不改配置 |
| ③ | `git status --short` | 明确待推内容；确认无 `.env` / 密钥 / 令牌文件、无不应提交的大数据 |
| ④ | 单次 `git push origin main`（带超时） | **只推 1 次**；失败**不重试 / 不 force / 不改 remote 目标 / 不绕过认证 / 不再改任何 git 配置**，如实记录原文与 EXIT 并交秘书 |

> 清单 ①②③ 为只读预检，④ 为唯一写操作；任何一步不达标即停止，不以「多试几次」「换个代理」「临时关认证」等方式绕过。

### 8.6 需老板拍板项（PM/运维不越权）

| # | 待拍板项 | 影响范围 / 为何不自行处置 |
|---|---|---|
| 1 | **是否统一修 global socks5**（`C:/Users/Administrator/.gitconfig` 的 `http.proxy`/`https.proxy` = `socks5://127.0.0.1:10808`） | 该 global 影响**老板所有其他仓库**，且其中另有 9 条针对 `snsoft.vip` / `120.238.247.245` 内网仓库的 credential 配置，改代理可能影响其直连；「不得静默改全局」，PM/运维不越权 |
| 2 | **是否改 remote 形态**（如改用 SSH `git@github.com:...` 或其他 URL 形态） | 改 remote 目标属未确认变更；且任何「URL 内嵌凭据」的做法会把令牌写入仓库，硬约束禁止，仅列为老板可选决策，PM/运维不实施 |
| 3 | **是否再次手动推送** | 当前 `rev-list 0 0`、远端 `f6f27dd93af03e4ccf6b392735192976d45124dd` = 本地 HEAD，本批实际无待推内容；F-2 已用满 1 次 push、本批不再 push；是否/何时推送属老板决策权 |

### 8.7 硬约束遵守与验收判定（PM）

**硬约束逐条**：本批**不执行 push**（F-2 已用满 1 次）✅；不跑回测 ✅；不放宽冻结门槛 ✅；不提交业务改动 ✅；不改 `--global` / remote / credential / `.env` ✅；不写任何令牌/密码 ✅；失败不重试、不绕过认证 ✅；不抢跑 F-1 专家复议 ✅；产出仅本章 + `agents/dispatch_log.md` 追加 **F-2-fix** 段 ✅；不改 `agents/dispatch_queue.md`（秘书维护）、不改 `src/` / `config/` / `.bat` / `.gitignore` / 任何报告脚本 ✅。写操作仅授权的两条 `git config --local --unset`。

**工作区状态（V8，写入本批产物前）**：` M agents/dispatch_log.md`、` M agents/dispatch_queue.md`、`?? agents/f1_expert_A_opinion.md`、`?? agents/f2-devops-git-config.md`。其中 `dispatch_queue.md`（秘书看板）、`f1_expert_A_opinion.md`（F-1 发言）为本批前既存、PM 未触碰；`dispatch_log.md`、`f2-devops-git-config.md` 为 F-2 产物，本批在其上**追加**（不新建文件）。写入后最终复核见 `agents/dispatch_log.md` F-2-fix 段补记。

**验收判定**：**F-2-fix 的回滚与硬验收复验通过** —— local 层 proxy 已清空、生效值恢复 `socks5://127.0.0.1:10808`、`ls-remote` 由 F-2 改后的 `EXIT=128` 恢复为 **`EXIT=0`** 且远端 hash 与改前一致、`rev-list 0 0`。但须明确：**这是对 F-2 错误改动的回滚恢复，不代表 F-2 验收转为通过、不代表「消除后续 push 隐患」目标已达成**；F-2 验收不通过的结论不变，真正的 push 隐患（凭据供给）仍未解决，需按 §8.6 交老板拍板。不把「失败」表述为「通过」。
