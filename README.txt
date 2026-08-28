Chisel —— 编程智能体（Coding Agent）
像凿子一样精准地改写代码，而不是推倒重来。

一、项目简介
基于大语言模型原生 tool calling 的终端编程智能体：用户下达任务，agent 自主读写文件、
执行命令、反复调用工具直到完成。对标简化版 Claude Code / OpenCode，核心逻辑全部自写，
不使用任何 agent 框架/SDK。

二、运行方式
1. pip install -r requirements.txt
2. 项目根目录 .env 写 LLM_API_KEY=sk-xxx（已 gitignore；也可用环境变量）
3. python agent.py "任务描述"
   常用参数：--model / --base-url（切换 OpenAI 兼容模型）、--workspace、
   --sandbox host|docker|auto（默认 auto：优先 Docker 沙盒，失败自动降级宿主机）、
   --memory "偏好"（持久记忆）

三、特色功能
1. 原生 tool calling 主循环 + 显式规划（plan 工具拆解子任务并跟踪进度，每轮注入上下文）
2. 工具链：bash / read_file / write_file / edit_file（SEARCH/REPLACE 多策略精确编辑，
   支持 ... 省略与缩进容错）/ git（自动快照 + 账本式安全回滚 undo）/ code_navigate
   （AST 符号导航：定义/引用/符号表）/ web_fetch（查阅文档）
3. 执行环境：Docker 沙盒（默认执行命令，失败自动降级）+ 交互式终端（启动/查看/终止
   长驻进程如 web server）
4. 记忆与上下文：MEMORY.md 持久偏好 + rag_search 代码库混合检索（BM25 必开 + 向量可选，
   RRF 融合，中文大词兜底）+ 超限自动压缩（确定性折叠 + LLM 摘要）
5. 闭环反馈：自我调试（跑测试→发现报错→修复→验证）+ 死循环检测（连续重复调用/连续错误
   自动注入警告或中止）+ 长输出头尾截断
6. 人机协作：危险命令确认（rm -rf / git push / 递归删除 等拦截）+ ask_user 主动提问

四、Git 仓库地址
https://github.com/TkTK0302/Chisel_coder-agent
