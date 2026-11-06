export interface PlanningConsent { policyId: string; policyHash: string }
export interface GoalPlanningPolicy {
  id: string | null; revision: number; enabled: boolean; policyHash: string | null
  maximumGoals: number; admittedGoals: number; expiresAt: string | null
  unavailableReason: string | null
}
export interface GoalPlanningAdmission { goalId: string; taskId: string; runId: string; state: string; errorCode: string | null }
