import { expect, test } from '@playwright/test'

const capability = {
  id: 'version-1',
  installed: false,
  installation_id: null,
  content_digest: `sha256:${'a'.repeat(64)}`,
  review_state: 'reviewed',
  manifest: {
    capability_id: 'research-analyst',
    name: 'Research analyst',
    description: 'Finds and checks evidence before synthesis.',
    version: '1.0.0',
    kind: 'instruction_skill',
    trust: 'vibeai_builtin',
    risk: 'low',
    supported_tasks: ['research', 'fact_checking'],
    permissions: [],
    services: [],
  },
}

async function mockCapabilityApi(page, installedByDefault = false) {
  let installed = installedByDefault
  await page.route('**/api/actions', (route) => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/capabilities', async (route) => {
    if (route.request().method() === 'POST') installed = true
    await route.fulfill({ json: {
      items: [{ ...capability, installed, installation_id: installed ? 'install-1' : null }],
      activation_mode: 'manual_only',
    } })
  })
}

async function mockTeam(page) {
  let lastRequest = null
  await page.route('**/api/team', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: { ok: true } })
      return
    }
    lastRequest = route.request().postDataJSON()
    await route.fulfill({ json: {
      text: 'Capability command received.',
      capability_snapshot: { selected: [{ capability_id: 'research-analyst', reason: 'explicit' }] },
    } })
  })
  return () => lastRequest
}

test.describe('@capability Capability Hub', () => {
  test('renders and installs a real capability in the signed-in product surface', async ({ page }) => {
    await mockCapabilityApi(page)
    await page.goto('/#/capabilities')
    await expect(page.getByRole('heading', { name: 'Give the whole AI team better ways to work.' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Research analyst' })).toBeVisible()
    await page.getByRole('button', { name: 'Install' }).click()
    await expect(page.getByText('Capability installed.')).toBeVisible()
    await expect(page.getByText('Installed', { exact: true })).toBeVisible()
  })

  test('keeps capability cards and controls free of phone overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockCapabilityApi(page)
    await page.goto('/#/capabilities')
    await expect(page.getByRole('heading', { name: 'Research analyst' })).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    const install = page.getByRole('button', { name: 'Install' })
    await install.scrollIntoViewIfNeeded()
    await expect(install).toBeInViewport()
  })

  test('invokes an installed capability through the slash command menu', async ({ page }) => {
    await mockCapabilityApi(page, true)
    const lastTeamRequest = await mockTeam(page)
    await page.goto('/#/chat')

    const composer = page.getByPlaceholder(/Message VibeAI/i)
    await composer.fill('/')
    await expect(page.getByRole('listbox', { name: 'Capability commands' })).toBeVisible()
    await page.getByRole('option', { name: /research-analyst/i }).click()
    await expect(composer).toHaveValue('/research-analyst ')
    await composer.fill('/research-analyst draft a concise outline')
    await page.getByRole('button', { name: 'Send message' }).click()

    await expect.poll(lastTeamRequest).not.toBeNull()
    expect(lastTeamRequest().prompt).toBe('draft a concise outline')
    expect(lastTeamRequest().capability_ids).toEqual(['research-analyst'])
  })

  test('lets users disable slash commands from Settings', async ({ page }) => {
    await mockCapabilityApi(page, true)
    await page.goto('/#/chat')
    await page.getByRole('button', { name: 'Account menu' }).click()
    await page.getByRole('menuitem', { name: 'Settings' }).click()
    await page.getByRole('button', { name: 'Capabilities' }).click()
    await page.getByRole('button', { name: 'Disable slash commands' }).click()
    await page.getByRole('button', { name: 'Close settings' }).click()

    const composer = page.getByPlaceholder(/Message VibeAI/i)
    await composer.fill('/')
    await expect(page.getByRole('listbox', { name: 'Capability commands' })).toHaveCount(0)
  })

  test('forwards slash-invoked capabilities from a project chat', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('vibeai_projects_e2e-user', JSON.stringify([{
        id: 'project-1',
        name: 'Launch notes',
        description: '',
        instructions: '',
        memory: '',
        files: [],
        chats: [],
        starred: false,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }]))
    })
    await mockCapabilityApi(page, true)
    const lastTeamRequest = await mockTeam(page)
    await page.goto('/#/projects/project-1')

    const composer = page.getByPlaceholder(/Start a new chat/i)
    await composer.fill('/')
    await page.getByRole('option', { name: /research-analyst/i }).click()
    await composer.fill('/research-analyst draft a project brief')
    await page.getByRole('button', { name: 'Send message' }).click()

    await expect.poll(lastTeamRequest).not.toBeNull()
    expect(lastTeamRequest().prompt).toBe('draft a project brief')
    expect(lastTeamRequest().project_id).toBe('project-1')
    expect(lastTeamRequest().capability_ids).toEqual(['research-analyst'])
  })

  test('keeps the slash command menu reachable on a phone', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await mockCapabilityApi(page, true)
    await page.goto('/#/chat')
    await page.getByPlaceholder(/Message VibeAI/i).fill('/')

    const menu = page.getByRole('listbox', { name: 'Capability commands' })
    await expect(menu).toBeVisible()
    await expect(menu).toBeInViewport()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
    expect(overflow).toBeLessThanOrEqual(1)
  })
})
