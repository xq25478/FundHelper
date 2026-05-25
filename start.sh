#!/usr/bin/env bash
# fund-helper 本地启动脚本：杀旧进程 + 释放端口 + 后台启动 + 健康检查
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${FH_HOST:-127.0.0.1}"
PORT="${FH_PORT:-7788}"
OPEN_FLAG="${FH_OPEN:---no-open}"   # 默认不自动打开浏览器；设 FH_OPEN=--open 启用
LOG="${FH_LOG:-/tmp/fh_serve.log}"
VENV="$ROOT/fund"
FH_BIN="$VENV/bin/fh"
PY_BIN="$VENV/bin/python"

color()  { printf "\033[%sm%s\033[0m\n" "$1" "$2"; }
info()   { color "36" "[fh] $*"; }
ok()     { color "32" "[fh] $*"; }
warn()   { color "33" "[fh] $*"; }
err()    { color "31" "[fh] $*"; }

if [[ ! -x "$FH_BIN" ]]; then
  err "未找到 fh 可执行: $FH_BIN  （请先 source ./fund/bin/activate 并 pip install -e .）"
  exit 1
fi

# 1) kill 旧的 fh serve 进程（任意 host/port）
PIDS=$(pgrep -f "fh serve" || true)
if [[ -n "$PIDS" ]]; then
  warn "kill 旧 fh serve 进程: $PIDS"
  kill $PIDS 2>/dev/null || true
  sleep 1
  PIDS_LEFT=$(pgrep -f "fh serve" || true)
  if [[ -n "$PIDS_LEFT" ]]; then
    warn "仍存活，强制 kill -9: $PIDS_LEFT"
    kill -9 $PIDS_LEFT 2>/dev/null || true
    sleep 1
  fi
fi

# 2) 释放目标端口（防止别的进程占用）
PORT_PIDS=$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [[ -n "$PORT_PIDS" ]]; then
  warn "端口 $PORT 被占用，kill: $PORT_PIDS"
  kill $PORT_PIDS 2>/dev/null || true
  sleep 1
  PORT_PIDS_LEFT=$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$PORT_PIDS_LEFT" ]]; then
    warn "端口仍占用，强制 kill -9: $PORT_PIDS_LEFT"
    kill -9 $PORT_PIDS_LEFT 2>/dev/null || true
    sleep 1
  fi
fi

# 3) 后台启动（脱离当前会话，避免随终端退出而退出）
info "启动 fh serve --host $HOST --port $PORT $OPEN_FLAG  (log: $LOG)"
: > "$LOG"
"$PY_BIN" -c "
import os, sys
pid = os.fork()
if pid > 0: sys.exit(0)
os.setsid()
pid2 = os.fork()
if pid2 > 0: sys.exit(0)
sys.stdin  = open('/dev/null')
sys.stdout = open('$LOG', 'a')
sys.stderr = sys.stdout
os.execvp('$FH_BIN', ['fh','serve','--host','$HOST','--port','$PORT','$OPEN_FLAG'])
"

# 4) 健康检查
URL="http://$HOST:$PORT/api/healthz"
for i in $(seq 1 25); do
  sleep 0.4
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL" || echo 000)
  if [[ "$CODE" == "200" ]]; then
    NEW_PID=$(pgrep -f "fh serve" | head -1)
    ok   "服务已就绪：http://$HOST:$PORT  (pid $NEW_PID)"
    ok   "日志：tail -f $LOG"
    exit 0
  fi
done

err "服务启动超时（10s 内未返回 200）。最近日志："
tail -20 "$LOG" >&2
exit 1
