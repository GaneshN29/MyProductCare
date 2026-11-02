import { copyFile, mkdir, readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectDirectory = path.resolve(scriptDirectory, '../..');
const outputDirectory = path.join(projectDirectory, 'demo-output');
const videoPath = path.join(outputDirectory, 'myproductcare-demo.webm');
const screenshotPath = path.join(projectDirectory, 'docs', 'demo-screenshot.png');
const recordingDirectory = path.join(outputDirectory, new Date().toISOString().replaceAll(':', '-'));
const apiUrl = process.env.MYPRODUCTCARE_API_URL ?? 'http://localhost:8000';
const frontendUrl = process.env.MYPRODUCTCARE_FRONTEND_URL ?? 'http://localhost:4200';
const token = process.env.APP_API_TOKEN;

if (!token) {
  throw new Error('Set APP_API_TOKEN in the project .env file before recording the demo.');
}

for (const [label, url] of [
  ['API health', `${apiUrl}/api/health`],
  ['Angular app', frontendUrl],
]) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${label} is not ready at ${url} (HTTP ${response.status}).`);
  }
}

await mkdir(outputDirectory, { recursive: true });
await mkdir(recordingDirectory, { recursive: true });
await mkdir(path.dirname(screenshotPath), { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
  deviceScaleFactor: 1,
  recordVideo: { dir: recordingDirectory, size: { width: 1440, height: 1100 } },
});
const page = await context.newPage();

try {
  await page.goto(frontendUrl, { waitUntil: 'networkidle' });
  const baseUrlField = page.getByLabel('API base URL');
  if (await baseUrlField.isVisible().catch(() => false)) {
    await baseUrlField.fill(apiUrl);
    await page.getByLabel('API bearer token').fill(token);
    await page.getByRole('button', { name: /Connect workspace/ }).click();
    await page.waitForLoadState('networkidle');
  }

  await page.getByRole('heading', { name: 'Everything you own,' }).waitFor();
  await page.waitForTimeout(700);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await page.getByRole('button', { name: /My products/ }).click();

  const demoProductName = 'MyProductCare Demo Washer';
  const productResponse = await fetch(`${apiUrl}/api/products`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!productResponse.ok) {
    throw new Error(`Could not load products (HTTP ${productResponse.status}).`);
  }
  const products = await productResponse.json();
  let product = products.find((item) => item.name === demoProductName);
  if (!product) {
    await page.getByLabel('Product name').fill(demoProductName);
    await page.getByLabel('Brand').fill('MyProductCare');
    await page.getByLabel('Model').fill('DEMO-8');
    await page.getByLabel('Category').fill('Washing machine');
    await page.getByRole('button', { name: 'Save product' }).click();
    const updatedResponse = await fetch(`${apiUrl}/api/products`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const updatedProducts = await updatedResponse.json();
    product = updatedProducts.find((item) => item.name === demoProductName);
    if (!product) {
      throw new Error('The demo product was not created.');
    }
  }

  await page.getByRole('button', { name: /Documents/ }).click();
  await page.getByLabel('Product').selectOption(product.id);
  const documentsResponse = await fetch(
    `${apiUrl}/api/products/${encodeURIComponent(product.id)}/documents`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const documents = await documentsResponse.json();
  if (!documents.some((item) => item.filename === 'sample_washer_manual.txt')) {
    await page.locator('input[type="file"]').setInputFiles(
      path.join(projectDirectory, 'sample_data', 'sample_washer_manual.txt'),
    );
    await page.getByLabel('Document type').selectOption('user_manual');
    await page.getByRole('button', { name: /Upload and process/ }).click();
  }
  const readyRow = page.locator('.document-row').filter({
    hasText: 'sample_washer_manual.txt',
  });
  await readyRow.getByText('ready', { exact: true }).waitFor({ timeout: 180_000 });

  await page.getByRole('button', { name: /AI assistant/ }).click();
  await page.getByLabel('Ask a product question').fill('What does error code 4C mean?');
  await page.getByRole('button', { name: 'Send question' }).click();
  await page.locator('.chat-turn:not(.user-turn) .turn-content > p').last().waitFor({
    timeout: 180_000,
  });
  await page.locator('.chat-body').evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: screenshotPath, fullPage: true });
} finally {
  await context.close();
  await browser.close();
}

const videoName = (await readdir(recordingDirectory)).find((name) => name.endsWith('.webm'));
const video = videoName ? path.join(recordingDirectory, videoName) : '';
if (!video || !(await stat(video)).size) {
  throw new Error('Browser closed without creating a demo video.');
}
if (video !== videoPath) {
  await copyFile(video, videoPath);
}
console.log(`Demo video: ${videoPath}`);
console.log(`Demo screenshot: ${screenshotPath}`);
