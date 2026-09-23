import { expect, test } from '@playwright/test';

test('local archived forecasts render real model output without invented facts', async ({
  page,
}) => {
  test.skip(
    !process.env.LOCAL_DASHBOARD_URL,
    'Optional integration with local original CSVs and saved weights',
  );
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(process.env.LOCAL_DASHBOARD_URL!);
  await expect(page.getByText('LOCAL SCADA + ML', { exact: true })).toBeVisible();
  await expect(page.getByText('Verified source data', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Status unavailable', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Archive date')).toHaveValue('2026-02-01');
  await page.getByLabel('Archive date').fill('2026-02-02');
  await expect(page.locator('.dashboard-date')).toContainText('2 February 2026');
  await expect(page.locator('.summary-card').first()).toContainText('0 matched hours');
  await page.getByRole('button', { name: 'Wind speed', exact: true }).click();
  await expect(page.getByText('SYNTHETIC DEMO', { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2),
  ).toBeTruthy();
  await page.screenshot({ path: `.qa/local-${test.info().project.name}.png`, fullPage: true });
});
