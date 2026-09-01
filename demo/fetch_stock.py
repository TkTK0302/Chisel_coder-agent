#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch stock data — 用于演示环境自愈与反思纠错。

剧本设计：
  1. 脚本 import requests（容器里故意没装） → ModuleNotFoundError
  2. Agent 自主 pip install requests 修复
  3. 脚本中 datta 变量拼写错误 → NameError
  4. Agent 用 edit_file 修复
  5. 最终成功打印 JSON 结果
"""

import json
import sys

try:
    import requests
except ImportError:
    print("Error: requests module not found. Please install it: pip install requests", file=sys.stderr)
    sys.exit(1)


def fetch_stock_price(symbol: str) -> dict:
    """获取股票价格（模拟 API 调用）。

    实际演示中会因网络问题走 fallback 路径，展示 JSON 输出即可。
    """
    url = f"https://api.example.com/v1/stock/{symbol}/quote"

    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        datta = resp.json()  # BUG: 变量名拼写错误，应为 data
    except (requests.ConnectionError, requests.Timeout):
        # 模拟环境无网络，走 fallback
        datta = {
            "symbol": symbol,
            "price": 150.25,
            "change": 2.35,
            "change_percent": 1.59,
            "volume": 1_250_000,
            "timestamp": "2026-09-01T10:30:00Z",
        }

    # 下面使用正确的变量名 data —— 但上面赋的是 datta，这里会 NameError
    result = {
        "status": "success",
        "data": data,  # NameError: name 'data' is not defined
    }
    return result


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    try:
        result = fetch_stock_price(symbol)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Fatal error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)