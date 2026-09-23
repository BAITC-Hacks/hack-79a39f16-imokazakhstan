import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { createDemoDashboard, summarizeTurbine } from '../src/data';

const now = new Date('2026-09-23T10:30:00Z');
test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: now });
});

test('dashboard has accessible labels and readable contrast', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('SYNTHETIC DEMO', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Generation', exact: true })).toBeVisible();
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
});

test('verified API response renders its values and supports day selection', async ({ page }) => {
  await page.route('**/runtime-config.json', (route) =>
    route.fulfill({
      json: { dataMode: 'api', apiUrl: '/api/dashboard', backendUrl: 'http://localhost:8501' },
    }),
  );
  const requested: string[] = [];
  await page.route('**/api/dashboard?*', (route) => {
    const period = new URL(route.request().url()).searchParams.get('period') as
      'today' | 'yesterday';
    requested.push(period);
    const fixture = createDemoDashboard(period, now);
    fixture.provenance = 'verified_original';
    fixture.turbines[0].name = 'Connected turbine 01';
    fixture.turbines[0].history[0].actualPower = 1.7;
    fixture.turbines.reverse();
    return route.fulfill({ json: fixture });
  });
  await page.goto('/');
  await expect(page.getByText('Verified source data', { exact: true })).toBeVisible();
  await expect(page.locator('.selection-heading')).toContainText('Connected turbine 01');
  await expect(page.getByRole('button', { name: 'Select turbine 1', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.getByText('SYNTHETIC DEMO', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Yesterday', exact: true }).click();
  await expect(page.locator('.summary-card').first()).toContainText('24 matched hours');
  expect(requested).toEqual(['today', 'yesterday']);
});

test('offline demo: turbine comparisons, history, CSV and weather outlook', async ({ page }) => {
  const externalRequests: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', (route) => {
    if (!new URL(route.request().url()).hostname.match(/^(127\.0\.0\.1|localhost)$/)) {
      externalRequests.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  await page.goto('/');
  await expect(page.getByText('SYNTHETIC DEMO', { exact: true })).toBeVisible();
  const first = summarizeTurbine(createDemoDashboard('today', now).turbines[0]);
  await expect(page.locator('.summary-card').first()).toContainText(first.actualEnergy!.toFixed(2));
  await expect(page.locator('.summary-card').first()).toContainText('15 matched hours');
  const marker = page.getByRole('button', { name: /^Select Demo site 02,/ });
  await marker.click();
  await expect(marker).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('complementary', { name: 'Selected turbine details' })).toContainText(
    'Demo site 02',
  );
  await expect(page.locator('.summary-card').nth(2)).toContainText('Below expected generation');
  await page.getByRole('button', { name: 'Yesterday', exact: true }).click();
  await expect(page.locator('.summary-card').first()).toContainText('24 matched hours');
  await expect(page.locator('.dashboard-date')).toContainText('22 September 2026');
  await page.getByRole('button', { name: 'Wind speed', exact: true }).click();
  await expect(page.locator('.chart-readout')).toContainText('m/s');
  await page.getByRole('button', { name: 'Temperature', exact: true }).click();
  await expect(page.locator('.chart-readout')).toContainText('°C');
  await page.getByRole('button', { name: 'Direction', exact: true }).click();
  await expect(page.locator('.chart-footer')).toContainText('0° / 360°');
  await page.getByRole('button', { name: 'View data table' }).click();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('table').locator('tbody tr')).toHaveCount(24);
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export CSV' }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('synthetic-turbine_2-2026-09-22.csv');
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  const csv = Buffer.concat(chunks).toString('utf8');
  expect(csv).toContain('provenance,turbine_id,time,actualPower,predictedPower');
  expect(csv.trim().split('\r\n')).toHaveLength(25);
  await expect(page.locator('.weather-card')).toHaveCount(5);
  await expect(page.locator('.weather-card').first()).toContainText('2.17');
  await page.getByRole('button', { name: 'All weather' }).click();
  await expect(page.locator('.weather-card')).toHaveCount(3);
  await page.locator('.weather-card').first().getByRole('button', { name: 'View outlook' }).click();
  await expect(page.locator('.event-description')).toContainText('Suggested action');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  expect(externalRequests).toEqual([]);
  expect(errors).toEqual([]);
});

test('street layer degrades clearly without internet and keeps marker keyboard focus', async ({
  page,
}) => {
  await page.route('https://tile.openstreetmap.org/**', (route) => route.abort());
  await page.goto('/');
  await page.getByRole('button', { name: 'Street', exact: true }).click();
  await expect(
    page.getByText('Street tiles are unavailable. The Site view works offline.'),
  ).toBeVisible();
  const marker = page.getByRole('button', { name: /^Select Demo site 02,/ });
  await marker.focus();
  await page.keyboard.press('Enter');
  await expect(marker).toHaveAttribute('aria-pressed', 'true');
  await expect(marker).toBeFocused();
  await page.getByRole('button', { name: 'Site', exact: true }).click();
  await expect(page.locator('.turbine-pin')).toHaveCount(2);
});

test('API failures stay visible and retry does not silently enable sample data', async ({
  page,
}) => {
  await page.route('**/runtime-config.json', (route) =>
    route.fulfill({ json: { dataMode: 'api', apiUrl: '/api/dashboard', backendUrl: '' } }),
  );
  let calls = 0;
  await page.route('**/api/dashboard?*', (route) => {
    calls++;
    return route.fulfill({ status: 503, json: { error: 'Unavailable' } });
  });
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('HTTP 503');
  await expect(page.getByText('SYNTHETIC DEMO', { exact: true })).toHaveCount(0);
  await expect(page.locator('.turbine-pin')).toHaveCount(0);
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('alert')).toContainText('HTTP 503');
  expect(calls).toBe(2);
});

test('midnight sample has neutral comparisons and no fabricated actual readings', async ({
  page,
}) => {
  await page.clock.setFixedTime(new Date('2026-09-22T19:30:00Z'));
  await page.goto('/');
  await expect(page.locator('.summary-card').first()).toContainText('0 matched hours');
  await expect(page.locator('.summary-card').nth(2)).toContainText(
    'Awaiting comparable observations',
  );
  await expect(page.locator('.summary-card .neutral')).toHaveCount(1);
  await expect(page.getByRole('button', { name: /No comparable data/ })).toHaveCount(2);
  await page.getByRole('button', { name: 'View data table' }).click();
  const actualCells = page.getByRole('table').locator('tbody tr td:first-of-type');
  expect(await actualCells.allTextContents()).toEqual(Array(24).fill('—'));
});

test('missing power does not hide independently available weather readings', async ({ page }) => {
  await page.route('**/runtime-config.json', (route) =>
    route.fulfill({ json: { dataMode: 'api', apiUrl: '/api/dashboard', backendUrl: '' } }),
  );
  const fixture = createDemoDashboard('today', now);
  const history = fixture.turbines[0].history;
  history.forEach((row) => {
    row.actualPower = null;
  });
  history[13].actualTemperature = 22.5;
  history[14].actualTemperature = null;
  history[14].actualWindSpeed = 9.5;
  history[12].actualWindDirection = 123;
  history[13].actualWindDirection = null;
  history[14].actualWindDirection = null;
  await page.route('**/api/dashboard?*', (route) => route.fulfill({ json: fixture }));
  await page.goto('/');
  const weather = page.locator('.detail-weather');
  await expect(weather).toContainText('22.5°');
  await expect(weather).toContainText('9.5 m/s');
  await expect(weather).toContainText('123°');
  await expect(weather).toContainText('13:00–14:00');
  await expect(weather).toContainText('14:00–15:00');
  await expect(weather).toContainText('12:00–13:00');
  await expect(page.locator('.summary-card').first()).toContainText('0 matched hours');
});

test('an unresponsive API times out with a usable retry control', async ({ page }) => {
  await page.route('**/runtime-config.json', (route) =>
    route.fulfill({ json: { dataMode: 'api', apiUrl: '/api/dashboard', backendUrl: '' } }),
  );
  let requested = false;
  await page.route('**/api/dashboard?*', () => {
    requested = true;
  });
  await page.goto('/');
  await expect.poll(() => requested).toBe(true);
  await page.clock.runFor(15_100);
  await expect(page.getByRole('alert')).toContainText('Loading timed out');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeEnabled();
});
