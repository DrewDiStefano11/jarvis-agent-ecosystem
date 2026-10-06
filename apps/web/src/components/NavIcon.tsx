import type { CSSProperties } from 'react'

const paths: Record<string, string> = {
  overview: 'M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9',
  tasks: 'M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1',
  agents: 'M16 21v-3a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v3M16 4a4 4 0 0 1 0 8M22 21v-3a4 4 0 0 0-3-4M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  approvals: 'M9 12l2 2 4-4M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  activity: 'M5 3h14v18H5zM8 7h8M8 11h8M8 15h4',
  planning: 'M3 5h18v16H3zM3 9h18M7 3v4M17 3v4',
  lab: 'M9 3h6M10 3v7L4 20h16l-6-10V3M7 15h10',
  office: 'M3 7h18v14H3zM8 7V3h8v4M3 12h18M10 12v3h4v-3',
  system: 'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',
  attention: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4',
  collapse: 'M14 6l-6 6 6 6M20 6l-6 6 6 6',
}

export function NavIcon({ name, style }: { name: string; style?: CSSProperties }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={style}><path d={paths[name] ?? paths.tasks}/></svg>
}
