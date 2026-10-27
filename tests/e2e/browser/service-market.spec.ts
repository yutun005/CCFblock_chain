import { expect, test } from '@playwright/test';

async function market(page: any) {
  const service = {id:'attraction_search',name:'景点检索',description:'按出游需求筛选景点',keywords:['景点'],scenario:'travel_planning',inputType:'需求',outputType:'景点清单',requires:[]};
  let corpus: any = {revision:1,services:[service],merchants:[
    {id:'clean-a',name:'景点服务商甲',offers:[{serviceId:service.id,price:'18',seconds:20,history:[]}]},
    {id:'offline-a',name:'待接入服务商',offers:[{serviceId:service.id,price:'20',seconds:30,history:[]}]},
  ],removedMerchants:[],removedServices:[]};
  const writes: any[] = [];
  let failSave = false;
  await page.route('**/api/**', async (route: any) => {
    const req=route.request(), path=new URL(req.url()).pathname;
    const body=req.method()==='POST'?req.postDataJSON():null;
    if(path==='/api/config')return route.fulfill({json:{scenarios:[],tokenSymbol:'USDC'}});
    if(path==='/api/agents')return route.fulfill({json:[{agentId:'clean-a',status:'ONLINE',skills:[service.id],wallet:'0x1111222233334444555566667777888899990000',endpoint:'http://127.0.0.1:6766',bindingVersion:3}]});
    if(path.endsWith('/planner'))return route.fulfill({json:{configured:true}});
    if(path.endsWith('/service-corpus')){
      if(body){
        writes.push(body);
        if(failSave){failSave=false;corpus.revision++;return route.fulfill({status:409,json:{detail:'资料版本已变化，请重试'}});}
        expect(body.revision).toBe(corpus.revision);
        for(const kind of ['services','merchants'])for(const row of body[kind]||[])corpus[kind]=corpus[kind].map((old: any)=>old.id===row.id?row:old);
        corpus.revision++;
      }
      return route.fulfill({json:corpus});
    }
    if(path.endsWith('/service-graph'))return route.fulfill({json:{revision:corpus.revision,serviceCount:1,merchantCount:2,edges:[],nodes:corpus.services.filter((s: any)=>!corpus.removedServices.includes(s.id)).map((s: any)=>({...s,executable:true,adapterReady:true,acceptance:'景点符合用户出游条件',providers:corpus.merchants.filter((m: any)=>!corpus.removedMerchants.includes(m.id)).map((m: any)=>({...m,online:m.id==='clean-a',price:m.offers[0].price,seconds:20,history:{window:10,settled:9,source:'demo'}}))}))}});
    if(path.endsWith('/delete')){
      expect(body.revision).toBe(corpus.revision); writes.push({path,...body});
      const parts=path.split('/');corpus[parts.at(-3)==='merchant'?'removedMerchants':'removedServices'].push(parts.at(-2));corpus.revision++;
      return route.fulfill({json:{ok:true}});
    }
    return route.fulfill({json:[]});
  });
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('/');await page.getByLabel('访问令牌').fill('test');await page.getByRole('button',{name:/连接工作区/}).click();
  await page.getByRole('button',{name:'服务市场',exact:true}).click();
  return {writes,failNextSave:()=>{failSave=true;}};
}

test('service instances stay visible; detail dialog edits and deletes the selected merchant',async({page},testInfo)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const mock=await market(page);
  const online=page.getByRole('region',{name:'在线服务实例',exact:true});
  const trigger=online.getByRole('button',{name:'查看详情：clean-a',exact:true});
  await expect(trigger).toBeVisible();
  await expect(page.locator('details,summary,[aria-expanded]')).toHaveCount(0);
  await expect(page.getByText('管理全部商家和未关联的服务定义')).toHaveCount(0);
  await trigger.click();
  let dialog=page.getByRole('dialog');
  await expect(dialog).toContainText('18 USDC');await expect(dialog).toContainText('http://127.0.0.1:6766');
  await page.screenshot({path:testInfo.outputPath('service-details-desktop.png')});
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await expect(trigger).toBeFocused();
  await trigger.click();await dialog.getByRole('button',{name:'修改商家和服务',exact:true}).click();
  await dialog.getByLabel('显示名称').fill('更新后的景点服务');
  await dialog.getByLabel('预计费用（USDC）').fill('21');
  mock.failNextSave();await dialog.getByRole('button',{name:'保存资料',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('资料版本已变化');
  await expect(dialog.getByLabel('显示名称')).toHaveValue('更新后的景点服务');
  await dialog.getByRole('button',{name:'保存资料',exact:true}).click();
  await expect(dialog.getByRole('heading',{name:'更新后的景点服务',exact:true})).toBeVisible();
  await expect(dialog).toContainText('21 USDC');
  await dialog.getByRole('button',{name:'删除商家',exact:true}).click();
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  expect(mock.writes.filter(x=>x.path)).toHaveLength(0);
  await dialog.getByRole('button',{name:'删除商家',exact:true}).click();
  await dialog.getByRole('button',{name:'确认删除',exact:true}).click();
  await expect(dialog).toHaveCount(0);await expect(trigger).toHaveCount(0);
  expect(mock.writes.filter(x=>x.path)).toHaveLength(1);
  await expect(page.getByRole('region',{name:'未在线的商家'}).getByText('待接入服务商')).toBeVisible();
  expect(errors).toEqual([]);
});

test('service definitions edit in the same dialog and fit a mobile viewport',async({page},testInfo)=>{
  const mock=await market(page);
  const trigger=page.getByRole('region',{name:'服务能力'}).getByRole('button',{name:'查看详情：景点检索',exact:true});
  await trigger.click();const dialog=page.getByRole('dialog');
  await dialog.getByRole('button',{name:'修改服务资料',exact:true}).click();
  await expect(dialog.getByLabel('输入类型',{exact:true})).toBeVisible();
  await dialog.getByLabel('业务说明').fill('按照出游日期与兴趣提供候选景点');
  await dialog.getByRole('button',{name:'保存资料',exact:true}).click();
  await expect(dialog).toContainText('按照出游日期与兴趣提供候选景点');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  expect(await dialog.evaluate((d:any)=>d.scrollWidth<=d.clientWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('service-details-mobile.png')});
  await dialog.getByRole('button',{name:'删除服务节点',exact:true}).click();
  await dialog.getByRole('button',{name:'确认删除',exact:true}).click();
  await expect(dialog).toHaveCount(0);await expect(trigger).toHaveCount(0);
  expect(mock.writes.at(-1).path).toContain('/service/attraction_search/delete');
});
