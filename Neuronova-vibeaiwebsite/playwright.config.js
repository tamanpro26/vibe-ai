import { defineConfig, devices } from '@playwright/test'

const localBaseURL = 'http://127.0.0.1:4173'
const externalBaseURL = process.env.PLAYWRIGHT_BASE_URL?.replace(/\/$/, '')

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  workers: 3,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'line' : 'list',
  outputDir: 'node_modules/.cache/playwright-test-results',
  use: {
    baseURL: externalBaseURL || localBaseURL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: externalBaseURL
    ? undefined
    : {
        command: 'npm run preview -- --host 127.0.0.1 --port 4173 --strictPort',
        url: localBaseURL,
        reuseExistingServer: !process.env.CI,
      },
})
