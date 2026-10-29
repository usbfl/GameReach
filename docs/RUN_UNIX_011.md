# Linux / macOS 启动与验证

需要Python 3.11+，推荐uv。首次下载依赖需网络；包内自带生产前端和字体，启动不要求Node。Linux实际执行启动脚本与HTTP访问；macOS未实机验证。

项目根目录：

```bash
DATA_MODE=fixtures LLM_MODE=mock bash scripts/run_demo.sh
```

访问 <http://127.0.0.1:8000>，保留终端，Ctrl+C停止。脚本优先uv固定锁文件，没有uv时使用venv和固定requirements。若系统Python未安装venv支持，先安装该发行版对应Python venv组件。首次前端已经构建，无需重新编译。

想用独立演示DB且保留已有配置：

```bash
DATA_MODE=fixtures LLM_MODE=mock DATABASE_URL=sqlite:///./data/v011-smoke.db bash scripts/run_demo.sh
```

环境变量仅覆盖本次进程，不写回 `.env`。升级旧资料请先阅读 `UPGRADE_011.md`，不要把演示数据库当成迁移结果。

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

Linux缺浏览器系统依赖时，可在拥有系统安装权限的环境运行 `npx playwright install --with-deps chromium`。停止8000端口已有服务再跑E2E；不要同时跑两套浏览器测试。脚本产生暂存CJS文件，正常结束会清理；异常中断后再次运行会覆盖对应暂存文件。

验收环境：Linux x86_64、Python3.12，Chromium真实运行；兼容最低Python3.11来自项目配置，本次没有遍历所有Python/Node版本。Docker及macOS原生环境未执行，不能以Linux通过替代跨平台实机结论。
