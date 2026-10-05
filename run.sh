#!/usr/bin/env bash
# 一条本地命令启动：建虚拟环境 → 装依赖 → 初始化 SQLite/种子数据 → 启动服务
set -e
cd "$(dirname "$0")"

PY=python3
if [ ! -d ".venv" ]; then
  $PY -m venv .venv 2>/dev/null || $PY -m venv --without-pip .venv
fi
source .venv/bin/activate

# 兼容系统缺少 ensurepip 的情况：venv 内没有 pip 时用 get-pip.py 引导
if ! python -m pip --version >/dev/null 2>&1; then
  python scripts/get-pip.py -q
fi
pip install -q -r app/requirements.txt
python -m app.seed

echo ""
echo "=============================================="
echo " 相机镜头租赁核对与暗伤争议系统已启动"
echo " 前端:    http://127.0.0.1:8000/"
echo " API文档: http://127.0.0.1:8000/docs"
echo "=============================================="
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
