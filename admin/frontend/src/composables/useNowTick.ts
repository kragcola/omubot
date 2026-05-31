import { onActivated, onDeactivated, ref } from 'vue'
import { useIntervalFn } from '@vueuse/core'

/**
 * Sanctioned relative-time ticker for the admin console.
 *
 * Returns a `now` ref (epoch ms) that updates every `intervalMs`, driving
 * re-render of relative-time labels ("刚刚 / N 分钟前") between data events.
 * Consume it reactively — read `now.value` inside a computed/template (even a
 * bare `void now.value` to subscribe) and the dependent re-renders on each tick.
 *
 * keepAlive-aware: under `<KeepAlive>` (router `meta.keepAlive: true`),
 * `onUnmounted` / scope-dispose does NOT fire when a page is cached — only
 * `onDeactivated`. This composable pauses the interval on deactivate and
 * resumes (with an immediate catch-up) on activate, so a cached page stops
 * ticking in the background instead of leaking a live timer. `useIntervalFn`
 * itself clears on scope dispose, so no manual `clearInterval` is needed.
 *
 * This is the one timer pattern admin views should use for wall-clock refresh.
 * See docs/admin-runtime-interaction.md for the full convention.
 *
 * @param intervalMs tick cadence in ms (default 30s — fine for minute-grained
 *   relative time; use 20s only where sub-minute freshness is visible).
 */
export function useNowTick(intervalMs = 30_000) {
  const now = ref(Date.now())

  const { pause, resume } = useIntervalFn(() => {
    now.value = Date.now()
  }, intervalMs)

  onDeactivated(pause)
  onActivated(() => {
    now.value = Date.now()
    resume()
  })

  return { now }
}
