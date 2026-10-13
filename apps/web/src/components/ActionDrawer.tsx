import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { NavIcon } from './NavIcon'

/** Native top layer supplies inert background, Escape dismissal and focus return. */
export function ActionDrawer({ open, title, onClose, children, closeLabel }: { open: boolean; title: string; closeLabel?: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (open && !dialog.current?.open) {
      previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      dialog.current?.showModal()
    }
    if (!open && dialog.current?.open) dialog.current.close()
  }, [open])
  useEffect(() => {
    return () => {
      // The DOM removes a modal when its owner unmounts. Do not close it here:
      // Strict Mode replays effect cleanup while the dialog is still mounted.
      if (!document.querySelector('dialog[open]') && previousFocus.current?.isConnected) previousFocus.current.focus()
    }
  }, [])
  const containTab = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key !== 'Tab' || (event.target instanceof Element && event.target.closest('dialog') !== event.currentTarget)) return
    const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
    )).filter(element => !element.hidden && element.getClientRects().length > 0)
    const first = controls[0]
    const last = controls.at(-1)
    if (!first) { event.preventDefault(); event.currentTarget.focus(); return }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
  return <dialog ref={dialog} className="action-drawer" aria-label={title} onKeyDown={containTab} onCancel={event => { event.preventDefault(); onClose() }} onClose={() => { if (open) onClose() }}>
    <header className="action-drawer-header"><strong>{title}</strong><button className="secondary" aria-label={closeLabel ?? `Close ${title}`} onClick={onClose}><NavIcon name="close"/></button></header>
    <div className="action-drawer-body">{children}</div>
  </dialog>
}
