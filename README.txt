══════════════════════════════════════════
  Chisel —— 编程智能体（Coding Agent）
  像凿子一样精准地改写代码，而不是推倒重来。
══════════════════════════════════════════

  一、Git 仓库
  https://github.com/TkTK0302/Chisel_coder-agent

  二、运行方式
  1. 安装依赖：pip install -r requirements.txt
  2. 配置密钥：创建 .env，写入 LLM_API_KEY=sk-xxx
  3. 命令行：python agent.py "任务描述" --workspace <目录>
  4. 桌面版：python desktop/main.py

  三、核心特色
  · 双模式规划 —— single（单 Agent 先计划审批再执行）
    和 multi（专用规划 Agent 拆解派发子 Agent 执行）
  · Docker 沙盒 —— 命令隔离执行，不可用时自动降级
  · 13 个工具 —— bash、read/write/edit_file（5 层模糊匹配）、
    git（自动快照 + 账本式安全回滚）、code_navigate
    （自研 AST 符号导航）、terminal、web_fetch/search、
    rag_search（BM25+向量混合检索，RRF 融合）
  · 6 级安全评估 —— 绝对安全白名单 → 条件命令检查 →
    受保护路径 → Shell AST 分析 → 正则模式 → LLM 兜底
  · Shadow Backup —— 高危操作前自动 zip 快照
  · 上下文压缩 —— 滑动窗口 + 关键点模板化提取
  · 死循环检测 —— 连续相同调用 3 次警告 5 次中止
  · 桌面应用 —— Eel 跨平台 GUI，项目管理、对话持久化、
    文件浏览器、任务历史、拖拽布局

  四、设计说明
  全部核心逻辑（对话管理、工具定义与执行、模型输出解析、
  循环终止条件、错误处理）自行编写，未使用任何 agent 框架
  或 SDK。仅依赖 OpenAI 兼容 API 客户端库和模型原生
  tool calling 接口。支持 deepseek-chat 等兼容模型。