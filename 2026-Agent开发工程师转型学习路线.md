# 2026 Agent 开发工程师转型学习路线

> 面向：7 年经验前端开发工程师  
> 目标：从 Frontend Engineer 转型为 **AI Agent Full-Stack Engineer / Agent Systems Engineer**  
> 周期：约 24 周  
> 建议投入：工作日每天约 2 小时，周末 3～4 小时  
> 核心原则：以 2026 年主流 Agent 工程实践为准，不以历史上接触过的框架或概念为学习依据。

---

## 1. 学习目标

最终不是成为“会调用大模型 API 的前端工程师”，而是具备完整的 Agent 系统设计、开发、调试、评测和生产落地能力。

目标能力：

```text
LLM
 ↓
Structured Output / Tool Calling
 ↓
Agent Loop
 ↓
RAG
 ↓
MCP
 ↓
Workflow / Agent
 ↓
Agent Runtime
 ↓
Multi-Agent
 ↓
Context Engineering / Memory / Skills
 ↓
Harness / Sandbox
 ↓
Evaluation
 ↓
Observability / Security / Reliability
 ↓
Production Agent System
```

最终建议定位：

> **AI Agent Full-Stack Engineer → Agent Systems Engineer**

---

# 2. 2026 年主流技术路线

当前学习主线建议围绕以下技术方向：

| 层级 | 核心知识 | 建议程度 |
|---|---|---|
| 编程基础 | Python / TypeScript / Async | 必学 |
| LLM | API、Token、Context、Structured Output | 必学 |
| Agent 基础 | Tool Calling、Agent Loop、ReAct | 必学 |
| RAG | Retrieval、Rerank、Hybrid Search、Agentic RAG | 必学 |
| MCP | Tool、Resource、Prompt、Transport、Security | 必学 |
| Agent Framework | LangChain | 建议掌握 |
| Agent Runtime | LangGraph | 重点掌握 |
| Agent SDK | OpenAI Agents SDK | 建议掌握 |
| Agent Harness | Context、Subagents、Filesystem、Sandbox、Planning | 重点掌握 |
| Multi-Agent | Supervisor、Handoff、Routing、Parallel | 必学 |
| Memory | Short-term / Long-term Memory | 必学 |
| Evaluation | Dataset、Trajectory、LLM Judge、Regression | 必学 |
| Observability | Trace、Span、Token、Latency、Cost | 必学 |
| Security | Prompt Injection、Tool Permission、Sandbox | 必学 |
| Deployment | FastAPI、Docker、Redis、PostgreSQL | 必学 |
| Vector DB | pgvector / Milvus 等 | 掌握原理和一种即可 |
| 模型训练 | PyTorch / RLHF / CUDA | 非主线 |

---

# 3. 为什么不建议走“模型训练路线”

如果目标是 Agent Engineer，不建议把大量时间投入：

- CUDA
- Transformer 源码
- PyTorch 深度学习训练
- 分布式训练
- RLHF
- 模型预训练
- 模型量化底层实现

需要理解：

```text
Transformer
Attention
Token
Embedding
Inference
Fine-tuning
Quantization
```

但不需要把它们作为主线。

只有未来准备转向：

> LLM Engineer / Model Engineer / AI Infra Engineer

时，再深入模型训练体系。

---

# 4. 你的已有优势

7 年前端经验不是需要舍弃的东西，而是 Agent 转型的优势。

已有能力可以直接复用：

```text
TypeScript / JavaScript
Vue / React
Node.js
HTTP / SSE / WebSocket
API 设计
工程化
Monorepo
组件化
性能优化
Observability
前端 AI UI
```

尤其适合发展：

```text
Agent Backend
+
Agent Frontend
+
AI Chat UI
+
Streaming UI
+
Human-in-the-loop UI
+
Agent State Visualization
+
Tool Call Visualization
+
Artifact UI
```

因此不建议把自己定位成纯 Python 后端，而建议定位成：

> **AI Agent Full-Stack Engineer**

---

# 5. 24 周学习计划总览

| 周期 | 学习主题 | 核心目标 | 主要产出 |
|---|---|---|---|
| 第 1～2 周 | Python + FastAPI | 能开发 Agent Backend | Python API 服务 |
| 第 3～4 周 | LLM 基础 | 掌握模型调用和上下文 | LLM Chat |
| 第 5～6 周 | Tool Calling + Agent Loop | 独立实现单 Agent | Tool Agent |
| 第 7～9 周 | RAG | 构建知识库 Agent | 企业知识库 Agent |
| 第 10～11 周 | MCP | 能开发 MCP Server / Client | MCP Server |
| 第 12～14 周 | Workflow / Agent | 理解 Agent 架构选择 | Workflow Agent |
| 第 15～18 周 | LangGraph | 掌握 Agent Runtime | Stateful Agent |
| 第 19～21 周 | Multi-Agent | 构建多 Agent 系统 | Supervisor Agent |
| 第 22～23 周 | Harness / Context / Skills / Sandbox | 构建复杂 Agent | Coding Agent |
| 第 24 周 | Evaluation / Observability / Security | 达到生产工程能力 | Agent Platform |

---

# 6. Phase 1：Python + FastAPI

## 时间

第 1～2 周

## 学习目标

不需要把 Python 学成专家，目标是：

> 能读懂 Agent 框架源码，并能写 Agent Backend。

## Python 必学

```text
基础语法
类型系统
typing
async / await
装饰器
异常处理
模块
虚拟环境
pytest
uv
```

重点：

### async / await

Agent 后端大量操作都是异步：

```python
async def agent():
    result = await model.invoke(...)
```

### Pydantic

重点学习：

```text
BaseModel
Field
JSON Schema
model_dump
Validation
Structured Data
```

### FastAPI

重点：

```text
REST API
Streaming Response
SSE
Dependency Injection
Pydantic
Async
Middleware
```

## 学习地址

- Python 官方教程：https://docs.python.org/3/tutorial/
- Pydantic：https://docs.pydantic.dev/latest/
- FastAPI：https://fastapi.tiangolo.com/
- uv：https://docs.astral.sh/uv/
- pytest：https://docs.pytest.org/

## 实战

开发：

```text
POST /chat
POST /stream
GET /sessions
GET /messages
```

最终：

```text
Vue / React
    ↓
SSE
    ↓
FastAPI
    ↓
LLM
```

---

# 7. Phase 2：LLM 基础

## 时间

第 3～4 周

## 学习内容

```text
LLM API
Messages
System Prompt
User Message
Assistant Message
Token
Context Window
Temperature
Structured Output
JSON Schema
Function Calling
Streaming
Multimodal
Model Selection
Cost
Latency
```

## 必须理解的基本链路

```text
User
 ↓
Prompt
 ↓
LLM
 ↓
Output
```

以及 Agent 的基础链路：

```text
User
 ↓
LLM
 ↓
Tool Call
 ↓
Tool Execute
 ↓
Tool Result
 ↓
LLM
 ↓
Final Answer
```

## 学习地址

- OpenAI Developer：https://platform.openai.com/docs/
- OpenAI API Quickstart：https://platform.openai.com/docs/quickstart
- Anthropic Docs：https://docs.anthropic.com/
- Google Gemini API：https://ai.google.dev/gemini-api/docs
- Qwen：https://qwen.readthedocs.io/

建议至少理解：

> 一个模型 API 如何调用、如何 Streaming、如何 Tool Calling、如何输出结构化数据。

不要一开始绑定单一模型厂商。

---

# 8. Phase 3：Tool Calling + Agent Loop

## 时间

第 5～6 周

这是 Agent 工程的第一个核心阶段。

## 必学

```text
Tool Schema
Function Calling
Structured Output
Agent Loop
Tool Result
ReAct
Stop Condition
Iteration Limit
Retry
Error Handling
Human Approval
Tool Permission
```

基本模型：

```text
LLM
 ↓
判断是否需要 Tool
 ↓
Tool Call
 ↓
执行 Tool
 ↓
Tool Result
 ↓
LLM
 ↓
继续执行 / Final Answer
```

## 实战

开发一个：

> AI 工作助手

Tools：

```text
calculator
weather
search
HTTP API
database
file
```

要求：

- Tool Schema
- Tool 参数校验
- Tool 错误处理
- Tool 超时
- Retry
- 最大循环次数
- 人工确认危险操作

---

# 9. Phase 4：RAG

## 时间

第 7～9 周

RAG 是企业 Agent 的基础能力。

## 第一层：RAG 基础

```text
Document
 ↓
Parser
 ↓
Chunking
 ↓
Embedding
 ↓
Vector Store
 ↓
Retrieval
 ↓
Context
 ↓
LLM
```

重点：

```text
Chunk
Embedding
Vector
Similarity
TopK
Metadata
Retrieval
```

## 第二层：Advanced RAG

必须学习：

```text
Query Rewrite
Multi Query
Hybrid Search
Rerank
Metadata Filter
Parent Document
Context Compression
```

## 第三层：Agentic RAG

理解：

```text
User Question
 ↓
Agent
 ↓
判断是否需要 Retrieval
 ↓
Search
 ↓
Grade Documents
 ↓
Rewrite Query
 ↓
再次 Search
 ↓
Answer
```

## 企业 RAG

必须考虑：

```text
权限
多租户
增量索引
数据更新
引用
召回率
准确率
RAG Evaluation
```

## 学习地址

- LlamaIndex：https://docs.llamaindex.ai/
- LangChain Retrieval：https://docs.langchain.com/oss/python/langchain/retrieval
- LangGraph Agentic RAG：https://docs.langchain.com/oss/python/langgraph/agentic-rag
- Milvus：https://milvus.io/docs
- pgvector：https://github.com/pgvector/pgvector

建议：

> Vector DB 学一种即可，不要同时深入 Milvus、Pinecone、Weaviate、Chroma 等多个产品。

---

# 10. Phase 5：MCP

## 时间

第 10～11 周

MCP 是当前 Agent 工具和上下文连接的重要标准。

## 核心模型

```text
Host
 │
MCP Client
 │
 ├──────────── MCP Server A
 │                ├─ Tools
 │                ├─ Resources
 │                └─ Prompts
 │
 └──────────── MCP Server B
                  ├─ Tools
                  ├─ Resources
                  └─ Prompts
```

## 必须理解

```text
MCP Protocol
JSON-RPC
Lifecycle
Capability
Session
Transport
Tool
Resource
Prompt
Client
Server
Host
```

## MCP 安全

必须学习：

```text
Authentication
Authorization
Least Privilege
Credential Isolation
Tool Approval
Prompt Injection
Data Exfiltration
Server Trust
```

## MCP 实战

开发：

> Frontend Development MCP Server

例如：

```text
get_project_info
search_code
read_file
search_component
get_api
search_docs
get_git_diff
```

架构：

```text
Cursor / Claude Code / Agent
            ↓
         MCP Client
            ↓
Frontend MCP Server
            ↓
Code / API / Docs / Git
```

## 学习地址

- MCP 官方文档：https://modelcontextprotocol.io/
- MCP Specification：https://modelcontextprotocol.io/specification/
- MCP Python SDK：https://github.com/modelcontextprotocol/python-sdk
- MCP TypeScript SDK：https://github.com/modelcontextprotocol/typescript-sdk
- OpenAI Agents MCP：https://openai.github.io/openai-agents-python/mcp/

---

# 11. Phase 6：Workflow vs Agent

## 时间

第 12～14 周

这是 Agent 架构设计最重要的概念之一。

## Workflow

路径基本确定：

```text
用户
 ↓
分类
 ↓
查询
 ↓
生成
 ↓
审核
 ↓
发送
```

## Agent

路径动态：

```text
用户
 ↓
LLM
 ↓
决定下一步
 ↓
Tool
 ↓
观察结果
 ↓
继续决定
 ↓
Tool
 ↓
Final
```

核心原则：

> 能用确定性 Workflow 解决的问题，不要为了“Agent 化”而强行使用 Agent。

## 学习

```text
Sequential Workflow
Parallel Workflow
Routing
Conditional Branch
Loop
Human-in-the-loop
Agent Loop
```

## 学习地址

LangGraph Workflows & Agents：

https://docs.langchain.com/oss/python/langgraph/workflows-agents

---

# 12. Phase 7：LangChain

## 时间

第 12～14 周穿插学习

LangChain 适合快速构建 Agent 和理解：

```text
Model
Tool
Agent
Middleware
Structured Output
Integration
```

当前 LangChain 官方定位：

> Agent framework

LangChain 的 Agent 架构构建在 LangGraph 之上，因此可以先用 LangChain 快速实现，再深入 LangGraph 理解底层运行机制。

## 学习地址

- LangChain：https://docs.langchain.com/oss/python/langchain/overview
- LangChain Agents：https://docs.langchain.com/oss/python/langchain/agents

---

# 13. Phase 8：LangGraph

## 时间

第 15～18 周

这是整个学习路线的重点。

LangGraph 当前定位为：

> Low-level Agent orchestration framework / runtime

重点不是 Prompt，而是：

```text
State
Node
Edge
Checkpoint
Persistence
Streaming
Human-in-the-loop
Durable Execution
Memory
Subgraph
```

## 核心模型

```text
                 State
                   │
        ┌──────────┼──────────┐
        ↓          ↓          ↓
      Node A     Node B     Node C
        │          │          │
        └──────────┼──────────┘
                   ↓
                 State
```

LangGraph 的三个核心抽象：

```text
State
Node
Edge
```

## 必学

```text
StateGraph
MessagesState
START
END
Node
Edge
Conditional Edge
Command
Interrupt
Checkpoint
Memory
Subgraph
Streaming
Persistence
Human-in-the-loop
```

## 实战

实现：

> Customer Support Agent

```text
Email
 ↓
Classify
 ↓
Search Knowledge Base
 ↓
Analyze
 ↓
Draft Reply
 ↓
Risk Check
 ↓
Human Approval
 ↓
Send
```

## 学习地址

- LangGraph：https://docs.langchain.com/oss/python/langgraph/overview
- Quickstart：https://docs.langchain.com/oss/python/langgraph/quickstart
- Graph API：https://docs.langchain.com/oss/python/langgraph/graph-api
- Thinking in LangGraph：https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph
- Workflows & Agents：https://docs.langchain.com/oss/python/langgraph/workflows-agents

---

# 14. Phase 9：Multi-Agent

## 时间

第 19～21 周

不要一开始就 Multi-Agent。

正确顺序：

```text
Single Agent
 ↓
Workflow
 ↓
Stateful Agent
 ↓
Multi-Agent
```

## 必学模式

```text
Supervisor
Router
Handoff
Sequential Agents
Parallel Agents
Hierarchical Agents
Subagents
```

## 推荐架构

```text
                    Supervisor
                         │
          ┌──────────────┼──────────────┐
          ↓              ↓              ↓
      Research         Coding         Review
       Agent            Agent          Agent
          │              │              │
          └──────────────┼──────────────┘
                         ↓
                       Tester
                         ↓
                       Final
```

## 必须理解的问题

```text
为什么拆 Agent？
Agent 边界是什么？
谁拥有 State？
谁负责 Tool？
Agent 之间如何通信？
什么时候 Parallel？
什么时候 Handoff？
什么时候 Supervisor？
失败如何恢复？
```

原则：

> 不要为了“看起来高级”而拆 Multi-Agent。

---

# 15. Phase 10：Agent Harness

## 时间

第 22～23 周

这是从普通 Agent 开发走向复杂 Agent 系统的重要阶段。

可以理解：

```text
Agent
 ├── Model
 └── Harness
      ├── Context Management
      ├── Planning
      ├── Tools
      ├── Skills
      ├── Memory
      ├── Filesystem
      ├── Subagents
      ├── Sandbox
      ├── Permissions
      ├── Execution
      ├── Retry
      └── Recovery
```

当前 Agent 工程越来越强调：

> 长周期任务、上下文管理、文件操作、代码执行、Subagent、Sandbox、安全控制。

OpenAI 在 2026 年更新 Agents SDK 时也将 model-native harness、文件操作、命令执行和受控 sandbox 纳入重点能力。

## 学习地址

- OpenAI Agents SDK：https://openai.github.io/openai-agents-python/
- OpenAI Agents SDK 更新：https://openai.com/index/the-next-evolution-of-the-agents-sdk/
- LangChain / LangGraph Frameworks：https://docs.langchain.com/oss/python/concepts/products

---

# 16. Phase 11：Context Engineering

## 时间

第 22～23 周穿插学习

Context Engineering 是 Agent 工程非常核心的能力。

不是简单：

> “Prompt 写得好”。

而是：

```text
什么信息应该进入 Context？
什么信息不应该进入？
什么时候加载？
什么时候压缩？
什么时候检索？
什么时候清理？
```

## 必学

```text
Context Window
Context Budget
Conversation History
Summarization
Compaction
Retrieval
Tool Result Management
Context Compression
State
Memory
```

核心思想：

```text
全部信息
   ↓
筛选
   ↓
压缩
   ↓
检索
   ↓
当前任务 Context
   ↓
LLM
```

---

# 17. Phase 12：Memory

## 必学

### Short-term Memory

当前任务：

```text
Conversation
Agent State
Working Memory
```

### Long-term Memory

跨会话：

```text
User Preferences
Facts
Past Tasks
Knowledge
```

## 必须理解

```text
Memory ≠ Chat History
```

Memory 应该解决：

> 什么信息值得长期保存，以及什么时候应该重新取出来。

---

# 18. Phase 13：Skills

Skills 可以理解为：

> 可复用的 Agent 能力 / 操作规范 / 工作方法。

典型结构：

```text
Skill
 ├── Instructions
 ├── Examples
 ├── Tools
 ├── Constraints
 └── Resources
```

例如：

```text
Frontend Code Review Skill
Figma Analysis Skill
API Design Skill
Vue Development Skill
Testing Skill
Security Review Skill
```

重点学习：

```text
Skill Discovery
Skill Loading
Skill Composition
Skill Versioning
Skill Permission
```

不要把 Skill 简单理解为：

> 一段 Prompt。

更应该理解为：

> Agent 可动态加载和复用的能力模块。

---

# 19. Phase 14：Evaluation

## 时间

第 24 周

这是从 Demo 到 Production 的关键。

传统：

```text
Unit Test
E2E
```

Agent：

```text
Input
 ↓
LLM
 ↓
Reasoning
 ↓
Tool
 ↓
Observation
 ↓
Final
```

所以必须评估整个 trajectory。

## 必学

```text
Golden Dataset
Offline Evaluation
Online Evaluation
LLM-as-Judge
Trajectory Evaluation
Tool Calling Accuracy
Answer Correctness
Faithfulness
Latency
Token Usage
Cost
Success Rate
Regression Test
```

## 推荐工具

- LangSmith：https://smith.langchain.com/
- LangSmith Docs：https://docs.langchain.com/langsmith/home
- OpenAI Evals：https://github.com/openai/evals

---

# 20. Phase 15：Observability

你已有前端监控经验，这部分会比较容易。

Agent Observability：

```text
Trace
 ├── LLM Call
 │    ├── Prompt
 │    ├── Tokens
 │    └── Latency
 │
 ├── Tool Call
 │    ├── Input
 │    ├── Output
 │    └── Error
 │
 ├── Retrieval
 │    ├── Query
 │    └── Documents
 │
 └── Final Answer
```

## 必学指标

```text
Latency
Token
Cost
Error Rate
Tool Failure
Agent Success Rate
Retrieval Quality
LLM Failure
Retry
```

## 推荐

- LangSmith：https://smith.langchain.com/
- OpenTelemetry：https://opentelemetry.io/
- Prometheus：https://prometheus.io/
- Grafana：https://grafana.com/

---

# 21. Phase 16：Agent Security

生产 Agent 必须学习。

## 必学

```text
Prompt Injection
Indirect Prompt Injection
Tool Permission
Least Privilege
Credential Isolation
Sandbox
Data Exfiltration
Sensitive Data
Approval
Audit Log
Rate Limit
Tenant Isolation
```

危险链路：

```text
User
 ↓
Prompt Injection
 ↓
Agent
 ↓
Tool
 ↓
Sensitive API
 ↓
Data Leak
```

所以：

> Tool 不应该默认拥有所有权限。

---

# 22. Phase 17：生产基础设施

## Backend

```text
FastAPI
PostgreSQL
Redis
Docker
Nginx
```

## Agent Infrastructure

```text
Vector DB
Object Storage
Queue
Observability
Evaluation
Secrets Management
Sandbox
```

## 不需要一次全部深入

优先：

```text
PostgreSQL
Redis
Docker
pgvector
OpenTelemetry
```

Kubernetes 了解部署模型即可，除非工作岗位明确要求。

---

# 23. 六个核心实战项目

## Project 1：AI Chat

```text
Vue / React
+
FastAPI
+
LLM
+
SSE
```

功能：

```text
多轮对话
Streaming
Markdown
Code Highlight
Session
History
Token
Cost
```

---

# 24. Project 2：Tool Agent

AI 工作助手：

```text
Weather
Search
Calculator
HTTP
Database
File
```

实现：

```text
LLM
 ↓
Tool Selection
 ↓
Tool Execution
 ↓
Tool Result
 ↓
LLM
```

必须加入：

```text
Retry
Timeout
Error Handling
Permission
Human Approval
```

---

# 25. Project 3：企业知识库 Agent

```text
PDF
Word
Markdown
Excel
 ↓
Parser
 ↓
Chunk
 ↓
Embedding
 ↓
Vector DB
 ↓
Retriever
 ↓
Rerank
 ↓
Agent
```

实现：

```text
多轮问答
引用
权限
Query Rewrite
Hybrid Search
Rerank
Evaluation
```

---

# 26. Project 4：Frontend Development MCP Server

这是非常适合前端工程师的项目。

Tools：

```text
get_project_info
search_code
read_file
search_component
get_api
search_docs
get_git_diff
```

架构：

```text
AI Coding Agent
       ↓
    MCP Client
       ↓
Frontend MCP Server
       ↓
Code / Docs / API / Git
```

---

# 27. Project 5：Multi-Agent Coding Agent

推荐作为求职核心作品。

```text
                     User
                      │
                      ↓
                  Supervisor
                      │
       ┌──────────────┼──────────────┐
       ↓              ↓              ↓
 Requirement        Coding         Review
    Agent            Agent          Agent
       │              │              │
       ↓              ↓              ↓
   Spec/Plan         Git           Review
       │              │              │
       └──────────────┼──────────────┘
                      ↓
                    Tester
                      ↓
                    Result
```

加入：

```text
MCP
Skills
Memory
RAG
Git
Filesystem
Sandbox
Human Approval
Evaluation
```

---

# 28. Project 6：Production Agent Platform

最终项目：

```text
                    Agent Platform
                          │
       ┌──────────────────┼──────────────────┐
       ↓                  ↓                  ↓
     Agents             Tools              Skills
       │                  │                  │
       ↓                  ↓                  ↓
   Workflow              MCP              Prompts
       │                  │                  │
       └──────────────────┼──────────────────┘
                          ↓
                       Runtime
                          │
             ┌────────────┼─────────────┐
             ↓            ↓             ↓
          Memory         RAG          SubAgent
             │            │             │
             └────────────┼─────────────┘
                          ↓
                     Evaluation
                          ↓
                    Observability
                          ↓
                       Security
                          ↓
                     Production
```

---

# 29. 推荐技术栈

## Frontend

```text
Vue / React
TypeScript
SSE / WebSocket
AI Chat UI
Streaming UI
Artifact UI
Agent State UI
```

## Backend

```text
Python
FastAPI
Pydantic
asyncio
```

## Agent

```text
LangChain
LangGraph
OpenAI Agents SDK
MCP
```

## RAG

```text
Embedding
Hybrid Search
Rerank
pgvector / Milvus
```

## Infrastructure

```text
PostgreSQL
Redis
Docker
OpenTelemetry
Prometheus
Grafana
```

## Evaluation

```text
LangSmith
OpenAI Evals
LLM-as-Judge
Golden Dataset
Trajectory Evaluation
```

---

# 30. 能力矩阵

| 能力 | 目标 |
|---|---:|
| TypeScript | ⭐⭐⭐⭐⭐ |
| Python | ⭐⭐⭐⭐ |
| LLM API | ⭐⭐⭐⭐⭐ |
| Prompt / Context Engineering | ⭐⭐⭐⭐⭐ |
| Structured Output | ⭐⭐⭐⭐⭐ |
| Tool Calling | ⭐⭐⭐⭐⭐ |
| Agent Loop | ⭐⭐⭐⭐⭐ |
| RAG | ⭐⭐⭐⭐ |
| MCP | ⭐⭐⭐⭐⭐ |
| LangChain | ⭐⭐⭐⭐ |
| LangGraph | ⭐⭐⭐⭐⭐ |
| OpenAI Agents SDK | ⭐⭐⭐⭐ |
| Multi-Agent | ⭐⭐⭐⭐⭐ |
| Memory | ⭐⭐⭐⭐ |
| Skills | ⭐⭐⭐⭐ |
| Harness | ⭐⭐⭐⭐⭐ |
| Sandbox | ⭐⭐⭐⭐ |
| Evaluation | ⭐⭐⭐⭐⭐ |
| Observability | ⭐⭐⭐⭐⭐ |
| Security | ⭐⭐⭐⭐⭐ |
| FastAPI | ⭐⭐⭐⭐ |
| PostgreSQL / Redis | ⭐⭐⭐⭐ |
| Docker | ⭐⭐⭐⭐ |
| Vector DB | ⭐⭐⭐ |
| Kubernetes | ⭐⭐⭐ |
| Transformer 原理 | ⭐⭐ |
| 模型训练 | ⭐⭐ |

---

# 31. 最终学习顺序

不要同时学十几个框架。

建议严格按照：

```text
Python
 ↓
LLM API
 ↓
Structured Output
 ↓
Function Calling
 ↓
Tool
 ↓
Agent Loop
 ↓
RAG
 ↓
MCP
 ↓
Workflow
 ↓
LangChain
 ↓
LangGraph
 ↓
Memory
 ↓
Context Engineering
 ↓
Multi-Agent
 ↓
Skills
 ↓
Harness
 ↓
Sandbox
 ↓
Evaluation
 ↓
Observability
 ↓
Security
 ↓
Production
```

---

# 32. 框架学习策略

不要：

```text
LangChain
CrewAI
AutoGen
AgentScope
LangGraph
Dify
Coze
全部学一遍
```

正确方式：

```text
先理解 Agent 原理
       ↓
LangChain 快速开发
       ↓
LangGraph 深入 Runtime
       ↓
OpenAI Agents SDK 理解现代 Agent SDK / Harness
       ↓
MCP 标准化工具连接
       ↓
Multi-Agent
       ↓
Production
```

框架是实现手段，不是最终能力。

---

# 33. 最终应该能回答的问题

学习完成后，面试时应该能够清晰回答：

### Agent

- Agent 和 LLM 有什么区别？
- Agent Loop 是什么？
- Tool Calling 如何工作？
- ReAct 是什么？
- Workflow 和 Agent 怎么选择？

### RAG

- 为什么需要 RAG？
- Chunk 如何设计？
- Vector Search 和 Keyword Search 区别？
- 为什么需要 Rerank？
- 什么是 Agentic RAG？
- 如何评估 RAG？

### MCP

- MCP 解决什么问题？
- Host / Client / Server 区别？
- Tool / Resource / Prompt 区别？
- MCP Transport 有哪些？
- MCP 如何做安全控制？

### LangGraph

- State / Node / Edge 是什么？
- 为什么需要 Checkpoint？
- 如何实现 Human-in-the-loop？
- 如何实现 Streaming？
- Workflow 和 Agent 怎么设计？
- Multi-Agent 如何拆分？

### Multi-Agent

- 为什么拆 Agent？
- Supervisor 和 Handoff 区别？
- 如何设计 Agent 边界？
- 如何处理 Agent 失败？

### Harness

- Model 和 Harness 区别？
- Context 如何管理？
- Skill 是什么？
- Subagent 怎么设计？
- Sandbox 为什么重要？

### Production

- Agent 如何评估？
- Agent 如何监控？
- 如何控制 Token / Cost？
- 如何防 Prompt Injection？
- Tool 权限如何设计？
- Agent 长任务失败如何恢复？

---

# 34. 你的最终定位

经过 6 个月系统学习后，不建议简历写：

> 前端开发 + 会 LangChain

更建议定位：

> **AI Agent Full-Stack Engineer**

进一步：

> **Agent Systems Engineer**

能力结构：

```text
                 Agent Systems Engineer
                          │
          ┌───────────────┼────────────────┐
          ↓               ↓                ↓
    AI Application   Agent Runtime    AI Engineering
          │               │                │
      RAG / MCP        LangGraph        Python
      Tool Calling     Multi-Agent      FastAPI
      AI UI            Harness          Docker
      Streaming        Memory           Redis
          │               │                │
          └───────────────┼────────────────┘
                          ↓
                   Production Agent
                          │
             ┌────────────┼────────────┐
             ↓            ↓            ↓
          Evaluation   Security    Observability
```

---

# 35. 最重要的原则

### 原则 1：先原理，后框架

```text
Agent
 ↓
Tool
 ↓
RAG
 ↓
MCP
 ↓
Workflow
 ↓
Runtime
```

然后才是：

```text
LangChain
LangGraph
OpenAI Agents SDK
```

### 原则 2：不要过度 Multi-Agent

一个 Agent 能解决的问题，不要拆成五个 Agent。

### 原则 3：不要把 Prompt Engineering 当成 Agent Engineering

Agent Engineering 包括：

```text
Context
Tools
State
Memory
Runtime
Permissions
Evaluation
Observability
Security
```

### 原则 4：Production 能力比 Demo 更重要

最终一定要掌握：

```text
Retry
Timeout
Persistence
Checkpoint
Human Approval
Evaluation
Tracing
Cost
Security
Sandbox
```

### 原则 5：充分利用前端优势

重点发展：

```text
AI UI
Streaming
Agent State Visualization
Tool Call UI
Human-in-the-loop UI
Artifact UI
Multi-Agent UI
```

这样你会形成：

> 前端 + AI + Agent + Backend

的复合能力，而不是从头和纯 Python Agent 工程师竞争。

---

# 36. 一句话路线

```text
7年经验前端
    ↓
Python / FastAPI
    ↓
LLM
    ↓
Tool Calling
    ↓
Agent
    ↓
RAG
    ↓
MCP
    ↓
LangChain
    ↓
LangGraph
    ↓
Multi-Agent
    ↓
Context / Memory / Skills
    ↓
Harness / Sandbox
    ↓
Evaluation
    ↓
Observability / Security
    ↓
Production Agent
    ↓
AI Agent Full-Stack Engineer
    ↓
Agent Systems Engineer
```

---

## 37. 官方资料总入口

### LLM

- OpenAI：https://platform.openai.com/docs/
- Anthropic：https://docs.anthropic.com/
- Gemini：https://ai.google.dev/gemini-api/docs
- Qwen：https://qwen.readthedocs.io/

### Agent

- LangChain：https://docs.langchain.com/oss/python/langchain/overview
- LangGraph：https://docs.langchain.com/oss/python/langgraph/overview
- OpenAI Agents SDK：https://openai.github.io/openai-agents-python/

### MCP

- MCP：https://modelcontextprotocol.io/
- MCP Python SDK：https://github.com/modelcontextprotocol/python-sdk
- MCP TypeScript SDK：https://github.com/modelcontextprotocol/typescript-sdk

### RAG

- LlamaIndex：https://docs.llamaindex.ai/
- Milvus：https://milvus.io/docs
- pgvector：https://github.com/pgvector/pgvector

### Evaluation / Observability

- LangSmith：https://smith.langchain.com/
- OpenTelemetry：https://opentelemetry.io/
- OpenAI Evals：https://github.com/openai/evals

### Backend

- Python：https://docs.python.org/3/tutorial/
- FastAPI：https://fastapi.tiangolo.com/
- Pydantic：https://docs.pydantic.dev/latest/
- Docker：https://docs.docker.com/
- Redis：https://redis.io/docs/
- PostgreSQL：https://www.postgresql.org/docs/

---

## 38. 结论

对于一个拥有 7 年前端经验的工程师，最合理的 2026 Agent 转型路线不是重新从计算机基础开始，而是：

> **利用已有工程能力，快速补齐 LLM、Agent Runtime、RAG、MCP、Multi-Agent、Harness、Evaluation 和 Production 能力。**

最终目标不是“会使用某一个 Agent 框架”，而是：

> **能够从业务问题出发，判断应该使用普通 LLM、Workflow、单 Agent 还是 Multi-Agent，并独立完成从 Tool / MCP / RAG / Runtime / Memory / Harness 到 Evaluation / Observability / Security / Deployment 的完整 Agent 系统。**
