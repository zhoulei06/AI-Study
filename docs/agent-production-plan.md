# Agent 服务生产化重构方案

日期：2026-11-03。以下包含设计、迁移计划与本轮实施状态。

## 结论

保留 FastAPI + LangChain create_agent/LangGraph + PostgreSQL/pgvector + MCP。
采用模块化单体，将运行管理、图执行、协议适配和持久化分开；暂不引入多 Agent、微服务或通用工作流平台。
建议用官方 Python `ag-ui-langgraph` 承担标准协议转换，通过兼容验证后再替换当前适配器。

## 当前架构需要调整的边界

1. `agent/server.py` 同时负责路由、线程生命周期、运行注册、事件转换、审批和历史写入，变更容易互相影响。
2. `agent/loop.py` 同时承担图执行、协议观察事件、取消后的文本续写、工具审批和长期记忆整理。
3. BudgetMiddleware、MCP 确认回调及部分流式状态存放在共享实例上，目前依赖全局锁保证单次执行。
4. 会话 JSON 中同时保存展示条目、模型消息、运行状态，图 checkpoint 又有执行消息；需要明确各自权威来源和一致性策略。
5. MCP 删除确认等待进程内 Future；浏览器断开不一定结束运行，但服务重启无法恢复这个等待中的 RPC。

## 建议模块

```text
agent/
  api/              HTTP 路由、请求校验、身份依赖、SSE 订阅
  application/      RunService、ConversationService、ApprovalService
  runtime/          图构建、每次运行的上下文、预算、取消、恢复
  adapters/agui/    官方 LangGraph 适配器的薄封装、审批映射、自定义事件
  tools/            本地工具、工具授权/确认策略、MCP 网关
  memory/           摘要、检索、记忆策略与提取任务
  persistence/      runs/messages/events/approvals 仓储及数据库迁移
  infrastructure/   配置、模型客户端、连接池、日志与追踪
```

逐步从现有文件搬迁，避免一次性改写全部目录。依赖方向由 API 指向应用层，应用层通过接口调用执行器和仓储；运行核心不依赖 FastAPI 或 AG-UI。

## 运行和并发

- 每次请求生成独立 RunContext：principal、thread_id、run_id、deadline、budget、cancel signal、trace_id。
- 编译图和连接池可复用，但中间件不得持有当前请求的可变状态；AG-UI 适配器也要按请求隔离。
- 同一 thread 同时只允许一个活动 run，不同 thread 可并发，另设全局并发上限和用户配额。
- 单进程先用明确的运行管理器；多实例前引入数据库运行租约、心跳、过期接管和 fencing，不能依赖 threading.Lock。
- 等待模型、数据库和网络采用异步链路；本地 embedding 等阻塞工作放入受限线程池或独立任务。
- 不因改成 async 就移除并发控制；异步提高资源利用率，不能替代状态隔离。

## 数据权威来源与事件

- checkpoint：图内部执行状态和恢复位置。
- runs：运行状态、拥有者、租约、取消请求、错误和用量。
- messages：用户可见的持久消息和历史列表。
- approvals：确认对象、工具参数摘要、状态、有效期与决策审计。
- run_events：带 run_id、递增 seq、schema_version 的事件，用于增量投影和断线重放。
- 同一数据库事务内提交事件和展示投影，或使用 outbox 异步投影并暴露投影水位；不要假设 checkpoint 与 SSE 自然原子提交。
- SSE 只是订阅出口。连接断开、用户取消、运行失败、等待审批是不同状态。
- 支持重连游标和快照补齐，按 seq 去重。控制事件必须可靠；文本 delta 可以合并批量落库。
- 慢客户端使用有界缓冲，超限断开订阅并让其重放，避免无限内存积压。
- 过滤发给浏览器的状态、原始事件和内部推理；不能把全部图 state 直接公开。

## 工具、审批和副作用

- 身份从经验证的 JWT 得到稳定 subject；会话、记忆、审批和工具访问统一检查 owner/tenant。
- MCP 使用明确的用户委托或服务身份模型，不以服务启动时一次浏览器登录代表所有用户。
- 写工具执行前持久化操作 ID、参数摘要和授权结果；幂等键由工具服务共同落实。
- 超时代表结果可能未知。查询操作状态后再决定是否重试，避免盲目重放新增/删除。
- 图恢复可能重新执行节点，副作用必须幂等；checkpoint 本身不提供远端写操作的 exactly-once。
- 将业务审批收敛为持久的暂停/恢复语义。MCP elicitation 仍可保留为协议交互，但进程内 Future 不能成为唯一审批记录。
- 明确取消语义：停止后续计算、请求中止正在进行的调用，不承诺撤回已提交的远端操作。
- “取消后继续文本”与“从图 checkpoint 恢复执行”是两条不同路径，保留独立测试和接口语义。

## AG-UI 集成决策

引入的包是 Python `ag-ui-langgraph`，不是要求 React 前端改用 TypeScript `@ag-ui/langgraph`。现有 `HttpAgent` 可继续通过 HTTP/SSE 消费协议。

标准文本、工具、节点进度、状态和中断事件交给官方适配器；天气卡片、记忆任务进度、MCP elicitation 等业务展示事件继续在薄适配层扩展。
自己调用 `emit` 并非问题，主要成本是重复维护 LangGraph 到 AG-UI 的通用映射。

不直接将现有路由替换为一行 endpoint helper：自有 RunService 需要先授权、认领运行、加载权威输入，再驱动适配器事件迭代器；事件进入持久化/投影链路后再交给 SSE。
集成套件不负责用户隔离、运行租约、业务幂等、历史存储和断线重放。

迁移门槛：

1. 锁定并验证已发布的 ag-ui-langgraph、ag-ui-protocol、langchain、langgraph 及前端 client 版本组合，不能把 GitHub main 当成已发布包行为。
2. 为 HumanInTheLoopMiddleware 的 action_requests/review_configs 与 AG-UI resume payload 编写薄映射。
3. 验证普通流、DeepSeek reasoning、工具参数/结果、单个及多个审批、拒绝、取消、恢复、重连、异常终态。
4. 当前前端读取结构化 interrupt outcome，需确认并显式配置目标版本的对应开关。
5. 旧/新适配器使用独立测试数据和功能开关；禁止对真实写工具双跑比较。
6. 验证会话快照不会覆盖已经持久化的新内容，且重放不会重复显示工具或重复执行副作用。

## 记忆与预算

- 修复 short_term._render 在计算大小和摘要前统一截取每条消息前 500 字的问题。
- 统一统计正文、工具参数与结果；采用适配模型的 token 计数或可校准估算，并保留完整工具调用配对。
- 摘要和长期记忆提取也纳入模型用量、deadline、取消及重试预算。
- 长期提取以 run_id 为幂等键，先通过 PostgreSQL outbox/job 表持久排队；失败可重试且不阻塞回答完成。
- 保存记忆来源、模型/提示版本、提取时间、置信度及用户纠正记录；记忆作为数据注入，不能升级为可信系统指令。
- 向量模型记录模型标识、revision 和维度；同维度换模型也要重新嵌入。
- 禁止请求启动时自动删向量表；使用显式迁移和新旧索引切换。

## 分阶段实施与验收

### 第一阶段：可靠性基线

完成目录改名；冻结当前取消/审批行为；修复消息截断、MCP 超时 Future 未取消、记忆预算遗漏；增加回归测试与 CI。
验收：长输入不静默丢失，超时不会触发盲目写重试，全部模型调用可计量。

### 第二阶段：边界与官方协议适配

提取 RunService/RunContext，拆开协议转换与图执行，按版本锁定接入官方适配器。
验收：旧有取消、继续和审批场景全部通过；不同 thread 并发时事件与预算不串线。

### 第三阶段：持久运行和访问控制

加入 JWT 身份、runs/approvals/events、幂等与重放，长期记忆进入持久后台任务。
验收：跨用户无法访问会话，服务重启后运行可判定/恢复，断线重连事件不重不漏，写工具重试不重复副作用。

### 第四阶段：容量与运维

按实际负载决定是否分离 API/Worker、是否引入 Redis/独立消息队列；完善租约、连接池、限流、保留期和优雅停机。
追踪 thread/run/tool/model；度量首 token 延迟、总延迟、模型/工具错误率、取消延迟、事件积压、token 与费用、记忆任务延迟。
使用固定任务集评估工具选择、审批遵守、记忆正确性和恢复行为，不能只检查 HTTP 成功。

## 官方资料

- AG-UI Python 集成说明：https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/langgraph/python/README.md
- FastAPI endpoint 实现（请求级 clone）：https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/langgraph/python/ag_ui_langgraph/endpoint.py
- LangGraph 审批与恢复：https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph checkpoint/store 边界：https://docs.langchain.com/oss/python/langgraph/persistence

资料核查于 2026-11-03；接入时仍须验证实际锁定版本。


## 实施记录（2026-11-03）

已实现运行上下文隔离、RunService、官方 AG-UI 协议适配与回退开关、同步连接池的异步 checkpoint 访问、摘要完整输入和分块、主调用与辅助调用预算、MCP 超时/取消清理、持久记忆任务、事件日志/重放及有界订阅、非破坏性模型配置校验、版本化应用迁移与 CI。

本轮没有移除单运行入场控制：MCP 登录与 elicitation 仍属于进程级会话。现阶段仍使用单 HTTP worker，应用身份仍是 AGENT_HISTORY_OWNER。

后续阶段需要实现运行级数据库租约和恢复接管、浏览器认证与 MCP 用户委托、远端写操作幂等、持久业务审批，以及消息分行存储、事件保留与增量投影优化。现有事件重放接口可供客户端使用，页面仍通过快照轮询恢复状态。

旧索引没有模型元数据，首次升级仅登记当前配置为基线，不会重嵌入；操作者需确认当前模型就是旧索引使用的模型。之后模型/维度/revision 变化会拒绝启动，保护原索引。

本轮验收：37 项后端回归通过（包括真实 PostgreSQL checkpoint、事件与投影事务回滚、重放、后台任务租约、官方适配器 reasoning/工具/审批/取消续写）；Ruff、uv 锁校验、迁移命令和前端构建通过。模型调用使用确定性替身验证，未执行付费 DeepSeek 请求。开发服务已重启，健康检查显示 Agent ready、MCP connected。

### 后续：调用失败与重试

已加入统一同步/异步重试控制层、真实 SDK 客户端重试关闭与逐次超时设置、临时错误分类、指数退避和 Retry-After、流式输出后的重放保护、明确只读工具的有限重试及写操作结果未知保护。模型调用尝试和工具重试均受原运行预算约束，记忆任务由持久队列单独负责重试。

重试优化验收：59 项后端测试通过，Ruff 与 uv 锁检查通过，MCP 工具目录只读注解检查通过。持久任务测试使用随机隔离 schema，避免被开发服务 worker 领取。当前运行中的 Agent/MCP 尚未重启加载本轮策略，MCP 内存笔记须在重启前保存。配置与行为详见 agent-server/README.md 的“调用失败与重试”。
