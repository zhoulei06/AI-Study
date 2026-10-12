# 2026 Agent Engineer 学习路线与学习计划

> **目标：从 7 年前端开发工程师，逐步转型为 Agent Application / Agent Engineer，并最终具备设计 Agent Harness、Agent Runtime 和 Coding Agent 的能力。**

---

# 一、最终学习目标

整个学习路线不是以“会多少框架”为目标，而是以：

> **能够从 0 构建一个可靠的 Agent Runtime，并理解 Harness 如何驱动 Agent 完成复杂任务。**

为最终目标。

最终能力结构：

```text
                         Agent Engineer
                              │
              ┌───────────────┼────────────────┐
              │               │                │
             LLM            Agent          Engineering
              │               │                │
       ┌──────┼──────┐   ┌────┼────┐      ┌────┼─────┐
       │      │      │   │    │    │      │    │     │
     Prompt  RAG   Model Loop Tool State  Python API  Docker
              │               │
              │          ┌────┼──────────┐
              │          │    │          │
             MCP       Plan  Memory   Reflection
                         │
                         ↓
                      Workflow
                         │
                         ↓
                    Multi-Agent
                         │
                         ↓
                      Harness
                         │
                         ↓
                   Agent Runtime
                         │
                         ↓
                    Coding Agent
                         │
                         ↓
                   Agent Platform
```

---

# 二、整体学习路线

建议按照下面 16 个阶段学习：

```text
Phase 1   Python + Agent 工程基础
    ↓
Phase 2   LLM API + Prompt + Context
    ↓
Phase 3   Structured Output
    ↓
Phase 4   Tool Calling
    ↓
Phase 5   Agent Loop
    ↓
Phase 6   State + Memory
    ↓
Phase 7   MCP
    ↓
Phase 8   RAG
    ↓
Phase 9   Workflow
    ↓
Phase 10  Planner / Executor
    ↓
Phase 11  Reflection / Self-Correction
    ↓
Phase 12  Multi-Agent
    ↓
Phase 13  Agent Framework
    ↓
Phase 14  Harness
    ↓
Phase 15  Agent Runtime / Coding Agent
    ↓
Phase 16  Agent Platform / Production
```

---

# 三、学习时间规划

如果你每周可以投入：

- 工作日：每天 1.5～2 小时
- 周末：每天 3～4 小时

大约：

> **15～20 小时 / 周**

建议按照 **9～12 个月**完成。

如果投入更多，可以压缩到 6～8 个月。

---

# 四、Phase 1：Python + Agent 工程基础

## 学习目标

你不需要把 Python 学成传统后端工程师水平。

重点掌握 Agent 开发所需要的：

```text
Python 基础语法
    ↓
类型系统
    ↓
async / await
    ↓
HTTP
    ↓
JSON
    ↓
Pydantic
    ↓
FastAPI
    ↓
pytest
    ↓
uv / pip
```

重点掌握：

- list / dict / tuple / set
- class
- dataclass
- typing
- Generic
- Protocol
- async / await
- asyncio
- exception
- decorator
- generator
- context manager
- package
- virtual environment
- HTTP Client
- Web API

### 学习资源

**官方 Python Tutorial**

[Python 官方 Tutorial](https://docs.python.org/3/tutorial/?utm_source=chatgpt.com)

Python 官方文档当前为 Python 3.14 系列，适合作为语言参考。

### 建议

不要花 2～3 个月学 Python。

你的目标是：

> **2～3 周达到能够独立阅读和编写 Agent Python 代码。**

---

# 五、Phase 2：LLM API + Prompt + Context

## 学习目标

理解：

```text
LLM
Model
Message
System / Developer
User
Assistant
Context
Token
Temperature
Structured Output
Streaming
```

重点理解：

```text
User Input
    ↓
Context Assembly
    ↓
Model
    ↓
Output
```

尤其要开始理解：

> **Agent 开发正在从 Prompt Engineering 逐渐转向 Context Engineering。**

Context Engineering 关注的不是一句 Prompt 怎么写，而是：

> 在模型执行某一步任务时，究竟应该把什么信息放进 Context。

Anthropic 对 Context Engineering 有比较系统的工程文章。

### 推荐资源

**Anthropic：Effective context engineering for AI agents**

[Anthropic Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents?utm_source=chatgpt.com)

**OpenAI Agents SDK**

[OpenAI Agents SDK](https://openai.github.io/openai-agents-python/?utm_source=chatgpt.com)

---

# 六、Phase 3：Structured Output

## 学习目标

理解：

```text
Natural Language Output
        ↓
Structured Output
        ↓
JSON Schema
        ↓
Pydantic
```

例如：

```python
class Task(BaseModel):
    title: str
    priority: int
    dependencies: list[str]
```

模型不只是：

```text
"帮你创建一个任务"
```

而是：

```json
{
  "title": "创建用户列表",
  "priority": 1,
  "dependencies": []
}
```

这对 Agent 非常重要，因为：

> Agent 的不同模块之间需要可靠的数据结构。

### 学习重点

- JSON Schema
- Pydantic
- Structured Outputs
- Schema Validation
- Tool 参数 Schema

### 推荐资源

**OpenAI Structured Outputs Samples**

[OpenAI Structured Outputs Samples](https://github.com/openai/openai-structured-outputs-samples?utm_source=chatgpt.com)

该项目包含 Structured Output、Tool Calling、Generative UI 等示例。

---

# 七、Phase 4：Tool Calling

这是 Agent 学习的第一个核心阶段。

## 学习目标

理解：

```text
LLM
 ↓
决定是否调用 Tool
 ↓
生成 Tool Call
 ↓
Application 执行 Tool
 ↓
Tool Result
 ↓
LLM
```

例如：

```text
User:
帮我查询今天北京天气

        ↓

LLM

        ↓

get_weather({
    city: "Beijing"
})

        ↓

Application

        ↓

Weather API

        ↓

Tool Result

        ↓

LLM

        ↓

今天北京……
```

## 必须掌握

- Function Calling
- Tool Schema
- Tool Result
- Parallel Tool Calling
- Tool Error
- Tool Permission
- Tool Timeout

---

# 八、Phase 5：自己实现 Agent Loop

这是整条路线中最重要的阶段之一。

**不要马上使用 LangGraph。**

先自己写。

核心：

```python
while True:

    response = model(messages)

    if response.tool_calls:

        for call in response.tool_calls:

            result = execute_tool(call)

            messages.append(result)

    else:
        return response
```

你需要真正理解：

```text
                Agent
                  │
                  ↓
                Model
                  │
             Tool Call?
              /       \
            Yes        No
             │          │
             ↓          ↓
           Tool       Final
             │
             ↓
         Tool Result
             │
             └────→ Model
```

### 这一阶段不要追求框架

建议：

```text
Python
+
LLM SDK
+
自己写 Agent Loop
```

### 最终项目

实现：

> **Mini ReAct Agent**

支持：

- calculator
- weather
- search
- file
- shell

---

# 九、Phase 6：State + Memory

Agent 一旦进入复杂任务，必须解决：

> Agent 当前进行到哪一步？

这就是 State。

例如：

```python
state = {
    "task": "...",
    "messages": [],
    "plan": [],
    "current_step": 2,
    "tool_results": [],
    "errors": []
}
```

进一步学习：

```text
State
 ↓
Checkpoint
 ↓
Session
 ↓
Short-term Memory
 ↓
Long-term Memory
```

需要区分：

| 概念      | 作用           |
| ------- | ------------ |
| Context | 当前给模型看的信息    |
| State   | Agent 当前运行状态 |
| Session | 一次/一组会话      |
| Memory  | 跨任务长期记忆      |
| RAG     | 外部知识检索       |

这几个概念非常容易混淆。

---

# 十、Phase 7：MCP

MCP 是 Agent 工具生态中非常重要的一层。

官方架构是：

```text
Host
 │
 ├── Client ─── MCP Server
 │                  │
 │                  ├── Tool
 │                  ├── Resource
 │                  └── Prompt
 │
 └── Client ─── MCP Server
```

MCP 官方架构采用 Host / Client / Server 模型，Server 可以提供 Tools、Resources、Prompts。

## 学习顺序

```text
MCP 是什么
 ↓
Host
 ↓
Client
 ↓
Server
 ↓
Tool
 ↓
Resource
 ↓
Prompt
 ↓
Transport
 ↓
MCP Server 开发
 ↓
MCP Client 开发
```

### 推荐资源

**MCP 官方 Specification**

[Model Context Protocol 官方架构文档](https://modelcontextprotocol.io/specification/2025-06-18/architecture?utm_source=chatgpt.com)

**MCP Python SDK**

[MCP Python SDK 官方文档](https://py.sdk.modelcontextprotocol.io/?utm_source=chatgpt.com)

官方 Python SDK 当前文档提供 MCP Server、Client 以及 stdio、Streamable HTTP、SSE 等能力。

**MCP Python Quickstart**

[MCP Python SDK Quickstart](https://py.sdk.modelcontextprotocol.io/get-started/?utm_source=chatgpt.com)

### 实战项目

自己开发：

```text
filesystem-mcp
git-mcp
project-search-mcp
```

最终让自己的 Agent：

```text
Agent
 ↓
MCP Client
 ↓
Filesystem MCP
 ↓
读取项目
```

---

# 十一、Phase 8：RAG

RAG 不要太早学习。

你先理解 Agent Loop、Tool、MCP，再学习 RAG，会更容易。

## 学习目标

理解：

```text
Documents
 ↓
Chunk
 ↓
Embedding
 ↓
Vector DB
 ↓
Retriever
 ↓
Context
 ↓
LLM
```

进一步学习：

```text
Naive RAG
 ↓
Hybrid Search
 ↓
Rerank
 ↓
Query Rewrite
 ↓
Context Compression
 ↓
Agentic RAG
```

重点不是“会一个向量数据库”。

而是理解：

> **RAG 本质上是 Agent Context 构建的一种方式。**

---

# 十二、Phase 9：Workflow

这是从：

> “简单 Agent”

走向：

> “复杂 Agent 系统”

的重要一步。

理解：

```text
Workflow ≠ Agent
```

Workflow：

```text
A → B → C → D
```

Agent：

```text
Agent
 ↓
自己决定
 ↓
A
 ↓
观察
 ↓
决定 B / C / D
```

LangGraph 官方文档也明确区分 Workflow 与 Agent：Workflow 通常具有预定义路径，而 Agent 会动态决定过程和工具使用。

## 学习模式

```text
Sequential
Parallel
Conditional
Router
Evaluator
Optimizer
Loop
Human-in-the-loop
```

### 推荐资源

**LangGraph Workflows and Agents**

[LangGraph Workflows and Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents?utm_source=chatgpt.com)

**Anthropic Building Effective Agents**

[Anthropic Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents?utm_source=chatgpt.com)

Anthropic 的核心建议之一就是优先使用简单、可组合的 Agent 模式，而不是一开始堆复杂框架。

---

# 十三、Phase 10：Planner / Executor

开始真正进入 Agent Architecture。

经典结构：

```text
                User
                 │
                 ↓
              Planner
                 │
        ┌────────┼────────┐
        ↓        ↓        ↓
      Task1    Task2    Task3
        │        │        │
        └────────┼────────┘
                 ↓
              Executor
                 │
                 ↓
               Tools
                 │
                 ↓
            Observation
                 │
                 ↓
              Planner
```

需要掌握：

- Task Decomposition
- Plan
- Re-plan
- Dependency
- Task Queue
- Executor
- Observation

### 实战

做一个：

> **Project Task Agent**

输入：

```text
给我的 Vue 项目增加一个用户管理模块
```

Agent 自动：

```text
1. 分析项目
2. 找路由
3. 找 API
4. 找组件规范
5. 制定计划
6. 执行任务
7. 检查结果
```

---

# 十四、Phase 11：Reflection / Self-Correction

这是 Coding Agent 非常重要的能力。

基本模式：

```text
Generate
   ↓
Execute
   ↓
Observe
   ↓
Evaluate
   ↓
Error?
  /   \
Yes    No
 │      │
 ↓      ↓
Fix    Finish
 │
 └──→ Execute
```

例如：

```text
Agent 修改代码
 ↓
运行 pnpm test
 ↓
测试失败
 ↓
分析错误
 ↓
修改代码
 ↓
重新测试
```

这就是：

> **Agentic Self-Correction**

---

# 十五、Phase 12：Multi-Agent

不要过早学习 Multi-Agent。

建议在：

```text
Tool Calling
Agent Loop
State
Workflow
Planner
Reflection
```

全部理解之后再学习。

## 核心模式

### Manager / Worker

```text
             Manager
            /   |   \
           /    |    \
       Research Code  Review
```

### Handoff

```text
Triage
  │
  ├──→ Research Agent
  │
  ├──→ Coding Agent
  │
  └──→ Review Agent
```

### 推荐资源

**OpenAI Agents SDK**

[OpenAI Agents SDK Agents](https://openai.github.io/openai-agents-python/agents/?utm_source=chatgpt.com)

OpenAI Agents SDK 当前提供 Agents、Tools、Handoffs、Guardrails、Sessions 等核心能力。

**AgentScope**

[AgentScope 官方文档](https://doc.agentscope.io/?utm_source=chatgpt.com)

AgentScope 当前文档覆盖 Agent、Workflow、Memory、MCP、Skill、State/Session、Plan、RAG、Tracing、Evaluation 等内容。

---

# 十六、Phase 13：Agent Framework

这个阶段才开始系统学习框架。

建议：

```text
                 Agent Framework
                       │
        ┌──────────────┼──────────────┐
        ↓              ↓              ↓
    LangGraph      AgentScope    OpenAI Agents
```

## 第一优先级

### LangGraph

学习：

```text
State
Node
Edge
Graph
Checkpoint
Persistence
Streaming
Interrupt
Human-in-the-loop
```

LangGraph 的 Human-in-the-loop 能够通过 interrupt 暂停 Agent，并利用 checkpoint 保存状态后恢复执行。

### 第二优先级

### OpenAI Agents SDK

学习：

```text
Agent
Runner
Tools
Handoffs
Guardrails
Sessions
Tracing
```

其 Runner 会管理 turns、tools、guardrails、handoffs 和 sessions。

### 第三优先级

### AgentScope

重点理解：

```text
Agent
Pipeline
Workflow
Multi-Agent
Memory
MCP
Skill
Plan
RAG
Tracing
Evaluation
```

---

# 十七、Phase 14：Harness

这是你的核心目标之一。

## Harness 学习模型

不要把 Harness 理解成某个具体框架。

把它理解成：

> **围绕 Model 建立的一套 Agent Runtime 控制系统。**

核心：

```text
                    Harness
                       │
       ┌───────────────┼────────────────┐
       ↓               ↓                ↓
    Context          Control          Runtime
       │               │                │
       ↓               ↓                ↓
   Memory           Loop             Tools
   Skills           State            MCP
   RAG              Policy           Sandbox
   Prompt           Guardrail        Filesystem
```

进一步：

```text
Harness
 │
 ├── Context Management
 ├── Agent Loop
 ├── State
 ├── Memory
 ├── Tool Runtime
 ├── MCP
 ├── Skills
 ├── Sandbox
 ├── Permissions
 ├── Guardrails
 ├── Checkpoint
 ├── Retry
 ├── Timeout
 ├── Human Approval
 ├── Observability
 └── Evaluation
```

Anthropic 当前工程文章中已经把 Harness Design、长运行 Agent、Coding Agent、安全隔离、Tool Use、Skills、Context Engineering 等作为重要工程方向。

### 推荐重点阅读

**Anthropic Engineering**

[Anthropic Engineering](https://www.anthropic.com/engineering?utm_source=chatgpt.com)

重点关注：

- Harness design for long-running application development
- Effective harnesses for long-running agents
- Code execution with MCP
- Writing effective tools for agents
- Effective context engineering
- Agent Skills
- Claude Code best practices

---

# 十八、Phase 15：Coding Agent

这是最推荐你作为主线项目来做的。

最终目标：

> **自己实现一个简化版 Cursor / Claude Code / Codex。**

架构：

```text
                         Coding Agent
                              │
                           Harness
                              │
         ┌────────────────────┼────────────────────┐
         ↓                    ↓                    ↓
      Planner              Context              Tools
         │                    │                    │
         │              ┌─────┼─────┐       ┌─────┼─────┐
         │              ↓     ↓     ↓       ↓     ↓     ↓
         │            Files  Git   Memory   Shell  MCP  Search
         │
         ↓
      Executor
         │
         ↓
      Observe
         │
         ↓
      Evaluate
         │
       ┌─┴─┐
       ↓   ↓
     Fix  Finish
       │
       └──────→ Executor
```

---

# 十九、Coding Agent 项目分阶段开发

## V1：Chat Agent

```text
LLM
+
Prompt
+
Streaming
```

目标：

能够进行正常对话。

---

## V2：Tool Agent

增加：

```text
Calculator
Search
Weather
```

---

## V3：File Agent

增加：

```text
list_files
read_file
write_file
search_code
```

---

## V4：Coding Agent

增加：

```text
shell
git
npm
pnpm
pytest
```

Agent 能：

```text
读代码
 ↓
改代码
 ↓
运行测试
 ↓
分析错误
 ↓
修改
```

---

## V5：Planner

增加：

```text
Plan
 ↓
Task Queue
 ↓
Executor
```

---

## V6：Memory

增加：

```text
Project Memory
Task Memory
User Preference
```

---

## V7：MCP

让工具变成：

```text
MCP Client
    ↓
MCP Server
    ↓
Git
Filesystem
Database
Search
```

---

## V8：Human-in-the-loop

危险操作：

```text
rm
git push
数据库修改
生产部署
```

必须：

```text
Agent
 ↓
Request Approval
 ↓
Human
 ↓
Approve / Reject
```

---

## V9：Sandbox

把 Shell 执行放到隔离环境。

```text
Agent
 ↓
Sandbox
 ↓
Container
 ↓
Shell
```

---

## V10：Observability

记录：

```text
Trace
 ├── LLM
 ├── Tool
 ├── MCP
 ├── Planner
 ├── State
 └── Error
```

OpenAI Agents SDK 的 tracing 已经覆盖 Agent、LLM generation、Tool、Handoff、Guardrail 等执行过程，是理解 Agent Observability 很好的参考。

---

# 二十、Phase 16：Agent Runtime / Platform

当 Coding Agent 做到 V10，你就可以进一步学习 Agent Runtime。

重点：

```text
Agent Runtime
      │
      ├── Scheduler
      ├── Queue
      ├── Worker
      ├── State Store
      ├── Memory
      ├── Tool Runtime
      ├── Sandbox
      ├── Permission
      ├── Observability
      ├── Evaluation
      └── Model Router
```

进一步学习：

```text
Agent Runtime
      ↓
Agent Platform
      ↓
Agent Infrastructure
```

---

# 二十一、Model Serving

这个阶段不需要一开始深入。

但是作为 Agent Engineer，需要理解：

```text
Agent
 ↓
Model API
 ↓
OpenAI Compatible API
 ↓
vLLM
 ↓
GPU
```

重点理解：

- OpenAI Compatible API
- Streaming
- Tool Calling
- Model Routing
- Model Gateway
- Token
- Context Window
- KV Cache
- Batch
- GPU

### 推荐资源

**vLLM OpenAI-Compatible Server**

[vLLM 官方 OpenAI-Compatible Server 文档](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/?utm_source=chatgpt.com)

vLLM 当前提供 OpenAI-compatible 的 Completions、Chat、Responses、Embeddings 等接口，并支持通过 `vllm serve` 启动服务。

---

# 二十二、每周学习方法

不要采用：

```text
看视频
 ↓
看文档
 ↓
看博客
 ↓
继续看视频
```

推荐：

```text
20% 理论
30% 阅读官方文档
50% Coding
```

例如一周：

### 周一

理论：

```text
Agent Loop
```

### 周二

阅读：

```text
OpenAI / Anthropic 官方文档
```

### 周三

自己实现：

```text
Agent Loop
```

### 周四

增加：

```text
Tool Calling
```

### 周五

Debug：

```text
Tool Error
Timeout
Retry
```

### 周六

做项目。

### 周日

总结：

```text
我今天到底理解了什么？
```

---

# 二十三、每个阶段必须有一个输出

不要“学完了就算”。

要求：

| 阶段                | 必须产出                  |
| ----------------- | --------------------- |
| Python            | Python Agent Demo     |
| LLM               | Chat API              |
| Structured Output | JSON Agent            |
| Tool Calling      | Tool Agent            |
| Agent Loop        | Mini ReAct            |
| State             | Stateful Agent        |
| Memory            | Memory Agent          |
| MCP               | MCP Server            |
| RAG               | Knowledge Agent       |
| Workflow          | Workflow Agent        |
| Planner           | Planner Agent         |
| Reflection        | Self-Correction Agent |
| Multi-Agent       | Multi-Agent System    |
| Framework         | LangGraph Agent       |
| Harness           | Mini Agent Runtime    |
| Coding Agent      | Coding Agent          |
| Runtime           | Agent Runtime         |

---

# 二十四、推荐你建立一个 GitHub 学习仓库

建议：

```text
agent-engineering-learning/
│
├── 01-python/
│
├── 02-llm/
│
├── 03-structured-output/
│
├── 04-tool-calling/
│
├── 05-agent-loop/
│
├── 06-state/
│
├── 07-memory/
│
├── 08-mcp/
│
├── 09-rag/
│
├── 10-workflow/
│
├── 11-planner/
│
├── 12-reflection/
│
├── 13-multi-agent/
│
├── 14-langgraph/
│
├── 15-agentscope/
│
├── 16-harness/
│
├── 17-coding-agent/
│
└── 18-agent-runtime/
```

每一个目录：

```text
README.md
src/
tests/
examples/
notes/
```

---

# 二十五、最终项目：Mini Coding Agent

最终建议做成：

```text
                   Mini Coding Agent
                          │
                          ↓
                      User Task
                          │
                          ↓
                       Planner
                          │
                    Task Queue
                          │
                          ↓
                       Executor
                          │
            ┌─────────────┼─────────────┐
            ↓             ↓             ↓
         Filesystem      Git          Shell
            │             │             │
            └─────────────┼─────────────┘
                          ↓
                       Observe
                          │
                          ↓
                       Evaluate
                          │
                       ┌──┴──┐
                       ↓     ↓
                     Error  Success
                       │       │
                       ↓       ↓
                      Fix    Finish
                       │
                       └──→ Executor
```

最终支持：

```text
✓ 文件读取
✓ 文件搜索
✓ 文件修改
✓ Shell
✓ Git
✓ 测试
✓ Plan
✓ State
✓ Memory
✓ MCP
✓ Sandbox
✓ Human Approval
✓ Retry
✓ Reflection
✓ Streaming
✓ Tracing
✓ Evaluation
```

做到这里，你就不再只是：

> “会调用大模型 API 的前端工程师”。

而是已经具备：

> **Agent Application + Agent Runtime + Harness 的完整工程能力。**

---

# 二十六、推荐资源总表

| 方向                  | 推荐资源                                           |   优先级 |
| ------------------- | ---------------------------------------------- | ----: |
| Python              | Python 官方 Tutorial                             | ⭐⭐⭐⭐⭐ |
| LLM                 | OpenAI / Anthropic 官方文档                        | ⭐⭐⭐⭐⭐ |
| Prompt              | Anthropic Engineering                          |  ⭐⭐⭐⭐ |
| Context Engineering | Anthropic Context Engineering                  | ⭐⭐⭐⭐⭐ |
| Structured Output   | OpenAI Structured Outputs                      | ⭐⭐⭐⭐⭐ |
| Tool Calling        | OpenAI Agents / API Docs                       | ⭐⭐⭐⭐⭐ |
| Agent               | OpenAI Agents SDK                              |  ⭐⭐⭐⭐ |
| Agent Architecture  | Anthropic Building Effective Agents            | ⭐⭐⭐⭐⭐ |
| MCP                 | MCP 官方 Specification                           | ⭐⭐⭐⭐⭐ |
| MCP Python          | MCP Python SDK                                 | ⭐⭐⭐⭐⭐ |
| RAG                 | LangChain / LlamaIndex 文档                      |  ⭐⭐⭐⭐ |
| Workflow            | LangGraph                                      | ⭐⭐⭐⭐⭐ |
| Multi-Agent         | AgentScope                                     |  ⭐⭐⭐⭐ |
| Harness             | Anthropic Engineering                          | ⭐⭐⭐⭐⭐ |
| Coding Agent        | Claude Code / Agent Engineering Articles       | ⭐⭐⭐⭐⭐ |
| Runtime             | LangGraph / Agents SDK / Anthropic Engineering | ⭐⭐⭐⭐⭐ |
| Model Serving       | vLLM                                           |   ⭐⭐⭐ |
| Observability       | Agents SDK Tracing / LangSmith 等               |  ⭐⭐⭐⭐ |
| Evaluation          | AgentScope Evaluation / Agent SDK Testing      |  ⭐⭐⭐⭐ |

---

# 二十七、最重要的阅读顺序

资源很多，不要全部同时看。

我建议按照这个顺序：

```text
① Python Tutorial
       ↓
② Anthropic Building Effective Agents
       ↓
③ Tool Calling
       ↓
④ 自己实现 Agent Loop
       ↓
⑤ MCP 官方文档
       ↓
⑥ MCP Python SDK
       ↓
⑦ LangGraph Workflow / Agent
       ↓
⑧ OpenAI Agents SDK
       ↓
⑨ AgentScope
       ↓
⑩ Anthropic Engineering
       ↓
⑪ Harness / Coding Agent
       ↓
⑫ vLLM / Runtime / Evaluation
```

尤其推荐先读 Anthropic 的 Agent 指南，因为它强调简单、可组合的 Agent 模式，而不是先堆框架；这与你这条“先理解原理，再学习框架”的路线非常匹配。

---

# 二十八、你的学习重点比例

结合你的前端背景，我建议：

```text
Agent 原理             20%
Python                 15%
LLM / Context          10%
Tool / MCP             15%
RAG                    10%
Workflow / Planner     10%
Harness / Runtime      15%
Model / Infrastructure 5%
```

不要把大量时间花在：

```text
机器学习数学
Transformer 从零实现
模型训练
Fine-tuning
GPU Kernel
CUDA
```

这些不是你当前转型 Agent Engineer 的最高 ROI。

---

# 二十九、最终能力验收标准

当你完成这条路线后，应该能够回答下面这些问题：

### Agent

- Agent 和 Workflow 有什么区别？
- ReAct 是什么？
- Agent Loop 怎么实现？
- Tool Calling 为什么需要 Schema？
- Agent 如何知道什么时候停止？

### State

- Context 和 State 有什么区别？
- Session 和 Memory 有什么区别？
- Checkpoint 为什么重要？

### MCP

- Host / Client / Server 什么区别？
- Tool / Resource / Prompt 什么区别？
- MCP 和 Function Calling 什么关系？
- MCP 为什么能够解决工具生态问题？

### Workflow

- Sequential / Parallel / Router 怎么设计？
- Planner / Executor 怎么设计？
- Reflection 应该什么时候使用？
- Multi-Agent 什么时候真的有价值？

### Harness

- Harness 到底是什么？
- Agent Runtime 做什么？
- Tool Permission 怎么设计？
- Sandbox 为什么重要？
- Long-running Agent 怎么恢复？
- Agent 怎么做到 Human-in-the-loop？
- Agent Trace 应该记录什么？

### Coding Agent

- Coding Agent 如何读取项目？
- 如何决定修改哪些文件？
- 如何运行测试？
- 测试失败后怎么自动修复？
- 如何限制 Shell 权限？
- 如何让 Agent 支持 MCP？
- 如何让 Agent 长时间执行？
- 如何保存任务状态？
- 如何进行 Evaluation？

如果这些问题你都能够独立回答，并且能够自己写出一个 Mini Coding Agent，那么基本就已经跨过了：

> **“学习 Agent” → “具备 Agent 工程能力”**

这个阶段。

---

# 三十、最终路线总结

你的路线最终可以压缩成一句话：

```text
Python
  ↓
LLM
  ↓
Structured Output
  ↓
Tool Calling
  ↓
Agent Loop
  ↓
State / Memory
  ↓
MCP
  ↓
RAG
  ↓
Workflow
  ↓
Planner / Executor
  ↓
Reflection
  ↓
Multi-Agent
  ↓
Agent Framework
  ↓
Harness
  ↓
Agent Runtime
  ↓
Coding Agent
  ↓
Agent Platform
```

**其中最关键的三个节点是：**

```text
Tool Calling
     ↓
Agent Loop
     ↓
Harness
```

因为：

> **Tool Calling 让模型“能做事”；**

> **Agent Loop 让模型“能够连续做事”；**

> **Harness 让 Agent “能够可靠、可控、可恢复地完成复杂任务”。**

这三个真正打通之后，后面的 MCP、Workflow、Planner、Reflection、Multi-Agent、Coding Agent 都是在这个基础上不断增加能力。

最后，结合你的前端背景，我最建议你把 **“Mini Coding Agent”作为贯穿整个学习过程的主项目**，而不是每个阶段做完全无关的 Demo。这样到后期你会得到一个真正可以放到简历和 GitHub 上展示的 Agent 工程项目。
