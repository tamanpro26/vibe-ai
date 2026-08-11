import { expect, test } from '@playwright/test'

test.describe('@capability Capability Hub boundary', () => {
  test('is protected by the same authentication boundary as chat and projects', async ({ page }) => {
    await page.goto('/#/capabilities')
    await expect(page).toHaveURL(/#\/capabilities/)
    await expect(page.locator('body')).not.toContainText('Capability service is unavailable')
    await expect(page.locator('[aria-label="Loading your account"], .auth-page').first()).toBeVisible({ timeout: 15000 })
  })

  test('keeps the protected route free of horizontal overflow on a phone', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/#/capabilities')
    await page.waitForLoadState('networkidle')
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
    expect(overflow).toBeLessThanOrEqual(1)
  })
})
