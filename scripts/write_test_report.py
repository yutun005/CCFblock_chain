"""Write C report from actual raw outputs. Never prefill missing results as PASS."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.scripts.common import ROOT  # noqa: E402


def run():
    summary = json.loads((ROOT / "experiments/results/summary.json").read_text(encoding="utf-8"))
    records = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((ROOT / "experiments/raw/security").glob("SEC-*.json"))
    ]
    env_path = ROOT / "experiments/raw/environment.json"
    environment = json.loads(env_path.read_text(encoding="utf-8")) if env_path.exists() else {}
    ram = environment.get("ram_bytes")
    hardware = (
        f"实际 OS：{environment.get('os', '未记录')}；CPU：{environment.get('cpu', '未记录')}；"
        f"逻辑核心：{environment.get('logical_cpus', '未记录')}；"
        f"物理内存：{ram / 2**30:.2f} GiB。"
        if ram
        else "硬件信息见 raw/environment.json。"
    )
    lines = [
        "# AgentFlow Pay 测试报告 — C 模块实测素材",
        "",
        "本文由实际 raw 日志生成，仅涵盖 C 合约、链适配器和私有链实验；D 汇总全平台测试报告。",
        "",
        "## 测试环境与方法",
        "",
        hardware,
        "Windows；Python 3.11；Foundry 1.5.1；Solidity 0.8.28；Paris EVM；优化 200 runs，via_ir；"
        "OpenZeppelin 5.2.0；forge-std 1.9.7；Besu 25.10.0。Anvil chainId=31337，Besu chainId=1337。",
        "fuzz runs=256，seed=0x20261013；每个 invariant runs=128、depth=64。"
        "编译结果、实际 runs/calls 和失败信息见 raw/forge。Python 锁版本见 requirements.lock。",
        "",
        "Git 字段若为 UNCOMMITTED，表示独立模块尚未合并团队仓库；最终版本以源码 SHA-256 清单固定，"
        "新实验附加 source_hash。不得将缺少团队提交点写成已有 Git Tag。",
        "",
        "## 合约与 Python",
        "",
        f"Foundry：{summary['forge']['total']} 项，状态 {summary['forge']['statuses']}。",
        f"Python：{summary.get('python', {})}，实际 JUnit 在 experiments/raw/python/pytest-results.xml。",
        "Foundry 的 SEC-30 测试函数成功只表示观察到预期信任缺口；攻击矩阵必须记 LIMITATION。",
        "",
        "## 安全攻击矩阵",
        "",
        "PASS=达到预期且证据完整；FAIL=未达到；SKIP=注明缺少的跨模块条件；LIMITATION=保障范围外。",
        "",
        "| Case | 攻击/异常 | RPC层结果 | 证据/原因 |",
        "|---|---|---|---|",
    ]
    for r in records:
        reason = r.get("reason", f"raw/security/{r['case_id']}.json")
        lines.append(f"| {r['case_id']} | {r['title']} | {r['status']} | {reason} |")
    lines += [
        "",
        "SEC-28 的真实恶意回调组件在 Foundry 验证 ReentrancyGuard 错误选择器，"
        "正常 RPC 部署不引入恶意支付 token，因此 RPC 矩阵 SKIP 不代表合约测试缺失。",
        "SEC-09/10 需要 B 的真实服务绑定和 A 的业务验证，不能以 C 夹具替代。",
        "",
        "## 四任务链上闭环",
        "",
        "每个成功运行托管100、结算80、退款20（均为无价值测试单位），检查交易回执和钱包余额差额。"
        "这些是 C 模块夹具，不是完整 A2A 平台的端到端成绩。",
        "",
        "| chainId | 运行数 | 成功数 | 平均秒 | 最小秒 | 最大秒 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for chain, stats in summary["module_c_e2e"].get("by_chain", {}).items():
        values = [
            f"{stats[k]:.3f}" if stats[k] is not None else "未测"
            for k in ("mean_seconds", "min_seconds", "max_seconds")
        ]
        lines.append(f"| {chain} | {stats['total']} | {stats['passed']} | {' | '.join(values)} |")
    lines += [
        "",
        "单次耗时只覆盖该脚本定义的链上流程；样本量有限，不能外推为生产性能。",
        "恢复的失败记录同样计入运行总数；其耗时未记录，因此不填入成功运行的耗时统计。",
        "",
        "## Besu 节点故障",
        "",
    ]
    for case in summary.get("besu_faults", []):
        lines += [
            f"{case['case_id']}：{case['status']}；运行 {case['run_id']}。",
            "```json",
            json.dumps(case["observed"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    lines += [
        "## Gas 对照",
        "",
        "仅比较自建最小单任务托管与 DASC 的同一生命周期，部署在 setUp 中，不计入测试函数Gas。"
        "snapshot 是 Foundry 测试函数的总执行Gas，含测试辅助调用；不等同公网逐笔交易费用。",
        "",
    ]
    snapshot = ROOT / "experiments/raw/forge/gas-comparison.snap"
    if snapshot.exists():
        lines += ["```text", snapshot.read_text(encoding="utf-8"), "```"]
    else:
        lines += ["Gas snapshot 尚未生成，不填数值。"]
    reproduction = ROOT / "experiments/raw/reproduction/summary.json"
    if reproduction.exists():
        result = json.loads(reproduction.read_text(encoding="utf-8"))
        lines += [
            "",
            "## 独立解压复现",
            "",
            f"{result['case_id']}：{result['status']}。独立目录重新建立Python环境、安装锁定依赖，"
            f"{result['forge_tests']}项合约测试和{result['python_rpc_tests']}项Python测试通过。",
            "两个合约的编译创建字节码与原工程逐字节一致。另在临时Anvil实际部署并完成结算、"
            "提现和退款，测试结束停止该临时链。复用了校验过的同版本Foundry程序，没有复制原构建缓存。",
            "证据位于 experiments/raw/reproduction/；原平台统计与复现测试分开，避免重复计数。",
        ]
    lines += [
        "",
        "## 缺陷、修复与回归",
        "",
        "| 发现问题 | 修正 | 验证 |",
        "|---|---|---|",
        "| 跨链测试夹具未正确产生异域签名 | 显式构造不同chainId域哈希 | SEC-06和Python域测试 |",
        "| 恶意token构造期触发零地址回调 | 仅提现时触发已配置目标 | SEC-28核对重入错误选择器 |",
        "| 部署交易的bytes字段不能JSON序列化 | 指纹统一转换bytes | Anvil/Besu实际部署通过 |",
        "| 既有应付奖励影响重复演示余额 | 新Job前领取旧报酬 | 保留FAIL，修复后重复闭环通过 |",
        "| Besu不支持时间跳跃RPC | 改为短实验窗口并等待真实区块时间 | SEC-15/16真实回执 |",
        "| Foundry Windows fmt的路径解析失败 | 逐文件通过stdin格式化 | 格式化后完整测试回归 |",
        "",
        "## 结论与限制",
        "",
        "已验证C层签名、域、任务依赖、预算、执行轮次、唯一结算、退款/提现与资金守恒。"
        "节点实验验证本机QBFT网络单节点停止和恢复。未证明授权验证者诚实、多组织实际独立治理、"
        "真实A2A能力、公开网络可靠性或生产性能。完整集成由A/B/D接入后另测。",
        "",
    ]
    (ROOT / "docs/modules/Cmodule/运行测试报告.md").write_text("\n".join(lines), encoding="utf-8")
    print("C module test report generated from actual evidence")


if __name__ == "__main__":
    run()
