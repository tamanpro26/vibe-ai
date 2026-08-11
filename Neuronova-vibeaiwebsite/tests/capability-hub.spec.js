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

async function mockCapabilityApi(page) {
  let installed = false
  await page.route('**/api/actions', (route) => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/capabilities', async (route) => {
    if (route.request().method() === 'POST') installed = true
    await route.fulfill({ json: {
      items: [{ ...capability, installed, installation_id: installed ? 'install-1' : null }],
      activation_mode: 'manual_only',
    } })
  })
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
})
