/**
 * Keyboard handler for roving-selection widgets — WAI-ARIA `tablist` and
 * `radiogroup`. Attach to the container's `@keydown`; it moves focus AND
 * selection between the `role="tab"` / `role="radio"` children with
 * Left/Right (and Up/Down) arrows plus Home/End (automatic-activation model).
 *
 * Selection reuses each child's existing `@click` handler via a synthetic
 * click, so callers don't thread a select callback through — just wire the
 * buttons' click as usual and add roving `:tabindex` (0 on the selected
 * child, -1 on the rest) so Tab lands on the active one and arrows do the
 * rest. See docs/admin-a11y-checklist.md §2.
 */
export function onRovingKeydown(event: KeyboardEvent) {
  const NAV_KEYS = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']
  if (!NAV_KEYS.includes(event.key)) return

  const container = event.currentTarget as HTMLElement
  const items = Array.from(
    container.querySelectorAll<HTMLElement>('[role="tab"], [role="radio"]'),
  )
  if (!items.length) return

  let idx = items.indexOf(document.activeElement as HTMLElement)
  if (idx === -1) {
    idx = items.findIndex(
      el => el.getAttribute('aria-selected') === 'true'
        || el.getAttribute('aria-checked') === 'true',
    )
  }
  if (idx === -1) idx = 0

  let next = idx
  switch (event.key) {
    case 'ArrowLeft':
    case 'ArrowUp':
      next = (idx - 1 + items.length) % items.length
      break
    case 'ArrowRight':
    case 'ArrowDown':
      next = (idx + 1) % items.length
      break
    case 'Home':
      next = 0
      break
    case 'End':
      next = items.length - 1
      break
  }

  event.preventDefault()
  const target = items[next]
  target.focus()
  target.click() // automatic activation — reuse the child's existing @click
}
