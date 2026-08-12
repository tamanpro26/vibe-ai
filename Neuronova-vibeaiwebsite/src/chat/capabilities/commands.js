export function buildCapabilityCommands(items = []) {
  return items
    .filter((item) => item.installed && item.manifest?.capability_id)
    .map((item) => ({
      capabilityId: item.manifest.capability_id,
      name: item.manifest.name || item.manifest.capability_id,
      description: item.manifest.description || '',
      kindLabel: item.manifest.kind === 'approved_action'
        ? 'Plugin action'
        : item.manifest.kind === 'bundle' ? 'Bundle' : 'Skill',
      searchText: `${item.manifest.capability_id} ${item.manifest.name || ''}`.toLowerCase(),
    }))
    .sort((left, right) => left.capabilityId.localeCompare(right.capabilityId))
}

export function matchCapabilityCommands(text, commands) {
  const match = /^\/([^\s]*)$/.exec(text.trim())
  if (!match) return []
  const query = match[1].toLowerCase()
  const matches = []
  for (const command of commands) {
    if (command.searchText.includes(query)) matches.push(command)
    if (matches.length === 8) break
  }
  return matches
}

// Which capability a REGENERATE should run under. It reproduces the original
// request, so this reads the stored message and never the toolbar's current
// selection: a turn sent on Auto persists `capabilityId: null`, and inheriting
// the toolbar would silently rerun an old plain answer through a capability the
// user never chose for it. null (explicitly Auto) and undefined (sent before
// this field existed) both mean "no capability" -- unknown provenance degrades
// to none rather than guessing, since capabilities include approved plugin
// actions. Shared by ChatApp and ProjectWorkspace so the rule has one home.
export function capabilityForRegenerate(message) {
  return message?.capabilityId || ''
}

export function parseCapabilityCommand(text, commands) {
  const match = /^\/([a-z0-9-]+)(?:\s+([\s\S]*))?$/i.exec(text.trim())
  if (!match) return null
  const command = commands.find((item) => item.capabilityId === match[1].toLowerCase())
  if (!command) return null
  return {
    capabilityId: command.capabilityId,
    prompt: (match[2] || '').trim(),
  }
}
