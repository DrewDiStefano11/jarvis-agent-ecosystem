export type AgentStatus = 'idle'|'assigned'|'planning'|'thinking'|'researching'|'executing_tool'|'waiting_for_model'|'waiting_for_agent'|'waiting_for_approval'|'reviewing'|'paused'|'failed'|'retrying'|'delivering'|'completed'|'offline'
export type TaskStatus = 'queued'|'planning'|'assigned'|'in_progress'|'waiting'|'waiting_for_approval'|'under_review'|'revision_requested'|'paused'|'failed'|'retrying'|'completed'|'cancelled'
export type ConnectionStatus = 'connecting'|'connected'|'reconnecting'|'offline'|'error'

export interface Agent {
  id:string; schemaVersion:string; name:string; role:string; description:string; goals:string[]; departmentId:string; managerId:string|null;
  status:AgentStatus; currentTaskId:string|null; queuedTaskIds:string[]; progress:number; statusMessage:string; capabilities:string[];
  allowedTools:string[]; deniedTools:string[]; approvalPolicy:Record<string,unknown>; memoryAccess:Record<string,unknown>;
  performance:{completionRate:number; accuracyScore:number; averageCompletionTime:number; failedTaskCount:number; userCorrectionCount:number; reviewerScore:number; reliabilityScore:number};
  resourceProfile:Record<string,unknown>; office:{zone:string;deskId:string;spriteIdentifier:string;displayPosition:{x:number;y:number};currentAnimationState:string;currentDestination:string|null;isInMeeting:boolean};
  createdAt:string;updatedAt:string;version:string;deploymentStatus:string;isTemporary:boolean
}
export interface Department {id:string;name:string;description:string;managerAgentId:string|null;agentIds:string[]}
export interface Task {id:string;schemaVersion:string;title:string;description:string;request:string;parentTaskId:string|null;childTaskIds:string[];projectId:string|null;createdBy:string;assignedManagerId:string|null;assignedAgentIds:string[];priority:'low'|'medium'|'high'|'urgent';status:TaskStatus;progress:number;statusMessage:string;dependencies:{taskId:string;type:string}[];blockedBy:string[];approvalIds:string[];artifactIds:string[];result:string|null;error:{code:string;message:string}|null;retryCount:number;maxRetries:number;createdAt:string;startedAt:string|null;updatedAt:string;completedAt:string|null}
export interface Approval {id:string;taskId:string;requestedByAgentId:string;actionType:string;title:string;description:string;reason:string;riskLevel:'green'|'yellow'|'orange'|'red'|'black';affectedResources:string[];exactActionPreview:string;expectedOutcome:string;reversalMethod:string;expiresAt:string;status:'pending'|'approved'|'rejected'|'expired'|'cancelled';reviewedBy:string|null;reviewedAt:string|null;decisionNote:string|null;createdAt:string}
export interface AuditEvent {id:string;timestamp:string;eventType:string;actorAgentId:string|null;taskId:string|null;previousState:string|null;newState:string|null;summary:string;correlationId:string;sequenceNumber:number;payload:Record<string,unknown>;artifactIds:string[];approvalId:string|null}
export interface Notification {id:string;title:string;message:string;level:string;isRead:boolean;taskId:string|null;createdAt:string}
export interface Artifact {id:string;taskId:string;name:string;type:string;summary:string;simulatedPath:string;createdAt:string}
export interface SystemStatus {status:string;environment:string;apiSchemaVersion:string;seedDataVersion:string;emergencyStop:boolean;simulator:{state:'idle'|'running'|'paused'|'completed'|'failed';currentStep:number;totalSteps:number;accelerated:boolean};resources:{name:string;value:string;label:string}[];lastSynchronizedAt:string}
export interface Snapshot {departments:Department[];agents:Agent[];tasks:Task[];approvals:Approval[];artifacts:Artifact[];notifications:Notification[];auditEvents:AuditEvent[];emergencyStop:boolean}
export interface EventEnvelope {eventId:string;schemaVersion:string;eventType:string;timestamp:string;sequenceNumber:number;correlationId:string;taskId:string|null;agentId:string|null;source:string;payload:Record<string,unknown>}
export interface ApiEnvelope<T> {data:T;meta:{schemaVersion:string}}
