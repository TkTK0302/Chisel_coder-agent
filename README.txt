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
  · 双模式调度 —— 自动检测项目规模，小任务走单 Agent（Plan→审批→执行），
    大任务走 Planning Agent 只读探索 + 委派子 Agent 并行执行
  · 16 个工具 —— 去中心化注册，edit_file 5 层降级匹配（精确→去空白→省略号
    →缩进→模糊），code_navigate 自研 AST 毫秒级符号导航
  · 6 级安全管道 —— 白名单→条件命令→保护路径→Shell AST 语义分析→正则模式
    →LLM 兜底，规则优先零延迟；CRITICAL 操作需输入确认短语 + Shadow Backup
  · 上下文管理 —— 滑动窗口压缩（保护用户消息+最近N轮，远期 LLM 摘要），
    行级截断保留头尾 + 完整内容存文件，关键点模板化提取决策/约束/待办
  · 持久记忆 —— MEMORY.md 文件 + SQLite FTS5 全文搜索，BM25 排序
  · 混合检索 —— BM25 关键词 + FAISS 向量语义搜索，RRF 融合排序
  · Git 安全网 —— 写操作前自动快照，账本式撤销只回滚 Agent 自己提交
  · Docker 沙箱 —— 命令隔离执行，不可用时自动降级 Host
  · 闭环反馈 —— 死循环检测（3 次警告 5 次中止），错误分类 + 按工具独立重试
  · 桌面应用 —— Eel 跨平台 GUI，项目管理、对话持久化、文件浏览器

  四、设计说明
  全部核心逻辑自行编写，未使用任何 agent 框架或 SDK，仅依赖 OpenAI 兼容 API
  客户端库和模型原生 tool calling 接口，支持 deepseek-chat 等兼容模型。参考
  Aider 的 SEARCH/REPLACE 编辑、OpenHands 的 Planning+Delegate 多 Agent 架
  构、Cline 的死循环检测与可见性设计，融三家之长，自研 5 层匹配、6 级安全管
  道、滑动窗口压缩等核心机制。