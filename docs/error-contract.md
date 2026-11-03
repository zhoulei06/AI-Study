# 结构化错误协议

## 定义与语义

协议版本为 `1`。后端定义在 `agent-server/agent/errors.py`，前端解析和展示位于 `front/src/lib/errors.ts`。

错误使用稳定机器码和固定用户提示，不公开原始 SDK 异常、令牌、连接串或验证输入。日志通过 `requestId`、`runId`、错误码和异常类型定位问题。

本协议统一错误表达，不把所有工具失败都改成整轮失败：模型无法继续执行时终止运行；工具失败仍可交给模型解释或采取其他措施。

## 公共结构

```json
{
  "version": 1,
  "code": "RATE_LIMITED",
  "message": "上游服务请求过于频繁，请稍后重试。",
  "source": "model",
  "requestId": "70dc8361-bdfe-4d4a-9316-9ee735f30181",
  "runId": "example-run",
  "threadId": "example-thread",
  "operation": "model",
  "retryable": true,
  "attempts": 3,
  "maxAttempts": 3,
  "retryExhausted": true,
  "partial": false,
  "outcomeUnknown": false
}
```

- `code`：用于程序判断的稳定错误码，不应匹配中文提示。
- `message`：错误码目录生成的安全提示。
- `source`：原始错误来源，如 `api/model/tool/mcp/memory/history/runtime/transport`，上层捕获后保留已分类来源。
- `requestId`：服务端生成的参考号；HTTP 响应头 `X-Request-ID` 返回同一个值。
- `runId/threadId/operation`：可选的运行、会话及操作标识，不包含工具参数或 URL。
- `retryable`：该故障是否允许条件恢复后重新尝试，不代表立即自动重试。
- `attempts/maxAttempts`：实际尝试次数和上限；尚未调用时可以为 `0`。
- `retryExhausted`：是否因可重试故障耗尽次数，或后台任务达到尝试上限。永久错误第一次就可终止，不必标记为耗尽。
- `partial`：是否有需要保留的流式内容。
- `outcomeUnknown`：写操作结果无法确认，为真时不可直接重放。
- `causeCode`：可选，流中断或写结果未知时保留原始分类。
- `limit`：可选，预算停止的具体限制项，如 `timeout/token_budget/max_iterations/memory_attempts`。

## 错误码

- 请求与状态：`INVALID_ARGUMENT`、`NOT_FOUND`、`RUN_CONFLICT`、`AGENT_NOT_READY`。
- 上游访问：`AUTHENTICATION_FAILED`、`PERMISSION_DENIED`、`RATE_LIMITED`、`NETWORK_ERROR`、`UPSTREAM_TIMEOUT`、`UPSTREAM_UNAVAILABLE`、`UPSTREAM_REJECTED`、`TLS_ERROR`。
- 工具：`TOOL_NOT_FOUND`、`TOOL_REJECTED`、`TOOL_EXECUTION_FAILED`、`OPERATION_OUTCOME_UNKNOWN`。
- 执行与存储：`LIMIT_EXCEEDED`、`RUN_CANCELLED`、`STREAM_INTERRUPTED`、`PERSISTENCE_FAILED`。
- 配置与未知错误：`CONFIGURATION_ERROR`、`INTERNAL_ERROR`。

完整目录和固定提示以 `ErrorCode`、`MESSAGES` 为准。未知异常回退为安全提示，不向浏览器输出异常字符串。

## 通道约定

### HTTP：流开始前

保留 HTTP 状态码，响应体为 `{"error": 公共错误结构, "detail": 安全提示}`。`detail` 字符串兼容旧客户端，新代码优先读取 `error`。

Pydantic 参数验证失败不返回包含输入值的原始验证列表。前端 HttpAgent 使用 `checkedFetch`，避免流建立前的错误详情被 SDK 丢失。

### SSE：运行中

发送 AG-UI `RUN_ERROR`，其中 `code` 为错误码、`message` 为安全提示，`metadata.error` 为完整公共错误结构。

终止失败的运行不再发送成功 `RUN_FINISHED`；保留已输出文本，会话和当前回答标记为 `failed`。正常用户取消仍使用 `RUN_FINISHED` 的 interrupt outcome，与错误分开。

官方适配器转换异常前先保留分类，应用层据此恢复完整结构，避免只剩字符串。

### 工具结果

工具失败返回 JSON 字符串：`{"ok": false, "error": 公共错误结构}`。模型系统提示明确 `ok=false` 不能当作成功，前端工具卡片将其格式化为安全提示、错误码和参考号。

MCP 线协议保持 SDK 标准，Agent 在边界转换业务错误，不更改 OAuth/MCP 标准响应。

### 历史与重放

会话保留兼容字符串 `error`，新增 `errorInfo` 保存完整结构。新运行清除旧错误；日志持久化完整 `RUN_ERROR`，重放保留原错误码和参考号。

前端刷新后优先显示 `errorInfo`，旧记录只有裸异常文本时显示通用失败提示。数据库不可写时，通过当前订阅发送 `PERSISTENCE_FAILED` 并释放资源，但无法保证该条事件也已持久化。

### 后台记忆任务

`agent_memory_jobs.error` 保存错误码，新增 `error_info JSONB` 保存详情，由 `003_error_info.sql` 增量迁移。临时故障仍按任务队列规则重试，确定性错误立即失败；租约多次过期达到上限也记录结构化错误。

后台任务失败不改变已完成的聊天结果，目前没有新增任务管理页面。

### CLI

命令行执行失败输出包含 `error` 的 JSON；预算停止结果也包含错误结构，便于脚本读取。

## 预算与写操作

预算停止发送 `LIMIT_EXCEEDED`，在 `limit` 中标明原因，不再将停止说明标为成功回答。

`OPERATION_OUTCOME_UNKNOWN` 禁止直接自动重试；现有保护仅针对同一运行内相同工具和参数，不是跨运行幂等。

`retryable=true` 不增加重试次数。自动重试仍由原策略负责，不能绕过总超时、次数和费用限制。

## 验证入口

- `test_errors.py`：HTTP、真实图适配器、SSE、历史、重放、预算、持久化故障和敏感内容隔离。
- `test_retry.py`：原始异常类型和重试行为保持兼容。
- `test_persistence.py`：任务错误详情的持久化。
- `front/tests/errors.test.mjs`：结构验证、安全展示和真实 HttpAgent 的错误传递。

后端执行 `uv run python -m unittest discover -s tests -v`，前端执行 `pnpm test` 和 `pnpm build`。前端测试使用 Node 22 的类型擦除能力，无需新增测试框架。
