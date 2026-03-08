#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
URL="http://${HOST}:${PORT}/"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "未找到 Python 虚拟环境: .venv"
  echo "请先在项目目录执行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ "$OPEN_BROWSER" == "1" ]] && command -v xdg-open >/dev/null 2>&1; then
  (
    sleep 1
    xdg-open "$URL" >/dev/null 2>&1 || true
  ) &
fi

echo "AcompaLLM 正在启动..."
echo "访问地址: $URL"
echo "停止服务: Ctrl+C"

exec .venv/bin/uvicorn APIAgent:app --host "$HOST" --port "$PORT"
