// Shared contracts between renderer, Electron and Agent Core (mirrors backend models).
export type SchedulerMode = "fixed" | "round_robin" | "adaptive";
export type TaskStatus = "pending" | "running" | "completed" | "failed" | "cancelled";
export type ErrorType = "MODEL_ERROR" | "TOOL_ERROR" | "TIMEOUT" | "VALIDATION_ERROR"
  | "PERMISSION_ERROR" | "DEPENDENCY_ERROR" | "ENVIRONMENT_ERROR";
export type MessageType = "TASK_ASSIGNMENT" | "TASK_RESULT" | "QUESTION" | "ANSWER"
  | "REVIEW_REQUEST" | "REVIEW_RESULT" | "ERROR_REPORT" | "STATUS_UPDATE";
export interface SchedulerWeights {
  capability: number; success: number; availability: number; priority: number;
  cost: number; latency: number; load: number;
}
export const DEFAULT_SCHEDULER_WEIGHTS: SchedulerWeights = {
  capability: 0.30, success: 0.20, availability: 0.10, priority: 0.10,
  cost: 0.10, latency: 0.10, load: 0.10,
};
