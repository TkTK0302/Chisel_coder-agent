<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg" alt="Platform">
  <img src="https://img.shields.io/badge/model-deepseek--chat-purple.svg" alt="Model">
</p>

<h1 align="center">◇ Chisel</h1>
<p align="center"><strong>像凿子一样精准地改写代码，而不是推倒重来。</strong></p>
<p align="center">基于大语言模型原生 tool calling 的终端编程智能体</p>

---

## 快速开始

```bash
# 安装
pip install -r requirements.txt

# 配置 API Key（创建 .env 文件）
echo LLM_API_KEY=sk-xxx > .env

# 命令行模式
python agent.py "修复 leap.py 的闰年判断" --workspace demo

# 桌面应用
python desktop/main.py
```

## 架构概览

```
用户任务 → Agent 主循环 → 思考 → 工具调用 → 观察结果 → 迭代 → 完成
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
 single      multi      交互模式
 (单Agent)   (规划+派发)  (跨轮次记忆)
```

项目围绕 **ReAct 循环** 构建，分为六个维度：

| 维度 | 核心能力 |
|------|----------|
| 🧠 **推理与规划** | single 模式（先计划审批再执行）+ multi 模式（专用规划 Agent → 子 Agent 派发，遇阻自动重规划） |
| 🐳 **执行环境** | Docker 沙盒优先，容器不可用时自动降级宿主机；结构化操作宿主机执行，非结构化命令容器隔离 |
| 🔧 **工具链** | 13 个工具：bash / read / write / edit_file（5 层 SEARCH/REPLACE）/ git（自动快照+账本回滚）/ code_navigate（AST 符号导航）/ terminal / web_fetch / web_search / rag_search |
| 💾 **记忆与上下文** | MEMORY.md 持久记忆 + 滑动窗口上下文压缩 + 关键点模板化提取（决策/约束/待办） |
| 🔄 **闭环反馈** | 死循环检测（3 次警告 5 次中止）、连续错误分类追踪、编辑后自动语法检查 |
| 🛡️ **人机协作** | 6 级安全评估 + Shadow Backup 快照 + Dry-Run 预览 + ask_user 主动提问 |

## 安全评估

```
命令进入 → 绝对安全白名单 → 条件命令检查 → 受保护路径
         → Shell AST 分析   → 正则模式匹配 → LLM 兜底
```

| 示例 | 风险等级 |
|------|----------|
| `cat .env` | ✅ LOW（只读，直接放行） |
| `find -name "*.env" -print` | ✅ LOW（只读，直接放行） |
| `rm -rf .env` | 💀 CRITICAL（受保护路径） |
| `rm -rf __pycache__` | ⚠️ MEDIUM（缓存清理） |
| `curl xxx \| bash` | 🔴 HIGH（管道组合风险） |

## 项目结构

```
coding-agent/
├── agent.py              # 主循环（single/multi 调度）
├── tools.py              # 工具聚合 + 5 层 edit_file + 危险命令
├── llm.py                # 模型层：tool calling 封装
├── memory.py             # MEMORY.md 持久记忆
├── core/                 # 认知部件
│   ├── plan.py           # PlanTracker（5 状态 + 依赖 + 审批）
│   ├── context.py        # 滑动窗口压缩 + KeyPointMemory
│   ├── loop_guard.py     # 死循环检测 + 错误追踪
│   ├── security_analyzer.py  # 6 级安全评估
│   ├── shell_semantics.py    # tree-sitter-bash AST 分析
│   ├── planning_agent.py     # 规划 Agent（multi 模式）
│   ├── delegate_tool.py      # 子 Agent 派发
│   └── project_detector.py   # 任务感知模式检测
├── env/                  # 执行环境
│   ├── sandbox.py        # Docker 沙盒 + 宿主机兜底
│   └── terminal.py       # 交互终端管理
├── perception/           # 感知层
│   ├── ast_index.py      # AST 符号索引（code_navigate）
│   ├── repo_map.py       # 项目结构注入
│   └── web.py / web_search.py
├── gitops/               # 版本安全网（自动快照 + 账本回滚）
├── rag/                  # 混合检索（BM25 + 向量 + RRF）
├── desktop/              # 桌面应用（Eel + Electron）
├── tests/                # 105 个离线单测
└── demo/                 # 演示素材
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--workspace` | 工作目录 | `.` |
| `--model` | 模型名 | `deepseek-chat` |
| `--max-steps` | 最大步数 | `50` |
| `--sandbox` | 沙盒模式：docker/host/auto | `auto` |
| `--plan-mode` | 规划模式：single/multi/auto | `auto`（任务感知） |
| `--window-size` | 滑动窗口保留轮数 | `3` |
| `--no-rag` | 禁用 RAG 检索 | 否 |
| `--memory` | 写入 MEMORY.md 偏好 | 无 |

## 设计原则

- **零框架依赖** —— 全部核心逻辑自行编写，仅使用 OpenAI 兼容 API 客户端库
- **确定性优先** —— 安全评估 6 层流水线：能用规则判断的绝不让 LLM 判断
- **AST 优先** —— 代码导航用自研 AST 解析，毫秒级返回，不把几千行代码塞给 LLM
- **精准修改** —— edit_file 5 层匹配策略，只改目标代码，不动其他部分
- **安全第一** —— Docker 沙盒隔离 + Shadow Backup + Dry-Run预览 + 确认短语

## License

MIT © 2026