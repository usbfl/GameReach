# Windows PowerShell 启动与验证

准备 Python 3.11+（安装时启用PATH），推荐安装uv。首次安装依赖需网络；已构建前端随包提供，单纯运行不需要Node。Windows本机未在本轮运行，命令按现有PowerShell启动脚本整理；Linux实际启动记录另见测试报告。

在已解压项目目录打开PowerShell：

```powershell
$env:DATA_MODE = 'fixtures'
$env:LLM_MODE = 'mock'
.\scripts\run_demo.ps1
```

若系统策略阻止本地脚本，可仅对当前进程运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1
```

也可双击 `Start_GameReach.bat`。打开 <http://127.0.0.1:8000>；Ctrl+C停止。不要关闭终端后期待服务继续运行。若8000已占用，停止自己的旧实例或手动修改启动端口；已有服务不要重复启动。

脚本优先 `uv sync --frozen --no-dev`，没有uv则用venv与固定 `requirements.txt`；随后运行Alembic并启动单worker。首次慢通常是依赖安装。`uv --no-dev` 会移除开发依赖，需要运行测试时重新 `uv sync --frozen`。

## 开发和测试

需要Node.js/npm以及uv；依赖安装后：

```powershell
$env:DATA_MODE = 'fixtures'
$env:LLM_MODE = 'mock'
Set-Location backend
uv sync --frozen
uv run pytest -q
uv run ruff check app
uv run python ..\scripts\verify_v011.py
uv run python ..\scripts\smoke_v011.py
Set-Location ..\frontend
npm ci
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

测试使用隔离合成数据库；运行浏览器E2E前停止8000端口开发服务，不要并行运行两套E2E。`npm run build` 包含TypeScript检查。

## 演示路径

导入 `data\fixtures\v011_player_library.json`，整理兴趣并确认两张主题卡；A跨卡、C无时长、F高时长排除。输入“构筑丰富，比较偏好快速重试”，先编辑标签，再运行研究。语言/价格等硬条件未知的候选保留待确认，不能算已满足。自动实验脚本会固定候选并校验预期，原始JSON位于 `docs\validation_v011`。

真实服务接入沿用 `CONNECTIONS.md`。已有 `.env` 不要被示例覆盖；本轮验收没有改动用户密钥，也没有真实付费调用。
