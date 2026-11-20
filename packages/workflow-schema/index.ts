// Workflow graph schema shared by the editor and (future) workflow engine.
export type WorkflowNodeKind = "Start" | "End" | "Agent" | "Tool" | "MCP" | "Condition"
  | "Parallel" | "Join" | "Loop" | "Verification" | "HumanApproval" | "SubWorkflow";
export interface WorkflowNode { id: string; kind: WorkflowNodeKind; label: string; config: Record<string, unknown>; position: { x: number; y: number }; }
export interface WorkflowEdge { id: string; source: string; target: string; condition?: string; }
export interface WorkflowGraph { nodes: WorkflowNode[]; edges: WorkflowEdge[]; }

export function validateWorkflowGraph(g: WorkflowGraph): string[] {
  const errors: string[] = [];
  const kinds = g.nodes.map((n) => n.kind);
  if (!kinds.includes("Start")) errors.push("missing Start node");
  if (!kinds.includes("End")) errors.push("missing End node");
  const ids = new Set(g.nodes.map((n) => n.id));
  for (const e of g.edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) errors.push(`illegal edge ${e.id}`);
  }
  const connected = new Set(g.edges.flatMap((e) => [e.source, e.target]));
  for (const n of g.nodes) {
    if (g.nodes.length > 1 && !connected.has(n.id)) errors.push(`unreachable node ${n.id}`);
  }
  return errors;
}
