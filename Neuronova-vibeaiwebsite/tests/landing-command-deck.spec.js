import { expect, test as base } from '@playwright/test'

const FUTURE_GROUPS = [
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
  test('presents one Command Deck with coordinated specialists and verification', async ({ page }) => {
    await page.goto('/')

    const heading = page.getByRole('heading', { level: 1 })
    const workspaceLink = page.getByRole('link', { name: 'Open the AI workspace' }).first()
    const commandDeck = page.locator('#command-deck')

    await expect(heading).toBeVisible()
    await expect(heading).toContainText(/complex work/i)
    await expect(workspaceLink).toBeVisible()
    await expect(workspaceLink).toHaveAttribute('href', '#/chat')
    await expect(commandDeck).toHaveCount(1)
    await expect(commandDeck).toContainText(/specialist agents/i)
    await expect(commandDeck).toContainText(/verified/i)
    await expect(
      page.getByRole('heading', { name: 'See the work before you trust the answer.' }),
    ).toHaveCount(0)
  })

  test('keeps the workspace CTA behind the existing hash-route boundary', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: 'Open the AI workspace' }).first().click()

    await expect(page).toHaveURL(/#\/chat$/)
    await expect(page.locator('.app')).toHaveCount(0)
  })
})

test.describe('Command Deck trace @trace', () => {
  test.beforeEach(async ({ page }) => {
    await page.clock.install({ time: new Date('2026-08-09T12:00:00Z') })
    await page.goto('/')
  })

  test('runs the ordered specialist sequence to a verified outcome', async ({ page }) => {
    const deck = page.locator('#command-deck')
    const activeStage = deck.locator('[aria-current="step"]')

    await expect(deck.getByRole('list', { name: 'Orchestration stages' })).toBeVisible()
    await expect(deck.getByRole('button', { name: 'Start trace' })).toBeEnabled()
    await expect(activeStage).toHaveCount(0)

    await deck.getByRole('button', { name: 'Start trace' }).click()
    await expect(deck).toHaveAttribute('data-run-status', 'running')
    await expect(activeStage).toHaveCount(1)
    await expect(activeStage).toContainText('Planning')
    await expect(deck.getByRole('status')).toContainText(/planning.*strategy lead/i)

    for (const stage of ['Routing', 'Critique', 'Verification']) {
      await page.clock.runFor(1200)
      await expect(activeStage).toHaveCount(1)
      await expect(activeStage).toContainText(stage)
    }

    await page.clock.runFor(1200)
    await expect(deck).toHaveAttribute('data-run-status', 'complete')
    await expect(deck.getByText('Specialists aligned. Checks complete.', { exact: true })).toBeVisible()
    await expect(deck.getByRole('status')).toHaveText(
      'Verification complete. Specialists aligned. Checks complete.',
    )
  })

  test('pauses, resumes, supports manual inspection, and replays without moving focus', async ({ page }) => {
    const deck = page.locator('#command-deck')
    const activeStage = deck.locator('[aria-current="step"]')
    const pause = deck.getByRole('button', { name: 'Pause trace' })

    await deck.getByRole('button', { name: 'Start trace' }).click()
    await page.clock.runFor(1200)
    await expect(activeStage).toContainText('Routing')

    await pause.focus()
    await page.clock.runFor(1200)
    await expect(activeStage).toContainText('Critique')
    await expect(pause).toBeFocused()

    await pause.click()
    await expect(deck).toHaveAttribute('data-run-status', 'paused')
    await page.clock.runFor(5000)
    await expect(activeStage).toContainText('Critique')

    const planningStage = deck.getByRole('button', { name: 'Inspect Planning stage' })
    await planningStage.click()
    await expect(planningStage).toBeFocused()
    await expect(deck).toHaveAttribute('data-run-status', 'paused')
    await expect(activeStage).toContainText('Planning')

    await deck.getByRole('button', { name: 'Resume trace' }).click()
    await page.clock.runFor(1200)
    await expect(activeStage).toContainText('Routing')
    await page.clock.runFor(3600)
    await expect(deck).toHaveAttribute('data-run-status', 'complete')

    await deck.getByRole('button', { name: 'Replay trace' }).click()
    await expect(activeStage).toContainText('Planning')
    await page.clock.runFor(1200)
    await expect(activeStage).toContainText('Routing')
    await expect(activeStage).toHaveCount(1)
  })

  test('disposes an active run when the landing route unmounts', async ({ page }) => {
    const deck = page.locator('#command-deck')

    await deck.getByRole('button', { name: 'Start trace' }).click()
    await page.clock.runFor(400)
    await page.evaluate(() => {
      window.location.hash = '#/chat'
    })

    await expect(page).toHaveURL(/#\/chat$/)
    await expect(deck).toHaveCount(0)
    await page.clock.runFor(5000)

    await page.goto('/')
    await expect(page.locator('#command-deck')).toHaveAttribute('data-run-status', 'idle')
    await expect(page.locator('#command-deck [aria-current="step"]')).toHaveCount(0)
  })
})

test.describe('Responsive Command Deck @responsive', () => {
  for (const viewport of [
    { name: 'tablet', width: 820, height: 1180 },
    { name: 'phone', width: 390, height: 844 },
  ]) {
    test(`${viewport.name} keeps the deck and primary action in frame`, async ({ page }) => {
      await page.setViewportSize(viewport)
      await page.goto('/')

      await expect(page.locator('#command-deck')).toBeVisible()
      await expect(page.getByRole('link', { name: 'Open the AI workspace' }).first()).toBeVisible()
      await expect(page.getByRole('button', { name: 'Start trace' })).toBeVisible()
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      expect(overflow).toBeLessThanOrEqual(1)
    })
  }
})

test.describe('Landing motion preferences @motion', () => {
  test('honors the operating-system reduced-motion preference with a static complete trace', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/')

    await expect(page.locator('.command-deck-landing')).toHaveAttribute('data-effective-motion', 'reduced')
    await expect(page.locator('#command-deck')).toHaveAttribute('data-run-status', 'complete')
    await expect(page.locator('#command-deck [aria-current="step"]')).toContainText('Verification')
  })

  for (const preference of ['reduced', 'off']) {
    test(`honors the root ${preference} preference without autoplay`, async ({ page }) => {
      await page.goto('/')
      await page.evaluate((value) => {
        document.documentElement.dataset.motion = value
      }, preference)

      await expect(page.locator('.command-deck-landing')).toHaveAttribute(
        'data-effective-motion',
        preference,
      )
      await expect(page.locator('#command-deck')).toHaveAttribute('data-run-status', 'complete')
      await expect(page.getByRole('button', { name: 'Replay trace' })).toBeDisabled()
    })
  }

  test('cancels a live trace when motion is reduced', async ({ page }) => {
    await page.goto('/')
    const deck = page.locator('#command-deck')
    await deck.getByRole('button', { name: 'Start trace' }).click()
    await expect(deck).toHaveAttribute('data-run-status', 'running')

    await page.evaluate(() => {
      document.documentElement.dataset.motion = 'reduced'
    })

    await expect(deck).toHaveAttribute('data-run-status', 'complete')
    await expect(page.locator('.command-deck-landing')).toHaveAttribute('data-effective-motion', 'reduced')
  })
})

for (const [name, scope] of FUTURE_GROUPS) {
  test.describe(name, () => {
    test.skip(`is reserved for ${scope}`, () => {})
  })
}
