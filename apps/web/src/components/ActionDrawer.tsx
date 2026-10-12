import { useEffect, useRef, type ReactNode } from 'react'
import { NavIcon } from './NavIcon'

/** Native top layer supplies inert background, Escape dismissal and focus return. */
export function ActionDrawer({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal()
    if (!open && dialog.current?.open) dialog.current.close()
  }, [open])
  return <dialog ref={dialog} className="action-drawer" aria-label={title} onCancel={event => { event.preventDefault(); onClose() }} onClose={onClose}>
    <header className="action-drawer-header"><strong>{title}</strong><button className="secondary" aria-label={`Close ${title}`} onClick={onClose}><NavIcon name="close"/></button></header>
    <div className="action-drawer-body">{children}</div>
  </dialog>
}
