Chisel —— 编程智能体（Coding Agent）
像凿子一样精准地改写代码，而不是推倒重来。

一、项目简介
这是一个基于大语言模型的编程智能体：用户下达一个编程任务，agent 自主地读写文件、
执行命令，反复调用工具直到任务完成。功能对标简化版 Claude Code / OpenCode，但不
依赖任何 agent 框架或 SDK，核心逻辑全部自行实现。

二、运行方式
1. 安装依赖：pip install -r requirements.txt（仅 openai 客户端库）
2. 配置 API key：项目根目录新建 .env 文件，写入 LLM_API_KEY=sk-xxx
   （.env 已加入 .gitignore，不会入库；也可用环境变量 LLM_API_KEY）
3. 运行：python agent.py "写一个计算斐波那契数列的 Python 脚本"
   可选参数：--model、--base-url（切换 DeepSeek/其它 OpenAI 兼容模型）、
   --memory "我喜欢用 Python"（记住用户偏好）、--workspace（指定工作目录）

三、特色功能
1. 原生 tool calling：用模型厂商的 function calling 接口，非文本解析，稳健且语义清晰。
2. 四个本地工具：bash（执行命令）、read_file、write_file、edit_file（SEARCH/REPLACE
   精确替换，只改第一处匹配，避免整文件重写误删）。
3. 持久记忆：--memory 把用户偏好写入 MEMORY.md，下次启动自动注入 system prompt。
4. 危险命令确认：rm -rf、git push 等执行前暂停询问，体现安全设计。
5. 上下文管理：估算 token 超限时自动丢弃最旧对话，保留 system 与最近内容。
6. 错误处理：LLM 调用指数退避重试；工具参数坏 JSON、edit_file 匹配失败均回填给模型，
   让它读取真实内容后自纠。

四、核心设计说明
- 对话历史：用 messages 列表管理，system + user 起始，每轮追加 assistant（含 tool_calls）
  与 tool 结果，形成完整的 agent 循环轨迹。
- 循环终止条件：tool_choice="auto"，模型不调工具只返回文本即视为任务完成，另设
  max_steps 兜底防死循环。
- 模型输出解析：解析 message.tool_calls 的 name/arguments，对坏 JSON 做容错回填。
- 编辑策略：借鉴 Aider 的 SEARCH/REPLACE，要求 original_lines 逐字符精确匹配现有内容。

五、Git 仓库地址
https://github.com/TkTK0302/Chisel_coder-agent
