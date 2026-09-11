"""regwatch 网页端一键启动脚本。

用法::

    python run_web.py            # 默认 http://localhost:8501
    python run_web.py --port 8600
    python run_web.py --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_PATH = PROJECT_ROOT / "web" / "app.py"


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 regwatch 网页界面")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    if not APP_PATH.exists():
        print(f"未找到网页入口：{APP_PATH}")
        raise SystemExit(2)

    # 把项目根与 web/ 加入 PYTHONPATH，保证页面可直接 import regwatch / components
    existing = os.environ.get("PYTHONPATH", "")
    parts = [str(PROJECT_ROOT), str(PROJECT_ROOT / "web")]
    for part in existing.split(os.pathsep):
        if part:
            parts.append(part)
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))

    try:
        import streamlit.web.cli as stcli
    except ImportError:
        print("未安装 streamlit，请先执行：pip install -r requirements.txt")
        raise SystemExit(2)

    print(f"启动 regwatch 网页界面：http://{args.host}:{args.port}")
    sys.argv = [
        "streamlit", "run", str(APP_PATH),
        f"--server.port={args.port}",
        f"--server.address={args.host}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
