import { useEffect, useMemo, useRef, useState } from 'react'
import { useInView, animate, useReducedMotion } from 'motion/react'

/*
 * Counts up from 0 the first time it scrolls into view. Static text and
 * fade-reveal alone read as inert on a stats row -- premium SaaS sites treat
 * the number itself as the animated element, not just its container.
 *
 * Handles a leading/trailing non-digit wrapper ("$0", "10+") by animating
 * only the numeric core and keeping the wrapper static, so values that
 * aren't pure integers don't need a separate code path.
 */
export default function CountUp({ value }) {
  const ref = useRef(null)
  const isInView = useInView(ref, { once: true, margin: '-10% 0px' })
  const reduced = useReducedMotion()
  // Keep the parsed wrapper stable across animation renders. Recreating the
  // match array on each onUpdate used to restart the effect repeatedly.
  const match = useMemo(() => String(value).match(/^(\D*)(\d+)(\D*)$/), [value])
  const [display, setDisplay] = useState(reduced || !match ? value : `${match[1]}0${match[3]}`)

  useEffect(() => {
    if (!isInView || reduced || !match) return
    const [, prefix, numStr, suffix] = match
    const target = parseInt(numStr, 10)
    const controls = animate(0, target, {
      duration: 1.3,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setDisplay(`${prefix}${Math.round(v)}${suffix}`),
    })
    return () => controls.stop()
  }, [isInView, reduced, match])

  return <span ref={ref}>{display}</span>
}
