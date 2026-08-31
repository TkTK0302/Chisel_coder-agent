Chisel —— 编程智能体（Coding Agent）
像凿子一样精准地改写代码，而不是推倒重来。

一、项目简介
基于大语言模型原生 tool calling 的终端编程智能体。用户下达任务后，agent 自主读写文件、
执行命令、调用工具，反复迭代直到完成。核心逻辑（对话管理、工具定义与执行、模型输出解析、
循环控制、错误处理）全部自行编写，不使用任何 agent 框架或 SDK。提供命令行和桌面应用
两种使用方式。

二、运行方式
安装依赖：pip install -r requirements.txt
配置密钥：在项目根目录创建 .env 文件，写入 LLM_API_KEY=sk-xxx（已加入 .gitignore）
命令行模式：python agent.py "修复 leap.py 的闰年判断" --workspace demo
桌面应用：python desktop/main.py

三、整体架构
项目围绕"思考→行动→观察"的 ReAct 循环构建，分为六个维度：
1. 推理与规划 —— 支持 single 模式（同一 Agent 先计划审批再执行）和 multi 模式
  （专用规划 Agent 拆解任务后派发给子 Agent 执行，遇阻自动重规划），每轮自动注入进度
2. 执行环境 —— Docker 沙盒优先，容器不可用时自动降级宿主机，保证命令执行安全隔离
3. 工具链集成 —— 13 个工具：bash / read_file / write_file / edit_file（5 层
   SEARCH/REPLACE 匹配策略）/ git（自动快照 + 账本式安全回滚）/ code_navigate
   （AST 符号导航）/ terminal / web_fetch / web_search / rag_search（BM25+向量
   混合检索，中文 jieba 分词，RRF 融合，Rerank 重排序）
4. 记忆与上下文 —— MEMORY.md 持久记忆（支持 key-value 覆盖与 AI 自动写入）+
   递归分治上下文压缩 + 长输出截断保存到文件
5. 闭环反馈 —— 死循环检测（连续相同调用 3 次警告 5 次中止，穿插工具即重置）、
   连续错误分类追踪（API 错误/工具调用错误/执行失败）、编辑后自动语法检查与 ruff 修复
6. 人机协作 —— 4 级风险确认（UNKNOWN/LOW/MEDIUM/HIGH，策略可配置）、
   always_allow 会话缓存、ask_user 主动提问、shell 语义 AST 分析、操作审计日志

四、核心设计
整个系统从零开始构建，所有核心机制均为自主实现。关键设计包括：基于 tool_choice="auto"
的循环终止策略、SEARCH/REPLACE 五层宽松匹配（精确→去空行→省略→缩进容错→模糊）、
账本式 git 安全回滚（只回滚本会话 Agent 提交，保护用户提交不被误伤）、
确定性折叠与 LLM 摘要结合的上下文压缩、以及基于 tree-sitter 的 shell 命令语义分析。

五、Git 仓库地址
https://github.com/TkTK0302/Chisel_coder-agent