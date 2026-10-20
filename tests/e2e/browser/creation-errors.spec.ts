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
  await page.goto('/');
  await page.getByLabel('访问令牌').fill('test-token');
  await page.getByRole('button', { name: /连接工作区/ }).click();
  await page.getByRole('button', { name: '协作任务', exact: true }).click();
  await expect(page.getByRole('heading', { name: '协作任务', exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Internal Server Error');
  await page.getByRole('button', { name: /创建协作任务/ }).click();
  await expect(page.getByRole('heading', { name: '创建一个新任务' })).toBeVisible();
  await expect(page.getByRole('region', { name: '项目与成果工作区' }).getByText(/Internal Server Error/)).toHaveCount(0);
});
