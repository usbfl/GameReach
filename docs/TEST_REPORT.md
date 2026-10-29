# 0.9.1 验证记录

日期：2026-10-21。所有契约测试使用明确编写的合成响应；真实来源检查单列。

| 检查 | 实际结果 | 边界 |
|---|---|---|
| `uv sync --frozen` | 通过 | 锁文件与版本一致 |
| 后端 `uv run pytest -q` | **219 passed，52.17 秒** | 1 条第三方 Starlette/AnyIO 弃用警告 |
| 新增 `test_tag_recovery.py` | **10 passed** | 预算中断、单款模型故障、来源错误、场景、分层部分失败、空响应、live 类 HTTP 协议贯通 |
| Ruff：app/tags 与新增回归 | 通过 | 改动模块静态检查 |
| `npm ci --offline --ignore-scripts` | 179 包安装成功 | 未变更第三方依赖版本 |
| `npm run build` | TypeScript + Vite 通过 | dist 已重新生成 |
| `npm test` | **3 passed** | 数值格式 + 无数据/混合结果的服务端静态渲染；不等同浏览器交互 |
| `bash scripts/run_demo.sh` | 见 LOCAL_SMOKE_091.json | 真实启动脚本、迁移、后台 worker、HTTP API 和已打包前端 |
| SteamSource 匿名真实请求 | **通过；3 次请求，20 篇完整评价，取得总体统计** | AppID 1245620；仅检验来源，不验证真实模型质量 |
| `npm run test:e2e -- --max-failures=1` | **未完成：浏览器启动失败** | 共 24 项，首项因缺少可执行文件失败，其余 23 项未运行 |
| `npx playwright install chromium` | **失败** | 官方下载地址返回损坏 ZIP 或 HTTP 503；不宣称视觉验收通过 |
| 真实 IGDB + 真实 LLM 推荐 | **未执行** | 本轮未使用用户私密凭据，不能以 HTTP Mock 冒充真实效果验收 |
| Windows / Docker | 未实机验证 | 保留原启动入口，未声称通过 |

## 新增回归的关键断言

- 在调用第二款来源前，第一款已有 supported 主张；第二款预算异常后第一款仍可排序。
- 单款 extract 或 verify 失败不会使后续候选停止，也不会将失败主张自动通过。
- 评论失败和未调查的候选无排序分；未阅读时 coverage 为 null。
- 已核验后期限定证据不会泛化为全程；明确给出 scope_only 诊断。
- Steam 首次全情感请求使用 purchase_type=0；后续限流保留先前统计和完整正文。
- reviews 缺失只有明确零计数才作为空页；未知结构仍然拒绝。
- live 适配器与 TagLLM 的实际 JSON 代码经过 HTTP Mock 完整贯通；每个模型阶段接收完整正文。响应均为作者编写的测试替身。
- 无数据页面显示“尚未形成可推荐结论”及来源原因，不渲染评分卡、0% 或排序分。

## 复现

```bash
cd backend
uv sync --frozen
uv run pytest -q
uv run python ../scripts/check_steam_reviews.py --appid 1245620
cd ../frontend
npm ci
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

公开接口检查不读取私密密钥、不调用模型，只打印诊断和统计；会实际访问 Steam。正常/限流的离线启动结果记录在 LOCAL_SMOKE_091.json。旧版验证记录保留于 TEST_REPORT_090.md。
