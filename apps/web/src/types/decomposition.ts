export interface PlannedSubtask {
  id: string
  key: string
  title: string
  description: string
  assignedAgentId: string
  assignedAgentName: string
  assignmentRationale: string
  requiredCapabilities: string[]
  dependsOn: string[]
  deliverable: string
  outputType: string
  completionCriteria: string[]
  order: number
  status: 'pending' | 'ready' | 'blocked'
}

export interface Decomposition {
  id: string
  taskId: string
  version: number
  status: 'ready' | 'needs_team_reselection' | 'failed' | 'unsupported' | 'superseded' | 'needs_redecomposition'
  teamSelectionId: string | null
  objectiveSummary: string
  subtasks: PlannedSubtask[]
  issues: { code: string; message: string; affectedSubtasks: string[]; requiredCapabilities: string[] }[]
}
