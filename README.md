# GameReach v0.11.0 — 多维兴趣画像与需求聚焦研究

[项目展示](https://usbfl.github.io/GameReach/) · [实验与验收报告](https://usbfl.github.io/GameReach/report.html) · [GitHub 发布说明](docs/GITHUB_DEPLOYMENT.md)

基于 v0.10.2 Budget Savings 增量升级。保留 FastAPI / SQLite / React / TypeScript、64 个体验标签、Game DNA、持久化 Agent、预算、缓存、人工接管与历史报告。

## 启动与升级

需要 Python 3.11+。包内有构建后的前端及字体，直接运行不需要 Node.js；首次安装 Python 依赖需要网络。新安装默认 `DATA_MODE=fixtures`、`LLM_MODE=mock`，游戏与评论均为合成演示数据。

Windows PowerShell（项目根目录）：

```powershell
$env:DATA_MODE = 'fixtures'
$env:LLM_MODE = 'mock'
.\scripts\run_demo.ps1
```

Linux / macOS（项目根目录）：

```bash
DATA_MODE=fixtures LLM_MODE=mock bash scripts/run_demo.sh
```

访问 <http://127.0.0.1:8000>；保留终端，Ctrl+C 停止。也可打开 `START_HERE.html` 阅读导航。已有用户先按 [升级与回退说明](docs/UPGRADE_011.md) 备份并在新目录升级，保留原 `.env` 和数据库；不要覆盖真实密钥。

详细说明：[Windows](docs/RUN_WINDOWS_011.md) · [Linux/macOS](docs/RUN_UNIX_011.md)。

## 使用

1. 在“我的游戏资料”导入游戏库或清单。可导入 `data/fixtures/v011_player_library.json` 体验两种主题及交叉成员；该文件中的 A–F 都是虚构游戏。
2. 整理兴趣：默认每卡 3–7 款、最多 6 卡。同一游戏可以参加不同主题，单卡去重；可以查看归入依据、编辑标题/概括/成员、确认、删除。资料不足保留待归类。
3. 输入当前需求，可附加最多 6 张已确认兴趣卡片。点击“解析并编辑需求”修改标签、方向、重要度、软硬条件或自定义表达，再确认本次条件；也可直接开始自动解析。
4. 选择策略、商店、平台及可选语言/预算，开始研究。最终人工条件优先于推断；兴趣卡片不能静默成为硬约束。
5. 阅读符合关注点的理由、明确取舍与原文证据。软偏好未知可以形成部分匹配；硬约束未知保留待确认，失败排除。展开“更多证据与不确定项”查看诊断。
6. 结果页调整重要度在已有证据上本地重排。新增关注点、改变硬约束或候选范围，应发起新研究；有效缓存仍可复用。

## 主要变化

- 多对多兴趣分组复用目录标签、个人分类、收藏/反馈及已有可靠 DNA；不为库存逐款抓取评论。明确不喜欢/排除阻止正向入组，时长只增加最多 0.25 的关注权重，不等同喜欢。
- 研究前可编辑结构化需求；词表外表达保留为任务级自定义需求，不自动写入全局词表，也不伪造商店标签。未映射的硬条件无法自动证明满足。
- 关注点排序 `focus-rank-2`：未知不当负面，取消 coverage 乘法，已核验权重饱和奖励并惩罚已证实冲突。coverage 仅作诊断。仅有次要证据不能获得接近满分的分数。
- 按硬条件、主要关注点及有价值的反证缺口补查；保留完整评论、独立语义核验、作者/正文去重、引用定位、来源 TTL 与预算停止。
- 旧确认卡片和历史报告不重写；无 SQL 表/列迁移。新字段为可选 JSON 字段，提供只写新路径的数据库备份/回退脚本。

详细设计、公式与边界见 [改进说明](docs/CHANGES_011.md) 和 [当前规格](docs/PROJECT_SPEC.md)。旧版文档在 `docs/history/` 与原版本号文档中保留，只表示历史实现/测试。

## 实际验收

本版完成 Linux 环境下后端、前端单测、TypeScript、生产构建、真实 Chromium 桌面/窄屏 E2E、实际 HTTP Smoke、合成离线对照与 v0.10.2 数据兼容检查。精确数量及执行日志见 [测试报告](docs/TEST_REPORT_011.md)；实验输入、输出、断言与新旧对照见 [离线实验报告](docs/OFFLINE_EXPERIMENTS_011.md)。

本次没有启用真实付费 LLM、IGDB 或 Steam 网络数据验收；Windows/macOS 和 Docker 未实机执行。mock 是明确选择的离线测试模式，live 失败不会静默改为模拟成功。

## 开发验证

```bash
cd backend
uv sync --frozen
DATA_MODE=fixtures LLM_MODE=mock uv run pytest -q
uv run ruff check app
uv run python ../scripts/verify_v011.py
uv run python ../scripts/smoke_v011.py
cd ../frontend
npm ci
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

不要并行启动两个 `test:e2e`（当前保留的 CJS 转译入口共用临时路径）。首次浏览器安装需要网络；Linux 必要时使用 `npx playwright install --with-deps chromium`。配置/API 来源说明仍见 [CONNECTIONS.md](docs/CONNECTIONS.md)，本次无需新增密钥。
