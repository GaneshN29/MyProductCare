import { randomBytes, randomUUID } from 'node:crypto';
import { copyFile, mkdir, readdir, stat, unlink, writeFile } from 'node:fs/promises';
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
const email = `demo-${randomUUID()}@example.invalid`;
const password = randomBytes(32).toString('base64url');
const warrantyFile = path.join(outputDirectory, 'demo-warranty.txt');
const formatDate = (date) => [
  date.getFullYear(),
  String(date.getMonth() + 1).padStart(2, '0'),
  String(date.getDate()).padStart(2, '0'),
].join('-');
const warrantyExpiry = new Date();
warrantyExpiry.setDate(warrantyExpiry.getDate() + 21);
const warrantyExpiryDate = formatDate(warrantyExpiry);
const warrantyText = `MYPRODUCTCARE DEMO WARRANTY

Fictional warranty document for the MyProductCare product assistant demo.
This sample coverage expires on ${warrantyExpiryDate}.
For a real product, confirm all dates and terms with its manufacturer.
`;

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
await writeFile(warrantyFile, warrantyText, 'utf8');

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
  deviceScaleFactor: 1,
  recordVideo: { dir: recordingDirectory, size: { width: 1440, height: 1100 } },
});
const page = await context.newPage();
let token = '';

try {
  await page.goto(frontendUrl, { waitUntil: 'networkidle' });
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password').fill(password);
  await page.waitForTimeout(1400);
  await page.getByRole('button', { name: /Create account/ }).click();

  await page.getByRole('heading', { name: 'Everything you own,' }).waitFor();
  token = await page.evaluate(() => localStorage.getItem('myproductcare.session') ?? '');
  if (!token) {
    throw new Error('The demo account was created but no sign-in session was stored.');
  }
  await page.waitForTimeout(2400);

  const today = new Date();
  const dateAfterDays = (days) => {
    const date = new Date(today);
    date.setDate(date.getDate() + days);
    return formatDate(date);
  };
  const addProduct = async ({ name, brand, model, category, purchaseDate, warrantyDate, description }) => {
    await page.getByRole('button', { name: /My products/ }).click();
    await page.waitForTimeout(1000);
    await page.getByLabel('Product name').fill(name);
    await page.getByLabel('Brand', { exact: true }).fill(brand);
    await page.getByLabel('Model', { exact: true }).fill(model);
    await page.getByLabel('Category', { exact: true }).fill(category);
    await page.locator('input[name="purchaseDate"]').fill(purchaseDate);
    await page.locator('input[name="warrantyExpiryDate"]').fill(warrantyDate);
    await page.getByLabel('Description').fill(description);
    await page.waitForTimeout(1200);
    await page.getByRole('button', { name: 'Save product' }).click();
    await page.getByRole('heading', { name: 'Product documents' }).waitFor();
    await page.waitForTimeout(1200);
  };

  await addProduct({
    name: 'MyProductCare Demo Washer',
    brand: 'MyProductCare',
    model: 'DEMO-8',
    category: 'Washing machine',
    purchaseDate: dateAfterDays(-300),
    warrantyDate: warrantyExpiryDate,
    description: 'Family laundry appliance · 8 kg capacity',
  });
  await addProduct({
    name: 'MyProductCare Demo Refrigerator',
    brand: 'MyProductCare',
    model: 'DEMO-FR2',
    category: 'Refrigerator',
    purchaseDate: dateAfterDays(-120),
    warrantyDate: dateAfterDays(610),
    description: 'Kitchen appliance · warranty coverage saved',
  });
  await addProduct({
    name: 'MyProductCare Demo Air Purifier',
    brand: 'MyProductCare',
    model: 'DEMO-AIR',
    category: 'Air purifier',
    purchaseDate: dateAfterDays(-45),
    warrantyDate: dateAfterDays(320),
    description: 'Living room appliance · filter care reference',
  });
  await page.getByRole('button', { name: /Overview/ }).click();
  await page.getByRole('heading', { name: 'All your products' }).waitFor();
  await page.getByRole('heading', { name: 'MyProductCare Demo Washer', exact: true }).waitFor();
  await page.getByRole('heading', { name: 'MyProductCare Demo Refrigerator', exact: true }).waitFor();
  await page.getByRole('heading', { name: 'MyProductCare Demo Air Purifier', exact: true }).waitFor();
  await page.waitForTimeout(3200);

  await page.getByRole('button', { name: /My products/ }).click();
  await page.locator('.product-list-row').filter({
    hasText: 'MyProductCare Demo Washer',
  }).click();
  await page.getByRole('button', { name: /Documents/ }).click();
  await page.getByRole('heading', { name: 'Product documents' }).waitFor();

  const productResponse = await fetch(`${apiUrl}/api/products`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!productResponse.ok) {
    throw new Error(`Could not load products (HTTP ${productResponse.status}).`);
  }
  const products = await productResponse.json();
  const product = products.find((item) => item.name === 'MyProductCare Demo Washer');
  if (!product) {
    throw new Error('The demo product was not created.');
  }

  await page.locator('input[type="file"]').setInputFiles(
    path.join(projectDirectory, 'sample_data', 'sample_washer_manual.txt'),
  );
  await page.getByLabel('Document type').selectOption('user_manual');
  await page.waitForTimeout(1400);
  await page.getByRole('button', { name: /Upload and process/ }).click();
  const manualRow = page.locator('.document-row').filter({
    hasText: 'sample_washer_manual.txt',
  });
  await manualRow.getByText('ready', { exact: true }).waitFor({ timeout: 180_000 });
  await page.waitForTimeout(1800);

  await page.locator('input[type="file"]').setInputFiles(warrantyFile);
  await page.getByLabel('Document type').selectOption('warranty');
  await page.waitForTimeout(1400);
  await page.getByRole('button', { name: /Upload and process/ }).click();
  const warrantyRow = page.locator('.document-row').filter({
    hasText: 'demo-warranty.txt',
  });
  await warrantyRow.getByText('ready', { exact: true }).waitFor({ timeout: 180_000 });
  await warrantyRow.getByText(new RegExp(warrantyExpiryDate)).waitFor();
  await page.waitForTimeout(1500);
  await warrantyRow.getByRole('button', { name: 'Review this date' }).click();
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: 'Save date' }).click();
  await page.getByText(/Expires in 21 days/).waitFor();
  await page.waitForTimeout(2300);

  await page.getByRole('button', { name: /AI assistant/ }).click();
  const questions = [
    'What does error code 4C mean?',
    'What is the maximum load capacity?',
    'How should I clean the detergent drawer?',
  ];
  for (let index = 0; index < questions.length; index += 1) {
    await page.getByLabel('Ask a product question').fill(questions[index]);
    await page.waitForTimeout(1300);
    await page.getByRole('button', { name: 'Send question' }).click();
    const answers = page.locator('.chat-turn:not(.user-turn) .turn-content > p');
    await answers.nth(index).waitFor({ timeout: 180_000 });
    const citations = page.locator('.chat-turn:not(.user-turn) details.citation-card');
    await citations.nth(index).waitFor({ timeout: 30_000 });
    await citations.nth(index).locator('summary').click();
    await page.locator('.chat-body').evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await page.waitForTimeout(3000);
  }
  await page.getByRole('button', { name: /Overview/ }).click();
  await page.getByRole('heading', { name: 'All your products' }).waitFor();
  await page.waitForTimeout(2800);
  await page.screenshot({ path: screenshotPath, fullPage: true });
} finally {
  await context.close();
  await browser.close();
  await unlink(warrantyFile).catch((error) => {
    if (error.code !== 'ENOENT') {
      throw error;
    }
  });
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
