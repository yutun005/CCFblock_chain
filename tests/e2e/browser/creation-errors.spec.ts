import { expect, test } from '@playwright/test';

test('historical task polling errors do not contaminate the new project form', async ({ page }) => {
  const jobId = '0x' + '11'.repeat(32);
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/config') return route.fulfill({ json: {
      scenarios: [{ id: 'cross_org_analysis', title: '跨组织销售分析', description: '输入资料' }],
      chainId: '31337', tokenSymbol: 'TEST',
    } });
    if (path === '/api/jobs' && request.method() === 'GET') return route.fulfill({ json: [
      { jobId, status: 'INTERRUPTED' },
    ] });
    if (path.startsWith('/api/workspace/')) return route.fulfill({ json: path.endsWith('/planner') ? {configured:false,message:'未配置模型'} : [] });
    if (path === '/api/agents') return route.fulfill({ json: [] });
    if (path === '/api/plans/preview') return route.fulfill({ json: {
      planHash: '0x' + '22'.repeat(32),
      plan: { tasks: [{ key: 'clean', title: '数据清洗', budget_cap: '90' }] },
      inputs: { files: [{ rows: 3 }] },
    } });
    if (path === '/api/jobs' && request.method() === 'POST') return route.fulfill({ status: 409,
      json: { detail: '托管交易被链拒绝，请重新创建任务。' } });
    return route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' });
  });
  await page.goto('/?ui=current-build');
  await page.getByLabel('访问令牌').fill('test-token');
  await page.getByRole('button', { name: /连接工作区/ }).click();
  await page.locator('.task-list-main').first().click();
  await expect(page.getByRole('alert')).toContainText('任务数据暂时不可用');
  expect(new URL(page.url()).searchParams.get('ui')).toBe('current-build');
  expect(new URL(page.url()).searchParams.get('job')).toBe(jobId);
  await page.getByRole('button', { name: '项目与成果',exact:true }).click();
  await page.getByRole('button', { name: '新建任务',exact:true }).click();
  await expect(page.getByRole('heading', { name: '你希望完成什么？' })).toBeVisible();
  await expect(page.getByRole('region', { name: '项目与成果规划工作区' }).getByText(/Internal Server Error/)).toHaveCount(0);
});

for (const failingPath of ['/api/agents', '/api/jobs']) {
  test(`login remains usable and recovers when ${failingPath} is unavailable`, async ({ page }) => {
    let unavailable = true;
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === failingPath && unavailable) return route.fulfill({
        status: 500, contentType: 'text/plain', body: 'Internal Server Error',
      });
      if (path === '/api/config') return route.fulfill({ json: {
        scenarios: [{ id: 'cross_org_analysis', title: '销售分析' }], tokenSymbol: 'TEST',
      } });
      if (path.endsWith('/planner')) return route.fulfill({ json: { configured: false } });
      return route.fulfill({ json: [] });
    });
    await page.goto('/');
    await page.getByLabel('访问令牌').fill('test-token');
    await page.getByRole('button', { name: /连接工作区/ }).click();
    await expect(page.getByRole('heading', { name: '项目与成果',exact:true })).toBeVisible();
    await expect(page.getByRole('alert')).toContainText(
      failingPath === '/api/agents' ? '服务列表暂时不可用' : '任务列表暂时不可用',
    );
    unavailable = false;
    await page.getByRole('button', { name: '重试连接' }).click();
    await expect(page.getByRole('alert')).toHaveCount(0);
  });
}

test('invalid tokens still cannot enter the workspace', async ({ page }) => {
  await page.route('**/api/**', (route) => route.fulfill({ status: 401, json: { detail: 'Unauthorized' } }));
  await page.goto('/');
  await page.getByLabel('访问令牌').fill('invalid-token');
  await page.getByRole('button', { name: /连接工作区/ }).click();
  await expect(page.getByRole('alert')).toContainText('访问令牌无效');
  await expect(page.getByRole('heading', { name: '连接协作控制台' })).toBeVisible();
  await expect(page.getByRole('region', { name: '项目与成果规划工作区' })).toHaveCount(0);
});
