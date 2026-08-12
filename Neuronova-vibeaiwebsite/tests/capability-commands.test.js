import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildCapabilityCommands,
  capabilityForRegenerate,
  matchCapabilityCommands,
  parseCapabilityCommand,
} from '../src/chat/capabilities/commands.js'

const installed = {
  id: 'version-1',
  installed: true,
  manifest: {
    capability_id: 'research-analyst',
    name: 'Research analyst',
    description: 'Find and verify evidence.',
    kind: 'instruction_skill',
  },
}

test('buildCapabilityCommands exposes installed capabilities only', () => {
  const commands = buildCapabilityCommands([
    installed,
    { ...installed, id: 'version-2', installed: false, manifest: { ...installed.manifest, capability_id: 'draft-skill' } },
  ])

  assert.deepEqual(commands, [{
    capabilityId: 'research-analyst',
    name: 'Research analyst',
    description: 'Find and verify evidence.',
    kindLabel: 'Skill',
    searchText: 'research-analyst research analyst',
  }])
})

test('parseCapabilityCommand removes a known slash command and preserves the request', () => {
  const commands = buildCapabilityCommands([installed])

  assert.deepEqual(parseCapabilityCommand('/research-analyst compare these sources', commands), {
    capabilityId: 'research-analyst',
    prompt: 'compare these sources',
  })
  assert.equal(parseCapabilityCommand('/unknown compare these sources', commands), null)
})

test('capabilityForRegenerate uses message provenance, never the current toolbar', () => {
  // The turn was sent WITH a capability -- regenerate must reuse that one.
  assert.equal(capabilityForRegenerate({ capabilityId: 'research-analyst' }), 'research-analyst')

  // The regression this guards: a turn sent on Auto persists null. Falling back
  // to the toolbar meant regenerating an old plain answer could run it through
  // whichever capability happened to be selected at the time.
  assert.equal(capabilityForRegenerate({ capabilityId: null }), '')

  // Sent before the field existed -- provenance unknown, so degrade to none
  // rather than guess. Capabilities include approved plugin actions, so
  // silently escalating INTO one is the dangerous direction.
  assert.equal(capabilityForRegenerate({}), '')
  assert.equal(capabilityForRegenerate(undefined), '')
})

test('matchCapabilityCommands searches command names and display names', () => {
  const commands = buildCapabilityCommands([installed])

  assert.equal(matchCapabilityCommands('/res', commands)[0].capabilityId, 'research-analyst')
  assert.equal(matchCapabilityCommands('/analyst', commands)[0].capabilityId, 'research-analyst')
  assert.deepEqual(matchCapabilityCommands('ordinary prompt', commands), [])
})
