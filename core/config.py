"""配置读取：从 .env 读取（复用 agent._load_env_file 的思路，独立成模块避免循环导入）。

优先级：进程环境变量 > .env 文件。
"""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str = ".env") -> dict:
    """解析 KEY=VALUE 格式的 .env 文件（不引入 python-dotenv 依赖）。"""
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get(key: str, default: str | None = None, env_file: str = ".env") -> str | None:
    """环境变量优先，其次 .env 文件。"""
    if os.environ.get(key):
        return os.environ[key]
    return load_env(env_file).get(key) or default
