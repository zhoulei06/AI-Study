export type ErrorDetails = {
  version: 1;
  code: string;
  message: string;
  source: string;
  requestId: string;
  retryable: boolean;
  attempts: number;
  maxAttempts: number;
  retryExhausted: boolean;
  partial: boolean;
  outcomeUnknown: boolean;
  runId?: string;
  threadId?: string;
  causeCode?: string;
  limit?: string;
};

export function readError(value: unknown): ErrorDetails | null {
  if (!value || typeof value !== "object") return null;
  const error = value as Record<string, unknown>;
  if (error.version !== 1 || typeof error.code !== "string" ||
      typeof error.message !== "string" || typeof error.requestId !== "string" ||
      typeof error.source !== "string" || typeof error.retryable !== "boolean" ||
      !Number.isInteger(error.attempts) || Number(error.attempts) < 0 ||
      !Number.isInteger(error.maxAttempts) || Number(error.maxAttempts) < 0 ||
      typeof error.retryExhausted !== "boolean" || typeof error.partial !== "boolean" ||
      typeof error.outcomeUnknown !== "boolean") return null;
  return error as ErrorDetails;
}

export function formatError(error: ErrorDetails): string {
  const reference = error.source === "client" ? "" : `，参考号：${error.requestId}`;
  return `${error.message}（${error.code}${reference}）`;
}

export class RequestError extends Error {
  readonly details: ErrorDetails;
  constructor(details: ErrorDetails) {
    super(formatError(details));
    this.name = "RequestError";
    this.details = details;
  }
}

export function errorMessage(cause: unknown, fallback = "请求失败，请重新加载会话。") {
  if (cause instanceof RequestError) return cause.message;
  if (cause instanceof Error) {
    try {
      const error = readError(JSON.parse(cause.message)?.error);
      if (error) return formatError(error);
    } catch { /* Transport errors use a safe local message. */ }
  }
  return fallback;
}

export function formatToolResult(raw: string) {
  try {
    const result = JSON.parse(raw);
    const error = result?.ok === false ? readError(result.error) : null;
    return error ? formatError(error) : raw;
  } catch { return raw; }
}

export async function checkedFetch(url: string, requestInit: RequestInit): Promise<Response> {
  const response = await fetch(url, requestInit);
  if (!response.ok) {
    const body = await response.clone().json().catch(() => null);
    const error = readError(body?.error);
    if (error) throw new RequestError(error);
    throw new Error("请求失败，请检查服务状态或重新加载会话。");
  }
  return response;
}
