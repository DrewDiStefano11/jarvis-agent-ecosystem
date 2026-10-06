import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { TaskDependencyGraph, dependencyLevels, executionMatchesPlan } from '../src/components/TaskDependencyGraph'
import { PlannedWork } from '../src/components/PlannedWork'
import type { Decomposition } from '../src/types/decomposition'
import type { Coordination } from '../src/types/coordination'

const plan: Decomposition = { id: 'plan-1', taskId: 'task-1', version: 1, status: 'ready', teamSelectionId: 'team-1', objectiveSummary: 'Compare choices', issues: [], subtasks: [
  { id: 'a', key: 'research', title: 'Compare markets', description: 'Inspect sources', assignedAgentId: 'researcher', assignedAgentName: 'Market Researcher', assignmentRationale: 'Capability match', requiredCapabilities: ['research.market'], dependsOn: [], deliverable: 'Sourced comparison', outputType: 'analysis', completionCriteria: ['Five sources'], order: 0, status: 'ready' },
  { id: 'b', key: 'finance', title: 'Analyze economics', description: 'Inspect costs', assignedAgentId: 'analyst', assignedAgentName: 'Financial Analyst', assignmentRationale: 'Capability match', requiredCapabilities: ['business.financial-analysis'], dependsOn: ['research'], deliverable: 'Cost model', outputType: 'analysis', completionCriteria: ['Bounded estimates'], order: 1, status: 'blocked' },
] }
const execution: Coordination = { id: 'coordinator', taskId: plan.taskId, decompositionId: plan.id, status: 'active', blockedReason: null,
  nodes: plan.subtasks.map(node => ({ subtaskId: node.id, key: node.key, assignedAgentId: node.assignedAgentId, status: node.key === 'research' ? 'succeeded' : 'running', attemptCount: 1, retryEligibleAt: null, resultSummary: null, failureDetail: null, provider: null, model: null })),
  synthesis: { status: 'pending', attemptCount: 0, summary: null, failureDetail: null, retryEligibleAt: null, inputSubtaskIds: [] },
}

test('layout handles forward references and parallel dependencies without implying sequence', () => {
  const third = { ...plan.subtasks[1]!, id: 'c', key: 'prototype' }
  const levels = dependencyLevels([plan.subtasks[1]!, third, plan.subtasks[0]!])
  expect(Array.from(levels!.entries()).sort()).toEqual([['finance', 1], ['prototype', 1], ['research', 0]])
})

test('missing dependencies, cycles, duplicate keys and oversized graphs safely fall back', () => {
  expect(dependencyLevels([{ ...plan.subtasks[0]!, dependsOn: ['missing'] }])).toBeNull()
  expect(dependencyLevels([{ ...plan.subtasks[0]!, dependsOn: ['finance'] }, plan.subtasks[1]!])).toBeNull()
  expect(dependencyLevels([plan.subtasks[0]!, plan.subtasks[0]!])).toBeNull()
  expect(dependencyLevels(Array.from({ length: 13 }, (_, i) => ({ ...plan.subtasks[0]!, id: String(i), key: String(i) })))).toBeNull()
})

test('execution overlay requires plan, task, subtask and assignment identity', () => {
  expect(executionMatchesPlan(plan, execution)).toBe(true)
  expect(executionMatchesPlan(plan, { ...execution, decompositionId: 'old-plan' })).toBe(false)
  expect(executionMatchesPlan(plan, { ...execution, taskId: 'old-task' })).toBe(false)
  expect(executionMatchesPlan(plan, { ...execution, nodes: [execution.nodes[0]!] })).toBe(false)
  expect(executionMatchesPlan(plan, { ...execution, nodes: execution.nodes.map(node => ({ ...node, assignedAgentId: 'reassigned' })) })).toBe(false)
  expect(executionMatchesPlan(plan, { ...execution, nodes: [execution.nodes[0]!, execution.nodes[0]!] })).toBe(false)
})

test('matched execution renders actual node statuses and synthesis, never a progress percentage', () => {
  render(<TaskDependencyGraph plan={plan} execution={execution} onInspect={vi.fn()}/>)
  expect(screen.getByText(/Execution matched to this plan/)).toBeInTheDocument()
  expect(screen.getAllByText('running', { exact: false }).length).toBeGreaterThan(0)
  expect(screen.getByText('Manager synthesis · pending')).toBeInTheDocument()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(screen.getAllByRole('button', { name: 'Inspect subtask Analyze economics' })[0]).toHaveAccessibleDescription(/Financial Analyst running\s*· Attempt 1/)
})

test('another plan keeps readiness and does not show synthesis or claim execution', () => {
  render(<TaskDependencyGraph plan={plan} execution={{ ...execution, decompositionId: 'old-plan' }} onInspect={vi.fn()}/>)
  expect(screen.getByText(/Execution does not match this plan/)).toBeInTheDocument()
  expect(screen.queryByText('Manager synthesis · pending')).not.toBeInTheDocument()
  expect(screen.queryByText('running')).not.toBeInTheDocument()
})

test('inspection opens original deliverables and focuses the selected subtask', () => {
  render(<PlannedWork record={plan} execution={execution} error=""/>)
  const details = document.querySelector<HTMLDetailsElement>('.subtask-details')!
  expect(details.open).toBe(false)
  fireEvent.click(screen.getAllByRole('button', { name: 'Inspect subtask Analyze economics' })[0]!)
  expect(details.open).toBe(true)
  expect(document.activeElement).toHaveTextContent('Cost model')
  expect(within(details).getByText('Bounded estimates')).toBeInTheDocument()
})

test('graph and list selection plus zoom have keyboard accessible names', () => {
  render(<TaskDependencyGraph plan={plan} execution={null} onInspect={vi.fn()}/>)
  fireEvent.click(screen.getByRole('button', { name: 'List' }))
  expect(screen.getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Graph' }))
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in dependencies' }))
  expect(screen.getByRole('button', { name: 'Reset dependency zoom' })).toHaveTextContent('110%')
  fireEvent.click(screen.getByRole('button', { name: 'Reset dependency zoom' }))
  expect(screen.getByRole('button', { name: 'Reset dependency zoom' })).toHaveTextContent('100%')
})
