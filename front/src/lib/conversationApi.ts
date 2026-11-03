import { readError, RequestError } from "./errors";

export const agentUrl = import.meta.env.VITE_AGENT_URL ?? "http://127.0.0.1:8010/agent";
const historyUrl = `${agentUrl.replace(/\/$/, "")}/conversations`;

export async function historyRequest<T>(path = "", method = "GET", body?: unknown): Promise<T> {
  // Reads can be retried while the Agent is starting. Never retry a create:
  // the server might have created it even if its response was lost.
  const attempts = method === "GET" ? 4 : 1;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let response: Response;
    try {
      response = await fetch(`${historyUrl}${path}`, {
        method,
        signal: AbortSignal.timeout(5000),
        ...(body === undefined ? {} : {
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        }),
      });
    } catch {
      if (attempt === attempts - 1) {
        throw new Error("暂时无法连接 Agent 服务，请等待服务启动完成后重新加载会话。");
      }
      await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
      continue;
    }
    if (response.ok) return response.json() as Promise<T>;
    if (![502, 503, 504].includes(response.status) || attempt === attempts - 1) {
      const body = await response.json().catch(() => null);
      const error = readError(body?.error);
      if (error) throw new RequestError(error);
      throw new Error(`会话请求失败（${response.status}），请重新加载会话。`);
    }
    await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
  }
  throw new Error("历史加载失败，请重新加载会话。");
}
