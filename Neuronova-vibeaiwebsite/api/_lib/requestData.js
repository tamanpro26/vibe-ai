export function boundedHistory(value, maxChars = 12_000) {
  if (!Array.isArray(value)) return []
  const result = []
  let remaining = maxChars
  for (const turn of value.slice(-8).reverse()) {
    const role = turn?.role === 'assistant' ? 'assistant' : turn?.role === 'user' ? 'user' : null
    const content = typeof turn?.content === 'string'
      ? turn.content.trim().slice(0, Math.min(6000, remaining))
      : ''
    if (role && content) {
      result.unshift({ role, content })
      remaining -= content.length
    }
    if (remaining <= 0) break
  }
  return result
}
