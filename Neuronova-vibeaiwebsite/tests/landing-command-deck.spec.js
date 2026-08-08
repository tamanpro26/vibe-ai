import { expect, test as base } from '@playwright/test'

const FUTURE_GROUPS = [
  ['Command Deck trace @trace', 'the interactive orchestration trace'],
  ['Responsive Command Deck @responsive', 'phone and tablet composition'],
  ['Landing motion preferences @motion', 'reduced and disabled motion modes'],
  ['Landing sound consent @sound', 'opt-in sound cues and failure states'],
  ['Landing accessibility @a11y', 'axe and keyboard coverage'],
]

const test = base.extend({
  runtimeGuard: [
    async ({ page, baseURL }, use) => {
      const pageErrors = []
      const firstPartyRequestFailures = []
      const firstPartyOrigin = new URL(baseURL).origin

      page.on('pageerror', (error) => pageErrors.push(error.message))
      page.on('requestfailed', (request) => {
        if (new URL(request.url()).origin !== firstPartyOrigin) return
        firstPartyRequestFailures.push(
          `${request.method()} ${request.url()} (${request.failure()?.errorText || 'unknown failure'})`,
        )
      })

      await use()

      expect(pageErrors, 'uncaught browser errors').toEqual([])
      expect(firstPartyRequestFailures, 'failed first-party requests').toEqual([])
    },
    { auto: true },
  ],
})

test.describe('Landing composition @composition', () => {
  test('renders the current landing and exposes its workspace entry point', async ({ page }) => {
    await page.goto('/')

    await expect(
      page.getByRole('heading', {
        level: 1,
        name: 'Complex work, orchestrated across models.',
      }),
    ).toBeVisible()
    await expect(page.getByRole('link', { name: 'Open the AI workspace' }).first()).toBeVisible()
  })

  test('keeps the workspace CTA behind the existing hash-route boundary', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: 'Open the AI workspace' }).first().click()

    await expect(page).toHaveURL(/#\/chat$/)
    await expect(page.locator('.app')).toHaveCount(0)
  })
})

for (const [name, scope] of FUTURE_GROUPS) {
  test.describe(name, () => {
    test.skip(`is reserved for ${scope}`, () => {})
  })
}
