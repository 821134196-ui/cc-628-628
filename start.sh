#!/usr/bin/env bash
# 一条命令启动本系统：创建虚拟环境（如需）→ 安装依赖 → 启动服务
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv --without-pip .venv 2>/dev/null || python3 -m venv .venv
fi
if [ ! -x .venv/bin/pip ]; then
  curl -sSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python -q
fi
.venv/bin/python -m pip install -q -r requirements.txt
exec .venv/bin/python run.py
