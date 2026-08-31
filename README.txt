Chisel —— 编程智能体（Coding Agent）
像凿子一样精准地改写代码，而不是推倒重来。

一、项目简介
基于大语言模型原生 tool calling 的终端编程智能体：用户下达任务，agent 自主读写文件、
执行命令、反复调用工具直到完成。核心逻辑全部自写，不使用任何 agent 框架/SDK。
提供 CLI 命令行和桌面应用两种使用方式。

二、运行方式
1. pip install -r requirements.txt
2. 项目根目录 .env 写 LLM_API_KEY=sk-xxx（已 gitignore；也可用环境变量）
3. 命令行：python agent.py "任务描述"
4. 桌面应用：python desktop/main.py
   常用参数：--model / --base-url（切换模型）、--workspace、--sandbox host|docker|auto

三、特色功能
1. 原生 tool calling 主循环 + 显式规划（plan 拆解子任务，每轮注入上下文）
2. 工具链：bash / read_file / write_file / edit_file（SEARCH/REPLACE 多策略编辑，
   支持 ... 省略 / 缩进容错 / 模糊匹配）/ git（自动快照 + 账本式 undo）/ code_navigate
   （AST 符号导航）/ web_fetch / web_search / rag_search（混合检索）
3. 执行环境：Docker 沙盒（默认优先，失败降级宿主）+ 交互式终端
4. 记忆与上下文：MEMORY.md 持久偏好 + 递归分治上下文压缩 + 长输出截断保存到文件
5. 闭环反馈：死循环检测（连续重复调用/错误注入警告或中止）+ 自动 lint + 自动回滚
6. 人机协作：4 级风险确认 + always_allow 缓存 + ask_user 主动提问 + 审计日志
7. 双模式规划：single 模式（小项目，计划+审批+执行）和 multi 模式（大项目，多 Agent 协作）
8. 桌面应用：项目管理 + 对话历史 + 文件上传 + 流式输出

四、Git 仓库地址
https://github.com/TkTK0302/Chisel_coder-agent
