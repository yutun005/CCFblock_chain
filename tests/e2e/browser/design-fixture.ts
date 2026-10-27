import { expect } from '@playwright/test';
export async function setup(page: any, configured = true, taskCount = 2, wide = false, graphOverride: any = null) {
  let session: any = null;
  let saved: any = null;
  let failEdit = false;
  let calls = 0;
  const brief = { objective: '策划校园读书节', requirements: [
    { id: 'R1', text: '交付校园读书节方案，预算一万元', kind: 'goal' },
  ], deliverables: ['活动执行方案'], constraints: ['一万元'], knownInputs: [], assumptions: [] };
  const outline = [{ id: 'design', title: '设计活动与执行安排', description: '明确参与对象、预算和日程', deliverables: ['活动执行方案'] }];
  const graph = { title: '校园读书节方案', summary: '按预算设计读书活动', tasks: [
    { key: 'research', title: '了解学生需求', description: '明确偏好与参与范围', stageId: 'design', dependencies: [], inputs: ['活动目标'], outputs: ['需求清单'], acceptance: ['说明调查对象'], requirementIds: ['R1'] },
    { key: 'plan', title: '编制执行方案', description: '明确预算和日程', stageId: 'design', dependencies: ['research'], inputs: ['需求清单'], outputs: ['活动执行方案'], acceptance: ['费用合计不超过一万元'], requirementIds: ['R1'] },
  ], deliverables: [{ name: '活动执行方案', taskKey: 'plan' }], limitations: [] };
  if (taskCount > 2) {
    graph.tasks = Array.from({length: taskCount}, (_, i) => ({ ...graph.tasks[0], key: `task${i}`, title: `工作阶段 ${i + 1}`,
      dependencies: wide || !i ? [] : [`task${i-1}`], outputs: [`阶段 ${i+1} 成果`] }));
    graph.deliverables = [{name: `阶段 ${taskCount} 成果`, taskKey: `task${taskCount-1}`}];
  }
  if (graphOverride) Object.assign(graph, graphOverride);
  const template = { id: 'event', revision: 1, name: '读书节模板', description: '校园活动的筹备与交付', category: '通用规划', builtin: true, brief, outline, graph };
  const fresh = () => ({ id: 'design1', revision: 1, title: '校园读书节方案', phase: 'dag', planningOnly: true, goal: '办读书节', messages: [], brief, outline, graph: structuredClone(graph), questions: [], coverage: [], versions: [], updatedAt: 1 });
  const json = (route: any, value: any) => route.fulfill({ json: value });
  await page.route('**/api/**', async (route: any) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.method() === 'POST' ? request.postDataJSON() : {};
    if (path === '/api/config') return json(route, { scenarios: [], tokenSymbol: 'USDC', chainId: 31337 });
    if (path.endsWith('/planner')) return json(route, { configured });
    if (path === '/api/workspace/design-templates') return json(route, saved ? [template, saved] : [template]);
    if (path.includes('/design-templates/') && path.endsWith('/apply')) {
      expect(body.revision).toBe(1);
      session = fresh();
      return json(route, session);
    }
    if (path === '/api/workspace/designs' && request.method() === 'GET') return json(route, session ? [session] : []);
    if (path === '/api/workspace/designs/design1' && request.method() === 'GET') return json(route, session);
    if (path.endsWith('/review')) return route.fulfill({status: 400, json:{detail:'AI 查漏暂未完成，可继续编辑或直接确认当前版本。'}});
    if (path === '/api/workspace/designs' || path.endsWith('/messages')) {
      calls++;
      if (calls > 1) expect(body.expectedRevision).toBe(session.revision);
      session ||= fresh();
      if (body.message === '生成DAG呀' || body.message === '我想办个活动，其他还不清楚') {
        session = { ...session, phase: 'dag', graph: structuredClone(graph), revision: session.revision + 1,
          messages: [...session.messages, { id: 'ugenerate', role: 'user', content: body.message },
            { id: 'agenerate', role: 'assistant', content: '已生成任务图，可以修改。', questions:['日期以后再补也可以'] }], questions: ['日期以后再补也可以'], warnings:['验收条件可以后续完善'] };
        return json(route, session);
      }
      session = { ...session, revision: calls, phase: calls === 1 ? 'clarifying' : 'outline', graph: null,
        outline: calls === 1 ? [] : [{ ...outline[0], title: calls >= 3 ? '先做小规模试点' : outline[0].title }],
        messages: [...session.messages, { id: `u${calls}`, role: 'user', content: body.message },
          { id: `a${calls}`, role: 'assistant', content: calls === 1 ? '请补充参与对象和预算。' : '先核对这份主干，满意后展开。', questions: calls === 1 ? ['主要面向谁，预算多少？'] : [] }],
        questions: calls === 1 ? ['主要面向谁，预算多少？'] : [] };
      return json(route, session);
    }
    if (path.endsWith('/expand')) {
      expect(session.phase).toBe('outline');
      expect(body.expectedRevision).toBe(session.revision);
      session = { ...session, phase: 'dag', graph: structuredClone(graph), revision: session.revision + 1 };
      return json(route, session);
    }
    if (path.endsWith('/edit')) {
      expect(body.expectedRevision).toBe(session.revision);
      if (failEdit) { failEdit = false; return route.fulfill({ status: 422, json: { detail: '任务依赖存在循环，请调整先后关系' } }); }
      session = { ...session, graph: body.graph, phase: 'dag', revision: session.revision + 1 };
      return json(route, session);
    }
    if (path.endsWith('/confirm')) {
      expect(body.expectedRevision).toBe(session.revision);
      session = { ...session, phase: 'confirmed', revision: session.revision + 1, versions: [{ id: 'v1', number: 1, graphHash: 'hash', createdAt: 1 }] };
      return json(route, session);
    }
    if (path.endsWith('/versions/v1')) return json(route, { ...session, number: 1 });
    if (path.endsWith('/template')) {
      saved = { ...template, id: 'personal', name: body.name, category: '我的模板', graph: session.graph };
      return json(route, saved);
    }
    return json(route, []);
  });
  await page.setViewportSize({ width: 1600, height: 1050 });
  await page.goto('/');
  await page.getByLabel('访问令牌').fill('test-token');
  await page.getByRole('button', { name: /连接工作区/ }).click();
  await page.getByRole('button', { name: '新建任务', exact: true }).click();
  return { failNextEdit: () => { failEdit = true; }, calls: () => calls };
}
