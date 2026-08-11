import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

test.describe('@capability release controls', () => {
  test('Vercel headers prevent framing and active embedded objects', () => {
    const config = JSON.parse(readFileSync(new URL('../vercel.json', import.meta.url), 'utf8'))
    const headers = config.headers[0].headers
    const csp = headers.find((item) => item.key === 'Content-Security-Policy')?.value || ''
    expect(csp).toContain("frame-ancestors 'none'")
    expect(csp).toContain("object-src 'none'")
    expect(csp).toContain('https://awaited-pipefish-42.accounts.dev')
  })

  test('tablet route keeps the Capability Hub primary action reachable', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 })
    await page.route('**/api/actions', (route) => route.fulfill({ json: { items: [] } }))
    await page.route('**/api/capabilities', (route) => route.fulfill({ json: { items: [], activation_mode: 'manual_only' } }))
    await page.goto('/#/capabilities')
    await expect(page.getByRole('heading', { name: 'Give the whole AI team better ways to work.' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Start with a bundle' })).toBeInViewport()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
    expect(overflow).toBeLessThanOrEqual(1)
  })
})
