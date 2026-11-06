export interface GoalMessage { id: string; role: 'user'; content: string; createdAt: string }
export interface Goal { id: string; conversationId: string; taskId: string; title: string; disposition: 'draft'; createdAt: string; messages: GoalMessage[] }
