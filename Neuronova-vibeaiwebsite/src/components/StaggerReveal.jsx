import { Fragment, useEffect, useRef, useState } from 'react'

/*
 * Word-by-word clip reveal for one key claim line, not body copy generally.
 * Anthropic/OpenAI reserve this for a single load-bearing sentence per
 * section; applying it to every paragraph would slow reading down and turn
 * a considered device into a tic. Each word rises out of its own
 * overflow-hidden mask via a plain CSS transition, reading as typeset
 * rather than animated.
 *
 * Built on a native IntersectionObserver + CSS class toggle -- the same
 * mechanism App.jsx's useReveal() already drives every other reveal on the
 * page with, verified working via live browser testing. An earlier version
 * used Motion's whileInView/viewport props instead: verified via Playwright
 * that every word stayed frozen at its initial transform indefinitely (up
 * to 2s past scroll-into-view, across several viewport-margin configs) --
 * whileInView never fired for these deeply-nested per-word motion.span
 * elements in this Motion version. Rebuilding on the project's own
 * already-proven reveal mechanism side-steps that rather than chasing it
 * further.
 *
 * `text` must be a plain string -- this owns the markup for it (including
 * <em>/<strong> via a tiny inline parser) rather than accepting arbitrary
 * JSX, since word-splitting arbitrary React children safely isn't worth
 * the complexity for one hero claim line.
 */
export default function StaggerReveal({ text, className }) {
  const ref = useRef(null)
  const [inView, setInView] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setInView(true)
          io.unobserve(el)
        }
      },
      { threshold: 0.12 },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])

  // Minimal <em>/<strong> support -- enough for this project's copy without
  // pulling in a markdown parser for two tags.
  const segments = text.split(/(<\/?(?:em|strong)>)/g)
  let tag = null
  const words = []
  for (const seg of segments) {
    if (seg === '<em>' || seg === '<strong>') {
      tag = seg.slice(1, -1)
      continue
    }
    if (seg === '</em>' || seg === '</strong>') {
      tag = null
      continue
    }
    for (const w of seg.split(' ')) {
      if (w) words.push({ w, tag })
    }
  }

  return (
    <p className={`${className}${inView ? ' is-in' : ''}`} ref={ref}>
      {words.map(({ w, tag: t }, i) => {
        const Wrap = t || 'span'
        return (
          <Fragment key={`${w}-${i}`}>
            <span className="stagger-mask">
              <span className="stagger-word" style={{ transitionDelay: `${i * 0.028}s` }}>
                <Wrap>{w}</Wrap>
              </span>
            </span>
            {i < words.length - 1 ? ' ' : null}
          </Fragment>
        )
      })}
    </p>
  )
}
