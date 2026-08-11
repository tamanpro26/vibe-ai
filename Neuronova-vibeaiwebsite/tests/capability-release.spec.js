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

  test('tablet route keeps primary authentication handoff reachable', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 })
    await page.goto('/#/capabilities')
    await expect(page.locator('.auth-page')).toBeVisible({ timeout: 15000 })
    await expect(page.getByRole('tab', { name: 'Log in' })).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
    expect(overflow).toBeLessThanOrEqual(1)
  })
})
