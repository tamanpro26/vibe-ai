/*
 * In-flight reply registry.
 *
 * Lives at MODULE scope, deliberately: a reply must outlive both the
 * component that started it and whichever conversation happens to be on
 * screen. Before this existed, a request's lifecycle was tied to both, and
 * two things killed a perfectly good answer:
 *
 *   1. Switching conversations (or hitting New chat) called handleStop(),
 *      which set a stop flag checked AFTER the await -- so a reply that had
 *      already come back successfully was thrown away on arrival.
 *   2. Navigating to the landing page unmounted ChatApp, and with it the
 *      state the result would have been written into.
 *
 * Both are fixed by the same move: the fetch and the WRITE happen here, and
 * the write goes straight to storage. Whether anything is mounted, and what
 * the user is looking at, are now presentation questions that cannot affect
 * whether the answer survives.
 *
 * Note the animation is unaffected by any of this: dispatchEngineReply
 * already awaits the complete reply, and the typewriter in the component is
 * cosmetic decoration replayed over a finished string. So there is no
 * partial-token state to preserve here -- only the final result.
 */

const runs = new Map() // runId -> { status, reply, error }
const listeners = new Set()

function emit() {
  for (const fn of listeners) fn()
}

/** Subscribe to run state changes. Returns an unsubscribe function. */
export function subscribeRuns(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export const getRun = (runId) => runs.get(runId)
export const isRunning = (runId) => runs.get(runId)?.status === 'running'

/** Ids of every run currently in flight — lets a surface show "still working
 *  over there" for conversations the user isn't looking at. */
export const runningIds = () =>
  [...runs.entries()].filter(([, r]) => r.status === 'running').map(([id]) => id)

/** Runs that have completed and not yet been consumed, as [id, run] pairs.
 *  Their replies are already persisted -- a surface consumes these only to
 *  animate the arrival, then calls clearRun. */
export const finishedRuns = () =>
  [...runs.entries()].filter(([, r]) => r.status === 'done')

/** Runs that failed, as [id, run] pairs. */
export const failedRuns = () => [...runs.entries()].filter(([, r]) => r.status === 'error')

/**
 * Start a run. `dispatch()` resolves to the reply; `persist(reply)` writes it
 * wherever it belongs (localStorage) and is called EVEN IF nothing is
 * mounted -- that is the whole point.
 *
 * Concurrent runs across different conversations are fine and expected; a
 * second run for the SAME id is ignored rather than racing the first.
 */
export async function startRun(runId, { dispatch, persist }) {
  if (isRunning(runId)) return
  runs.set(runId, { status: 'running' })
  emit()

  try {
    const reply = await dispatch()

    // Cancelled while in flight: drop the result rather than writing it into
    // a conversation the user explicitly stopped.
    if (runs.get(runId)?.status === 'cancelled') {
      runs.delete(runId)
      emit()
      return
    }

    // Persist FIRST, then announce. If a listener throws while rendering,
    // the answer is already saved.
    try {
      persist(reply)
    } catch (err) {
      console.error('[run] persist failed:', err)
    }
    runs.set(runId, { status: 'done', reply })
    emit()
  } catch (err) {
    runs.set(runId, { status: 'error', error: String(err?.message || err) })
    emit()
  }
}

/** Explicit user stop. Only the Stop button should call this -- navigating
 *  away must NOT, which was the original bug. */
export function cancelRun(runId) {
  const cur = runs.get(runId)
  if (!cur) return
  runs.set(runId, { ...cur, status: 'cancelled' })
  emit()
}

/** Clear a finished/errored run once a surface has consumed it. */
export function clearRun(runId) {
  const cur = runs.get(runId)
  if (cur && cur.status !== 'running') {
    runs.delete(runId)
    emit()
  }
}
