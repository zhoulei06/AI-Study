export type PlanProgress = {
  id: string;
  goal: string;
  phase: "executing" | "finalizing" | "completed" | "failed";
  version: number;
  replans: number;
  steps: { id: string; task: string; status: "pending" | "running" | "succeeded" | "failed" }[];
};

export function readPlan(value: unknown): PlanProgress | null {
  if (!value || typeof value !== "object") return null;
  const p = value as Record<string, unknown>;
  if (typeof p.id !== "string" || typeof p.goal !== "string" ||
    !["executing", "finalizing", "completed", "failed"].includes(String(p.phase)) ||
    !Number.isInteger(p.version) || !Number.isInteger(p.replans) || !Array.isArray(p.steps) ||
    !p.steps.every((s: unknown) => {
      if (!s || typeof s !== "object") return false;
      const step = s as Record<string, unknown>;
      return typeof step.id === "string" && typeof step.task === "string" &&
        ["pending", "running", "succeeded", "failed"].includes(String(step.status));
    })) return null;
  return value as PlanProgress;
}
