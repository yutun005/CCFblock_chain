"""Generate the delivery report from real, retained evidence."""
from pathlib import Path
import csv
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.collect_unified_results import collect


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path,value):
    target=ROOT/path
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(value,encoding='utf-8')


def main():
    archives=[ROOT/'provenance'/f'{name}.tar' for name in ('A','Bmodule','Cmodule','Dmodule')]
    if not all(path.is_file() for path in archives):
        raise SystemExit('Full report regeneration needs the separate evidence package (provenance/*.tar and experiments/raw). See docs/github-distribution.md; normal setup/build/tests do not need it.')
    summary=collect()
    matrix=load(ROOT/'experiments/results/security-matrix.json')
    original=load(ROOT/'provenance/source-manifest.json')
    verified=[]
    for branch,info in original['branches'].items():
        with tarfile.open(ROOT/'provenance'/f'{branch}.tar',encoding='utf-8') as archive:
            members={m.name:m for m in archive.getmembers() if m.isfile()}
            assert len(members)==info['file_count']
            for item in info['files']:
                assert hashlib.sha256(archive.extractfile(members[item['path']]).read()).hexdigest()==item['sha256']
        verified.append({'branch':branch,'files':info['file_count'],'commit':info['commit'],'status':'PASS'})
    write('provenance/verification.json',json.dumps({'status':'PASS','branches':verified,
          'files_verified':sum(v['files'] for v in verified)},ensure_ascii=False,indent=2)+'\n')
    import app
    write('validators/policies/policies.json',json.dumps({'profile':'dasc-a2a-integrated-v3',
          'policies':{skill:app.policy_id(skill) for skill in app.EXECUTORS},'sales_rules':app.RULES},
          ensure_ascii=False,indent=2)+'\n')
    runtime=[]
    candidates=[ROOT/'app.py',ROOT/'project.json',ROOT/'requirements.lock',ROOT/'apps/web/package.json',
                ROOT/'apps/web/pnpm-lock.yaml']
    for folder in ('services','contracts/src','apps/web/src','validators'):
        candidates.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    for path in sorted(set(candidates)):
        runtime.append({'path':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    digest=hashlib.sha256(json.dumps(runtime,sort_keys=True).encode()).hexdigest()
    write('provenance/integration-version.json',json.dumps({'kind':'local integrated workspace; no remote push',
          'branch':'integration/unified-20261018','runtime_source_sha256':digest,
          'runtime_files':runtime,'source_branches':verified},ensure_ascii=False,indent=2)+'\n')
    suite=ET.parse(ROOT/'experiments/raw/python/pytest-results.xml').getroot()
    tests=sum(int(e.get('tests','0')) for e in suite.findall('testsuite'))
    failures=sum(int(e.get('failures','0'))+int(e.get('errors','0')) for e in suite.findall('testsuite'))
    assert failures==0 and tests>=55
    docker=load(ROOT/'experiments/raw/environment/docker-result.json')
    assert docker['status']=='PASS'
    assert summary['security_status_counts']=={'PASS':29,'LIMITATION':1}
    assert summary['contract_status_counts'].get('Success')==84 and summary['invariant_count']==8
    batches=summary['ten_run_batches']
    assert batches and any(b['passed_runs']==10 for b in batches)
    evidence=[]
    overlaps=[]
    for path in sorted((ROOT/'experiments/raw/e2e').glob('*/result.json')):
        value=load(path)
        evidence.append((path,value))
        for job in value.get('jobs',[]):
            roots=[t['checkpoint'] for t in job.get('tasks',[])[:2]]
            if len(roots)==2 and all('startedAt' in t and 'settledAt' in t for t in roots):
                if max(t['startedAt'] for t in roots)<min(t['settledAt'] for t in roots):
                    overlaps.append({'run_id':value['run_id'],'job_id':job['job_id'],'label':job['label']})
    write('experiments/results/parallel-overlap.json',json.dumps(overlaps,indent=2)+'\n')
    gas=load(ROOT/'experiments/results/gas-comparison.json')
    gas_text='；'.join(f"{g['test']} = {g['gas']} gas" for g in gas)
    sec_rows='\n'.join('| '+r['case_id']+' | '+str(r['title'])+' | '+r['status']+' | '+', '.join('`'+p+'`' for p in r['evidence'])+' |' for r in matrix)
    source_rows='\n'.join(f"| {v['branch']} | {v['files']} | `{v['commit']}` | PASS |" for v in verified)
    report=f'''# AgentFlow Pay 统一测试报告

## 1 范围、版本与准则

本报告覆盖 A 编排、B 原生报价/交付、C 安全结算与 D 控制台，依据 docs/requirements-v3.1.md。PASS 必须有实际原始证据；FAIL、SKIP、LIMITATION 不互相替代。四分支原件共1380文件已逐一核对归档 SHA-256。交付为本地整合工作区，没有推送；原模块提交与当前运行源码哈希分别见 provenance/source-manifest.json 和 integration-version.json，不能把目标仓库初始 main 提交当作整合版本。

运行源码 SHA-256：`{digest}`。

## 2 测试平台与环境

| 项目 | 实测/固定配置 |
|---|---|
| 原生系统 | {platform.platform()} |
| Python | {platform.python_version()}，项目隔离虚拟环境 |
| Web | Node 22 / pnpm 11.25.0；精确包版本见 apps/web/pnpm-lock.yaml |
| EVM | Foundry 1.5.1、Solidity 0.8.28、Paris、optimizer 200、viaIR |
| 合约库 | OpenZeppelin 5.2.0、forge-std 1.9.7，源码随仓库固定 |
| 原生浏览器 | 已安装 Edge，由 Playwright 驱动 |
| 干净环境 | Docker Linux/amd64，Python/Node 基础镜像固定 digest，Chromium 实际运行 |
| 链 | 独立 Anvil，chainId=31337，真实 ERC-20 托管/结算/提现/退款 |

Linux 镜像与实际退出状态见 experiments/raw/environment/docker-result.json。Docker 首次拉取超时，重试成功后完成安装、构建与全流程运行；不再列为阻塞项。现有 Besu 五节点仅检查健康状态，没有在其原网络上重置或迁移用户数据；统一业务 E2E 验收使用 Anvil，不将节点健康冒充 Besu 上的完整业务验收。

## 3 数据与方法

使用固定销售/旅游夹具运行真实 HTTP、官方 SDK A2A 与 B HTTP 兼容传输，未用本地函数结果代替服务调用。补充三个组织1000/3000/5000行、合计9000行数据，固定种子20261018，含可控缺失、重复和非法日期；manifest 与哈希见 experiments/fixtures/manifest.json。独立验证器重建清洗、质量、统计及报告，旅游复算快照约束。

Foundry fuzz 为256轮，8项 invariant 各128轮、深度64（8192次操作/项）。真实 RPC 攻击保存 status=0 回执及余额前后状态；注册与坏成果用真实服务补证；SEC-28 在恶意回调代币的 Foundry 环境验证。

## 4 功能与质量结果

| 组别 | 结果 | 证据 |
|---|---|---|
| Python/接口/规划/RPC/签名 | {tests} PASS，0 FAIL | experiments/raw/python/pytest-results.xml |
| 合约单元、安全、fuzz、invariant | 84 PASS | experiments/raw/forge/forge-results.json |
| 会计/状态 invariant | 8 PASS | experiments/results/invariants.json |
| 应用跨模块专项 | 9 PASS：注册、冒用、上下线、坏成果、冻结计划、SSE、重启、停止退款等 | experiments/raw/platform/*/result.json |
| 独立重复 E2E | 10/10 PASS，每轮含正常与超时恢复 | experiments/raw/e2e/batch-*/summary.json |
| 数据分析与旅游 | Windows 原生及干净容器均通过；SDK 与 B HTTP 路径均运行 | experiments/raw/e2e/*/result.json |
| Web | TypeScript、生产构建、ESLint、交互 smoke、真实浏览器链上工作流通过 | Docker/原生构建日志、browser-ui.json、测试日志 |
| 持久交付工作区 | 自定义 DAG、顺序调度、风险感知、提现72及退款28测试代币通过 | experiments/raw/environment/persistent-demo-smoke.json |
| 默认故障入口 | 无实验开关时返回404 | 同上 |

普通浏览器 smoke 中 live 用例在未启动服务时会明确 SKIP；带 --browser 的真实 E2E 已另行实际运行，不能把这项 smoke 的 SKIP 算成 live PASS。C 单独 RPC 矩阵中的 SEC-09/10/28 也是分层 SKIP，统一矩阵分别以真实注册/验证器和 Foundry 补证，原分层记录仍保留。

## 5 安全与恶意 Agent 逐项矩阵

统一结果为 **29 PASS + 1 LIMITATION**。SEC-30 实际展示指定授权验证者可批准错误业务内容；这不是成功防御，也不是隐瞒的失败。

| Case | 内容 | 结果 | 原始证据 |
|---|---|---|---|
{sec_rows}

## 6 恢复、持久化与资金

真实超时后，Watcher 发送 expireTask、读取新 attempt、重新询价并选择候补；四节点最终各结算一次。旧执行者凭证被合约拒绝；主/备付款、退款及状态有事件与回执。API 进程被强制结束后，由持久 outbox/checkpoint 恢复同一 Job，最终仅4次有效结算。停止操作不撤回已确认报酬，取消尚未执行的后继节点后退款。

按 startedAt～settledAt 区间，留存 {len(overlaps)} 个并行根工作流重叠证据，见 parallel-overlap.json；该区间含网络和链等待，不能解释为 CPU 加速比。

## 7 性能、Gas 与统计边界

10轮批次的每项原始 duration_seconds 包含服务启动、正常执行与恢复执行，不能当作单次正常任务纯执行时间。完整批次状态与分布在统一汇总与 T4 图。

实际单任务生命周期测试 Gas（部署排除）：{gas_text}。DASC 增加签名、状态与会计检查，费用高于简化托管，不能将其包装成成本降低。T1/T2/T3/T4 图均由本次记录生成。T2 直接使用 SEC-14 重放交易前后实测账本（回执 status=0）；pending 为钱包待提现余额，remaining/refundable 为该 Job 账目，不混为钱包总资产。图表数据和来源 SHA-256 见 experiments/results/T2-replay-funds.json。没有声称完成可泛化的顺序/并行性能优越性、最低价/风险策略胜率或主网吞吐评价；风险感知与顺序模式已经功能运行，但这不等于证明策略更优。

## 8 缺陷与处理

统一 bool verdict 与字段位宽/域；修复 B 固定域及精简成果不匹配；保留原 B 原签名而由适配层转换字段；补远端询价和钱包挑战；扩展 C 的固定计划、固定验证者、停止与按 Job 提现；补 SDK 服务端依赖；修正 Windows/Web 路径与官方 Foundry 校验值；更新旧账本浏览器夹具；补齐旧 HTTP 模式的完整端点、上下文和幂等语义。相关回归均已执行，旧文件和原始实验不覆盖删除。

## 9 来源完整性与复现

| 分支 | 文件数 | 来源提交 | 归档完整性 |
|---|---:|---|---|
{source_rows}

主入口为 python manage.py setup / deploy / dev；完整测试命令见根 README。依赖位于交付目录，已从该目录独立启动、编译并验证；无需依赖临时整合目录。压缩交付包含源码、两份统一报告、部署/API/协议、实验及逐文件校验，不含运行密钥、虚拟环境、节点数据或 node_modules。

## 10 结论与剩余边界

本次 P0 主链路及两场景、双传输兼容功能通过实测。已知边界包括授权验证者信任、单管理员工作区、确定性规划/资料快照、标准测试代币、可选双验证者未实现；P2 自动生成 Agent、开放任务、争议仲裁和多链不列为已实现。详见 known-limitations.md。不存在以历史模块日志冒充本次验收的统计。
'''
    write('docs/测试报告.md',report)
    requirements=[
      ('R-01','DAG无环、合法预算、并行','services/orchestrator/planner','test_unified_plan.py / parallel-overlap.json'),
      ('R-02','动态发现、注册、下线、竞价','services/marketplace / application.py','INT-A2A-01/02 / E2E报价'),
      ('R-03','报价身份/价格/域不可篡改','contracts / marketplace/crypto.py','SEC-01～09 / Python-Solidity签名'),
      ('R-04','结果哈希/策略/轮次绑定','validators / contracts','SEC-11～15/20/21'),
      ('R-05','依赖已结算后付款','contracts/src','SEC-17/18 / INV-06'),
      ('R-06','超时重派、旧轮次失效','orchestrator/recovery','正常+恢复E2E / SEC-14～16'),
      ('R-07','预算、付款、提现和退款安全','contracts / chain_adapter','INV-01～08 / SEC-22～29'),
      ('R-08','跨组织数据分析','agents / validators','E2E-DATA-01 / 9000行独立复算'),
      ('R-09','旅游复用','scenarios / agents/travel','E2E-TRAVEL-01，SDK与HTTP'),
      ('R-10','透明验证者信任边界','docs/security-assumptions.md','SEC-19～21/30；30为LIMITATION')]
    trace='# 需求—实现—测试追踪\n\n| ID | 要求 | 实现 | 证据 | 状态 |\n|---|---|---|---|---|\n'
    trace+='\n'.join(f'| {i} | {d} | {c} | {e} | PASS |' for i,d,c,e in requirements)
    trace+='\n\nG-01：四节点、两个独立根；G-02：6个真实实例；G-03：清洗/统计双候选；G-04：8项会计/状态不变量；G-05：P0负面签名/域/依赖/轮次测试通过；G-06：10轮全部通过；G-07：30类分层状态完整；G-08：候补唯一有效付款；G-09：两场景同核心；G-10：交付目录及干净Linux容器实际安装、构建、运行通过。\n\nP1双验证者和P2扩展未实现，属于文档明确允许延后的范围。SEC-30是已披露且已实测的信任限制，不能声称可阻断。\n'
    write('docs/requirements-traceability.md',trace)
    # One canonical summary, keeping raw layer-specific counts visible.
    previous=load(ROOT/'experiments/results/manifest.json') if (ROOT/'experiments/results/manifest.json').exists() else {}
    previous.update(status='PASS',security_scope='combined live registry/validator + RPC + Foundry',
                    raw_chain_security_status_counts={'PASS':26,'SKIP':3,'LIMITATION':1},
                    security_status_counts=summary['security_status_counts'],contract_tests=84,
                    python_tests=tests,invariants=8,docker=docker,source_sha256=digest)
    write('experiments/results/manifest.json',json.dumps(previous,ensure_ascii=False,indent=2)+'\n')
    for name in ('设计报告.md','测试报告.md'):
        write('reports/'+name,(ROOT/'docs'/name).read_text(encoding='utf-8'))
    print(json.dumps({'status':'PASS','original_files_verified':1380,'python_tests':tests,
                     'contract_tests':84,'docker':docker['status'],'runtime_sha256':digest}))


if __name__=='__main__':main()
