import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const API_BASE_URL = process.env.E2E_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
const E2E_USER = process.env.E2E_USER ?? 'dev@localhost';
const TRUST_SOURCE = /Structured source|Document source|Hybrid source|Source unknown/;
const TRUST_EXACTNESS = /Validated exact value|Insufficient evidence|Estimated answer|Context answer/;
const TRUST_CONFIDENCE = /High confidence|Medium confidence|Abstained/;

async function resolveDataProductId(api: APIRequestContext): Promise<string> {
  const fixedId = process.env.E2E_DATA_PRODUCT_ID?.trim();
  if (fixedId) return fixedId;

  const headers = {
    'Sf-Context-Current-User': E2E_USER,
    'X-Dev-User': E2E_USER,
  };

  const listResponse = await api.get(`${API_BASE_URL}/data-products?page=1&per_page=20`, { headers });
  if (listResponse.ok()) {
    const payload = (await listResponse.json()) as {
      data?: Array<{ id?: string; tables?: string[] }>;
    };
    const withTables = (payload.data ?? []).find((row) => typeof row.id === 'string' && (row.tables?.length ?? 0) > 0);
    if (withTables?.id) return withTables.id;
    const first = (payload.data ?? []).find((row) => typeof row.id === 'string');
    if (first?.id) return first.id;
  }

  const createResponse = await api.post(`${API_BASE_URL}/data-products`, {
    headers: {
      ...headers,
      'Content-Type': 'application/json',
    },
    data: {
      name: `Hybrid E2E ${Date.now()}`,
      description: 'Auto-created for hybrid trust UX tests.',
      database_reference: 'E2E_DB',
      schemas: ['PUBLIC'],
      tables: ['PUBLIC.E2E_TEST_TABLE'],
    },
  });
  if (!createResponse.ok()) {
    throw new Error(`Failed to resolve or create test data product. HTTP ${createResponse.status()}`);
  }
  const created = (await createResponse.json()) as { id?: string };
  if (!created.id) {
    throw new Error('Create data product API did not return an id.');
  }
  return created.id;
}

async function ensureTrustContractVisible(page: Page, dataProductId: string): Promise<void> {
  await page.goto(`/data-products/${dataProductId}`);
  await expect(page.getByText(/ekai|ekaiX/i).first()).toBeVisible({ timeout: 30_000 });

  const exactnessChip = page.getByText(TRUST_EXACTNESS).first();
  if ((await exactnessChip.count()) > 0) {
    await expect(exactnessChip).toBeVisible();
    return;
  }

  const input = page.locator('textarea[placeholder="Reply to ekaiX..."]').first();
  await expect(input).toBeVisible({ timeout: 30_000 });
  await expect(input).toBeEnabled({ timeout: 60_000 });

  await input.fill('Summarize this data product and include evidence context.');
  await input.press('Enter');

  await expect(page.getByText(TRUST_SOURCE).first()).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText(TRUST_EXACTNESS).first()).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText(TRUST_CONFIDENCE).first()).toBeVisible({ timeout: 180_000 });
}

test.describe('Hybrid trust UX', () => {
  test.describe.configure({ timeout: 240_000 });

  let dataProductId: string;

  test.beforeAll(async ({ request }) => {
    dataProductId = await resolveDataProductId(request);
  });

  test('shows source/exactness/confidence trust contract chips', async ({ page }) => {
    await ensureTrustContractVisible(page, dataProductId);

    await expect(page.getByText(TRUST_SOURCE).first()).toBeVisible();
    await expect(page.getByText(TRUST_EXACTNESS).first()).toBeVisible();
    await expect(page.getByText(TRUST_CONFIDENCE).first()).toBeVisible();
  });

  test('opens evidence details and resolves source context', async ({ page }) => {
    await ensureTrustContractVisible(page, dataProductId);

    const detailsToggle = page.getByRole('button', { name: /View details|Hide details/ }).first();
    if ((await detailsToggle.count()) > 0) {
      await detailsToggle.click();
    }

    const openSource = page.getByRole('button', { name: 'Open source' }).first();
    if ((await openSource.count()) === 0) {
      await expect(page.getByText(/No linked sources for this answer yet|source linked|sources linked/).first()).toBeVisible();
      return;
    }

    await openSource.click();
    const evidenceDialog = page.getByRole('dialog').last();
    await expect(evidenceDialog).toBeVisible();
    await expect(evidenceDialog.getByRole('heading').first()).toBeVisible();
    await expect(evidenceDialog.locator('pre').first()).toBeVisible();
  });

  test('shows long-running processing details without orchestration jargon leakage', async ({ page }) => {
    await page.goto(`/data-products/${dataProductId}`);

    const input = page.locator('textarea[placeholder="Reply to ekaiX..."]').first();
    await expect(input).toBeVisible({ timeout: 30_000 });
    await expect(input).toBeEnabled({ timeout: 60_000 });
    await input.fill('Profile current context and summarize what you are doing while processing.');
    await input.press('Enter');

    const detailsButton = page.getByRole('button', { name: /Show details|Hide details/ }).first();
    if ((await detailsButton.count()) > 0) {
      await expect(detailsButton).toBeVisible({ timeout: 60_000 });
      await detailsButton.click();
    }
    await expect(page.getByText(/Execution status from orchestration events/i)).toHaveCount(0);
  });

  test('renders usable layout for current viewport', async ({ page, isMobile }) => {
    await page.goto(`/data-products/${dataProductId}`);

    await expect(page.locator('textarea[placeholder="Reply to ekaiX..."]').first()).toBeVisible({
      timeout: 30_000,
    });

    const overflow = await page.evaluate(() => {
      const width = window.innerWidth;
      const rootWidth = document.documentElement.scrollWidth;
      return rootWidth - width;
    });
    const allowance = isMobile ? 24 : 8;
    expect(overflow).toBeLessThanOrEqual(allowance);
  });
});
