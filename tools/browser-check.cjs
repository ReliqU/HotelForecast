// Optional QA: set PLAYWRIGHT_MODULE and EDGE_PATH, build the project, then run with Node.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.EDGE_PATH, headless: true,
    args: ['--disable-gpu', '--no-sandbox'],
  });
  const root = path.resolve(__dirname, '..');
  const location = process.env.DASHBOARD_URL || pathToFileURL(path.join(root, 'build', 'forecast.html')).href;
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, acceptDownloads: true });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(location);
  assert.equal(await page.locator('h1').textContent(), 'Прогноз для гостиниц');
  assert.equal(await page.locator('#mainChart rect.pick').count(), 14);
  const desktop = path.join(root, 'build', 'qa-dashboard.png');
  await page.screenshot({ path: desktop, animations: 'disabled' });
  const start = await page.locator('#selectedDate').textContent();
  await page.locator('#day').focus();
  await page.keyboard.press('ArrowRight');
  assert.notEqual(await page.locator('#selectedDate').textContent(), start);
  await page.locator('#hotel').selectOption('park');
  await page.locator('#target').selectOption('adr');
  assert.match(await page.locator('#detail .val').textContent(), /₽/);
  await page.locator('#test').click();
  assert.equal(await page.locator('#test').getAttribute('aria-pressed'), 'true');
  assert.match(await page.locator('#detail').textContent(), /Абсолютная ошибка/);
  const downloading = page.waitForEvent('download');
  await page.locator('#download').click();
  const download = await downloading;
  assert.equal(download.suggestedFilename(), 'park-adr-test.csv');
  assert.match(fs.readFileSync(await download.path(), 'utf8'), /date;forecast;seasonal;actual/);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(root, 'build', 'qa-mobile.png'), animations: 'disabled' });
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('Dashboard OK: desktop, mobile, filters, keyboard, chart, holdout and CSV');
})().catch(e => { console.error(e); process.exit(1); });
