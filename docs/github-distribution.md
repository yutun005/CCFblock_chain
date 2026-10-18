# GitHub 主仓库与发布材料

## 主仓库

主仓库包含独立复现所需的源码、测试、示例输入、精确依赖锁、安装/启动脚本、统一文档和紧凑验收摘要。不要通过浏览器拖入整个已安装目录；请用 GitHub Desktop 或 Git 提交，`.gitignore` 会排除安装环境和生成文件。

查看将提交的文件（不修改 Git 暂存区）：

```shell
python scripts/repository_files.py
git status --short
```

本地 `.cache`、`apps/web/node_modules`、`contracts/lib` 仍供运行使用，但不会上传。`setup` 会从锁文件恢复它们；合约依赖固定提交与下载归档 SHA-256，保留许可证。Git 自身的 `.git` 也不会作为项目文件上传。

`.env.example` 是无密钥的配置模板。真实 `.env`、运行 token、私钥、数据库、链数据及 `deployments` 一律不提交。每位使用者首次启动时生成自己的本地账户。

## 独立证据材料

本次整理将完整材料保存在仓库旁的 `CCFblock_chain-release-materials-20261018` 目录，并核对 `ARCHIVE-MANIFEST.json` 中全部移出文件的 SHA-256。该目录不属于 Git 仓库，目前没有上传 GitHub Release。

保留内容包括原 `experiments/raw`、`experiments/results`、历史实验、生成数据、`docs/modules`、报告副本、四个 `provenance/*.tar` 和整理前竞赛包。主仓库中报告的原始证据路径表示它们在独立证据材料内的相对路径；`docs/verification` 保留本次已完成验收的紧凑摘要。

GitHub Actions 在每次验证时重新生成实验结果并上传 artifact，不把数百个日志提交进版本历史。需要公开历史完整材料时，可将独立证据 ZIP 作为 Release 附件上传。整理脚本已保留本地材料，不会自动创建 Release。

## 源码包与竞赛包

```shell
python manage.py package
python scripts/verify_submission.py
```

生成 `submission/AgentFlow-Pay-unified.zip` 和同名 `.sha256`。默认仅包含与 Git 主仓库一致的源码/文档；每份文件只存一次，完整 SHA-256 清单随包提供。它是源码交付包，不自动代表满足竞赛规定的全部报告格式。

需要把新运行生成的证据一同打包：

```shell
python scripts/package_submission.py --include-evidence
```

可用 `--evidence-dir <独立材料目录>` 指定已归档材料位置；该目录下须有 `experiments/raw` 和 `experiments/results`，输出中保留实际来源范围。比赛要求的 PDF、匿名规则及离线依赖包应按赛事规定单独准备，避免上传到日常源码分支。

完整历史报告生成器 `scripts/write_unified_report.py` 需要四分支 tar 和完整实验记录。新克隆者的正常安装、构建、演示和测试均不需要这些原件；历史完整材料不混入本次新生成的验证结果。
