import assert from "node:assert/strict";
import test from "node:test";
import { readError, formatError, formatToolResult, errorMessage, RequestError, checkedFetch } from "../src/lib/errors.ts";

const details = {
  version: 1, code: "UPSTREAM_TIMEOUT", message: "服务响应超时，请稍后重试。",
  source: "model", requestId: "request-123", retryable: true,
  attempts: 3, maxAttempts: 3, retryExhausted: true, partial: false, outcomeUnknown: false,
};

test("live and restored structured errors share the same user message", () => {
  assert.deepEqual(readError(details), details);
  assert.match(formatError(details), /UPSTREAM_TIMEOUT/);
  assert.match(formatError(details), /request-123/);
  assert.equal(errorMessage(new RequestError(details)), formatError(details));
});

test("structured tool errors display their message instead of raw JSON", () => {
  assert.equal(formatToolResult(JSON.stringify({ ok: false, error: details })), formatError(details));
  assert.equal(formatToolResult("正常结果"), "正常结果");
  assert.equal(formatToolResult('{"value":42}'), '{"value":42}');
});

test("raw transport exceptions are not displayed to users", () => {
  assert.equal(errorMessage(new Error("sk-private-value")), "请求失败，请重新加载会话。");
  assert.equal(errorMessage(new Error(JSON.stringify({ error: details }))), formatError(details));
});

test("incomplete and invalid error envelopes are rejected", () => {
  assert.equal(readError({ code: "TIMEOUT" }), null);
  assert.equal(readError({ ...details, attempts: "3" }), null);
  assert.equal(readError({ ...details, partial: "false" }), null);
  assert.equal(readError({ ...details, version: 2 }), null);
});

test("HTTP errors before SSE starts retain their structured details", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ error: details }), { status: 503 });
  try {
    await assert.rejects(checkedFetch("https://test.invalid", {}), (error) => {
      assert.ok(error instanceof RequestError);
      assert.deepEqual(error.details, details);
      return true;
    });
  } finally { globalThis.fetch = originalFetch; }
});

test("the AG-UI client preserves structured HTTP errors before streaming", async () => {
  const { HttpAgent } = await import("@ag-ui/client");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ error: details }), { status: 503 });
  try {
    const agent = new HttpAgent({ url: "https://test.invalid", fetch: checkedFetch, debug: false });
    agent.addMessage({ id: "u", role: "user", content: "test" });
    let failure;
    try {
      await agent.runAgent({}, { onRunFailed({ error }) { failure = error; } });
    } catch (error) { failure ??= error; }
    assert.equal(errorMessage(failure), formatError(details));
  } finally { globalThis.fetch = originalFetch; }
});
