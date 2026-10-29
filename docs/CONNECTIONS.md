> 0.9.1：自动标签调查按游戏完成获取→全文提取→核验，单批最多12篇/32,000字符；旧报告需重新探索。空结果看逐款诊断。匿名接口检查：在backend运行 `uv run python ../scripts/check_steam_reviews.py --appid 1245620`，仅使用Steam公开接口，不调用模型、不读取私密凭据。

> 0.9.0更新：新标签入口需要DATA_MODE=live与LLM_MODE=live。原有MIN_REVIEWS_FOR_RECOMMENDATION、MIN_CITED_REVIEWS、MIN_KNOWN_MECHANICS仅影响旧DNA协议。REVIEW_CONTEXT_LIMIT现在是完整评价篇数上限，另受每游戏32000字符预算限制；超长评价延期，不截断为全文。下文旧完整性门槛说明只适用于旧协议。

# 真实数据与模型接入

密钥只填写在项目根目录 `.env`，修改后重启后端。页面不接收密钥；网页上的 Steam 账号标识是另一项信息。

## 获取 IGDB 凭据

1. 打开 [Twitch 开发者控制台](https://dev.twitch.tv/console)，用自己的 Twitch 账号登录。按官方要求验证邮箱并启用双重验证。
2. 在 Applications 中选择 Register Your Application，填一个自己的应用名称和类别。Client Type 选择 Confidential 才能生成 Secret；IGDB 不使用 OAuth Redirect URL，其官方入门文档建议填 localhost，按注册页校验完成创建。
3. 进入应用 Manage，复制 **Client ID**；选择 New Secret 生成并复制 **Client Secret**。重建 Secret 会使旧 Secret 失效。
4. 写入 `.env`：

```dotenv
DATA_MODE=live
IGDB_CLIENT_ID=这里填写Client_ID
IGDB_CLIENT_SECRET=这里填写Client_Secret
```

项目自行通过 client_credentials 换取访问令牌；无需手动把临时 access_token 填进配置。不要把 Twitch 账号密码、Steam Key 或 Client Secret 放错字段。

官方步骤：[Twitch 应用注册](https://dev.twitch.tv/docs/authentication/register-app/)；[IGDB Getting Started](https://api-docs.igdb.com/)。注册页选项以你账户当前页面为准。

## Steam Web API Key 填什么

`STEAM_WEB_API_KEY` 应填写 [Steam API Key 页面](https://steamcommunity.com/dev/apikey) 登录注册后显示的 **Key 字符串**。不是 `https://steamcommunity.com/profiles/...`，也不是 17 位数字 SteamID。若注册页要求域名，填写该应用使用的域名并按页面要求完成验证；遇到账号限制时先使用文件导入。

```dotenv
STEAM_WEB_API_KEY=这里填写Steam发放的Key
```

在网页“我的游戏资料 → Steam 账号标识”中填写你的 SteamID64 或 `/profiles/数字/` 主页链接，再点击“读取我的 Steam 游戏库”。该操作有进度、超时和错误提示。游戏库不可见会提示权限问题，不会被当作空库；游戏详情和时长的可见性会影响读取。分类 / 收藏使用 CSV 或 JSON 文件导入。

官方依据：[GetOwnedGames](https://partner.steamgames.com/doc/webapi/iplayerservice)。本轮没有登录你的 Steam 账号，也没有验证个人游戏库实际可见性。

## 启用真实模型

```dotenv
LLM_MODE=live
LLM_BASE_URL=你所用服务的OpenAI兼容基础地址
LLM_API_KEY=你的模型密钥
LLM_MODEL=你的账户当前可用的模型ID
LLM_USE_TOOLS=true
```

BASE_URL 不包含 `/chat/completions`。DeepSeek 官方基础地址为 `https://api.deepseek.com` 或其 `/v1` 形式时，程序保留 `thinking={"type":"disabled"}` 兼容处理。模型 ID 需要你指定，项目不猜测账户权限。

## 如何确认没有误用演示模式

| 页面模式 | 实际行为 |
|---|---|
| fixtures + mock | 虚构游戏 / 评论；本地规则演示 |
| fixtures + live | 虚构游戏 / 评论；真实模型调用 |
| live + mock | 真实目录 / 评论；规则模型不能理解一般评论，玩法可能未知 |
| live + live | 真实目录 / 评论与真实模型；仍受可用来源、权限和预算限制 |

顶部模式标识表示配置，不是连接成功证明。开始一次探索后，检查：

- 游戏记录是否是 `live` 数据集，原始来源与商店链接是否指向对应真实游戏。
- “本次调查与任务控制”中的外部请求 / 角色调用，以及“调查过程”中模型或来源是否报错。
- 卡片底部显示本轮采集、实际送读、复用、引用评论数和已核验机制数；未达标的游戏在范围记录中说明暂缓推荐原因。
- 证据抽屉是否有原始来源、时间、逐字片段和位置；目录标签、热度或模型认识游戏名都不是已核验玩法。

缓存命中时真实来源可以不产生新请求，这不等于演示模式。检索零命中也不等于游戏没有该机制。

## 可选连接测试

仅在你准备调用真实服务时，把 `.env` 的 `RUN_LIVE_TESTS=1`，然后在 backend 目录执行：

```bash
uv run python ../scripts/live_smoke.py --components igdb --query Hades
uv run python ../scripts/live_smoke.py --components model
# 把 真实AppID 替换为已核对的数字，不要照抄占位符：
uv run python ../scripts/live_smoke.py --components steam_reviews --steam-app-id 真实AppID
```

完整推荐链路验证（使用现有配置，不增加预算；自动隔离测试数据库）：

```bash
uv run python ../scripts/verify_recommendations_live.py --env-file ../.env --allow-model --stores steam
# 可追加 --exact-name "Slay the Spire" 定位真实目录游戏；不填时验证偏好搜索。
```

结果写入 `artifacts/recommendations-live.json`，含实际模型 usage、采样与推荐数、引用定位、完整性和硬限制重排检查。零推荐会明确记录，不能当作推荐成功。模型/API 调用可能计费。该脚本不会读取你的私人游戏库。

`STEAM_WEB_API_KEY` 用于个人库读取；公开评论接口不需要该个人 Key。接口可选的鉴权 Key 面向相关应用发行商，程序不再把你的个人 Key 传给评论接口。


## 评论上限与完整性下限（0.8）

在项目根目录的 `.env` 添加或修改，保存后重启后端：

```dotenv
MAX_REVIEWS_PER_GAME=120
REVIEW_CONTEXT_LIMIT=60
MIN_REVIEWS_FOR_RECOMMENDATION=20
MIN_CITED_REVIEWS=3
MIN_KNOWN_MECHANICS=3
```

| 配置 | 含义 | 可设范围 |
|---|---|---|
| `MAX_REVIEWS_PER_GAME` | 每款初始调查最多采集的评论总数，按 ID 去重 | 20–500 |
| `REVIEW_CONTEXT_LIMIT` | 每款每次模型提取最多送入的独立评论片段数；不会把未送读的下载内容算作读过 | 20–120 |
| `MIN_REVIEWS_FOR_RECOMMENDATION` | 至少实际送读多少条仍有效、非重复的评论；允许复用已记录的阅读 | 1–120 |
| `MIN_CITED_REVIEWS` | 卡片和画像至少引用几条不同内容的已核验评论 | 1–12 |
| `MIN_KNOWN_MECHANICS` | 至少有几项有效的默认玩法画像；不要求全部18个维度都已知 | 1–18 |

须满足：引用下限 ≤ 送读下限 ≤ 送读上限 ≤ 采集上限。例：想多参考评论，设 `MAX_REVIEWS_PER_GAME=240`、`REVIEW_CONTEXT_LIMIT=100`；保持下限不变。提高采样上限不保证每款一定有足量可用评论，也不保证模型会引用全部样本。

默认按约 3/8 好评、3/8 差评、1/4 近期评论取样；这是用于了解体验的分层样本，不能据此算游戏好评率。长评只送入相关片段；重复、过期、过短或无有效来源的材料不充当充分证据。商店说明与 IGDB 目录另行交叉参考，不能冒充玩家评价。

Steam 单页最大100条，程序自动拆分请求。采样上限也属于缓存标识，修改后旧采样缓存不会冒充新样本。官方参考：[Steam IUserReviewsService](https://partner.steamgames.com/doc/webapi/IUserReviewsService?l=english)。

你提供的配置保留 `MAX_LLM_CALLS=18`、`MAX_EXTERNAL_REQUESTS=25`、`MAX_ACTIVE_SECONDS=180`。每款要完成采集、提取、核验，扩大采样后这份预算通常只能完成较少卡片。若你希望一次调查更多游戏，可自行改为 `MAX_LLM_CALLS=30`、`MAX_EXTERNAL_REQUESTS=60`、`MAX_ACTIVE_SECONDS=600`；这会增加等待和模型消耗，本次没有替你修改这些值。预算不足时仅保留已完成的合格卡片。

推荐还要求所有已识别的硬限制通过、全部已选软偏好维度已知、已知匹配不低于60/100，以及有来源的玩法介绍、具体优点和取舍。软偏好允许有取舍，不会被伪装成必须同时满足。尚无法解析的明确必要要求会阻止推荐；可以编辑成支持的条件后重新探索。旧报告仍为历史快照，需要重新探索才能获得新版完整性认证。
