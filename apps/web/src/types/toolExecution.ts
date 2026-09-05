export type ToolName = 'workspace.list' | 'workspace.read' | 'workspace.write' | 'workspace.report'
export interface ToolStep { tool: ToolName; path: string; content?: string | null; expectedContentHash?: string | null }
export interface ToolScope { workspaceId: string; allowedTools: ToolName[]; readPrefixes: string[]; writePrefixes: string[]; maximumBytes: number; maximumSteps: number }
export interface WorkspaceInfo { workspaceId: string; displayName: string; allowedTools: ToolName[]; readPrefixes: string[]; writePrefixes: string[]; ready: boolean; reasonCode: string | null }
export interface ToolArtifact { artifactId: string; executionId: string; taskId: string; relativePath: string; contentHash: string; byteCount: number; mediaType: string }
export interface ToolArtifactContent extends ToolArtifact { content: string }
export interface ToolExecution {
  executionId: string; sourceExecutionId: string; sourceTaskId: string; taskId: string; runtimeRunId: string; targetAgentId: string
  workspaceId: string; planHash: string; scope: ToolScope; stage: 'preparing' | 'queued' | 'running' | 'completed' | 'failed' | 'paused'
  steps: { stepIndex: number; tool: ToolName; path: string; status: string; observation?: { content?: string | null; entries: string[]; byteCount: number; written: boolean } | null; artifactId?: string | null; failureCode?: string | null }[]
  artifacts: ToolArtifact[]; failureCode?: string | null; createdAt: string; updatedAt: string; completedAt?: string | null
}
