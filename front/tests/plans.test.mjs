import assert from "node:assert/strict";
import test from "node:test";
import { readPlan } from "../src/lib/plans.ts";

test("accepts persisted and streamed plan progress with the same contract", () => {
  const progress = { id: "task-1", goal: "比较天气", phase: "executing", version: 1, replans: 0,
    steps: [{ id: "s1", task: "获取天气", status: "running" }] };
  assert.deepEqual(readPlan(JSON.parse(JSON.stringify(progress))), progress);
  assert.deepEqual(readPlan({ ...progress, phase: "completed" })?.phase, "completed");
});

test("rejects malformed plan snapshots", () => {
  for (const value of [null, {}, { id: "t", steps: "bad" },
    { id: "t", goal: "g", phase: "executing", version: 1, replans: 0,
      steps: [{ id: "s", task: "x", status: "invented" }] }]) {
    assert.equal(readPlan(value), null);
  }
});
