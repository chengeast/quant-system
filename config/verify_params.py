"""
策略参数完整性校验（防误改 + 决策留痕）

不依赖第三方库（纯标准库），任何环境可运行。

指纹口径：
  取 strategy_params.yaml 正文（排除 audit: 段，保留 live_trading: 段）
  的 SHA256。参数主体被改动 → 指纹变化 → 与 audit 记录不符即告警。

用法：
  python config/verify_params.py            # 校验当前参数是否仍为冻结版
  python config/verify_params.py --update   # 参数经正式变更流程后，追加 audit 留痕
"""

import hashlib
import sys
from pathlib import Path

PARAMS_FILE = Path(__file__).resolve().parent / "strategy_params.yaml"


def _body_fingerprint() -> str:
    """对参数主体（排除 audit 段）计算 SHA256。"""
    lines = PARAMS_FILE.read_text(encoding="utf-8").splitlines()
    in_audit, body = False, []
    for ln in lines:
        if ln.startswith("audit:"):
            in_audit = True
        if in_audit:
            if ln.startswith("live_trading:"):
                in_audit = False
                body.append(ln)
            continue
        body.append(ln)
    return hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest()


def _recorded_checksums() -> list:
    import re
    text = PARAMS_FILE.read_text(encoding="utf-8")
    return [c for c in re.findall(r'checksum:\s*"([0-9a-f]{64})"', text)]


def verify() -> bool:
    actual = _body_fingerprint()
    recorded = _recorded_checksums()
    print(f"实际指纹：{actual}")
    print(f"记录指纹：{recorded}")
    if not recorded:
        print("⚠️  尚无有效校验和，请运行 --update 生成。")
        return False
    if actual in recorded:
        print("✅ 参数与冻结记录一致。")
        return True
    print("❌ 参数已被改动且未走审计流程！复核后运行 --update 留痕。")
    return False


def update() -> None:
    actual = _body_fingerprint()
    # 追加一条 audit 记录
    import re
    text = PARAMS_FILE.read_text(encoding="utf-8")
    marker = 'live_trading:'
    entry = (
        f'  - ts: "manual"\n'
        f'    actor: "manual"\n'
        f'    action: "checksum_refresh"\n'
        f'    reason: "参数变更流程留痕"\n'
        f'    checksum: "{actual}"\n'
    )
    # 在 audit 段（首个 live_trading: 前）末尾插入
    idx = text.index(marker)
    text = text[:idx] + entry + text[idx:]
    PARAMS_FILE.write_text(text, encoding="utf-8")
    print(f"✅ 已追加校验和：{actual}")


if __name__ == "__main__":
    if "--update" in sys.argv:
        update()
    else:
        sys.exit(0 if verify() else 1)
