import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

const edge = join(process.env['ProgramFiles(x86)'] || '', 'Microsoft/Edge/Application/msedge.exe');
const localBrowserPath = process.env.AGENTFLOW_BROWSER_EXECUTABLE ||
  (process.platform === 'win32' && existsSync(edge) ? edge : undefined);
const liveBaseURL = process.env.AGENTFLOW_LIVE_API_URL;

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  reporter: 'list',
  use: {
    baseURL: liveBaseURL || 'http://127.0.0.1:4179',
    browserName: 'chromium',
    headless: true,
    launchOptions: localBrowserPath ? { executablePath: localBrowserPath } : undefined,
    trace: 'retain-on-failure',
  },
  webServer: liveBaseURL ? undefined : {
    command: 'pnpm exec vite --host 127.0.0.1 --port 4179 --strictPort',
    url: 'http://127.0.0.1:4179',
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
});
