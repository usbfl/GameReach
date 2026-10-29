# GameReach 0.9.0：从玩家表达建立标签与评价证据

调研与实现日期：2026-10-21。完整词典、逐项映射和可点击来源见同目录 `PLAYER_TAG_DICTIONARY.md`；机器可读来源登记见 `sources.json`，应用使用 `backend/app/tags/vocabulary.json`。

## 结论

旧机制体系的问题不只是分值不够准确：它过早要求把模糊愿望转换成固定尺度，并把证据空白变成准入失败。这次改为 **玩家体验标签 → 平台目录映射 → 完整评价语义证据 → 可解释排序**。先理解用户，再寻找游戏；不能先凭类型选定答案再找赞同它的句子。

标签不是普通关键词列表。例如“刷”“肝”“grind”只是线索，真正判断要区分练习提升、资源重复、强制刷取、可选毕业、单局重开等不同含义。同一份完整评价可以同时支持喜欢之处与需要考虑的代价。

## 1. 调研范围与方法

采用目的性、跨社区的定性编码，而非随机抽样或全网词频统计。围绕旧项目的操作/构筑/少刷取问题，扩展到策略、解谜、探索、叙事、经营、移动游戏日常与付费，再补入合作、单人、生存与恐怖体验。搜索中英文的赞扬、抱怨、推荐求助和分歧讨论，不只搜正向词。

共登记37个页面：23个取得可见正文的玩家评价/讨论/投稿页面（S01–S19、S21–S24），1条未作为全文证据的搜索线索（S20），13条官方说明或平台目录（P01–P13）。**这不是37篇独立玩家评价**：论坛页面包含多条回复，商店页可能展示有限评价，搜索索引也可能滞后。这里没有把浏览到的全部评论人数伪装成统计样本数。

具体步骤：

1. 用需求的原始表达发散检索：如 grind vs repetition、build variety、runback、navigation、reading load、solo/co-op、fear/jumpscare。
2. 打开原页面，阅读相关评价/主帖的完整可见正文及可见反对意见。未打开、折叠或分页未取得的部分不视为读过。
3. 以“一个体验或判断”为编码单元，分离机制描述、主观喜好、适用阶段与取舍。
4. 合并近义表达，拆开容易混淆的概念；保留反例与不能推出的结论。
5. 在平台官方规则、商店标签页核对映射名称；明确直接对应、宽泛线索和没有对应。
6. 将词义和语境规则写入LLM协议，再用程序校验来源、引文、版本与计数。

选择这些来源是为了发现表达边界和反例，不是证明某款游戏现在必然好玩。旧帖、试玩版本、改装游戏和后期玩法必须保留时间/模式。部分 Reddit 帖无法访问；《恶意不息》Steam页面遇到内容提示；豆瓣定向搜索未取得可靠正文。这些没有计入“完整评价已读”。

## 2. 从评论中提炼出的关键区别

|观察|应拆成的标签|为什么影响推荐|来源|
|---|---|---|---|
|同样反复挑战，有人享受变化，有人觉得耗损|练习攻克Boss、单局循环、重复内容、装备资源刷取|不能把所有重复都当“肝”，也不能把可重玩自动当优点|S01、S02、S10|
|早期不需刷取，多周目追毕业装备需要刷|装备资源刷取、推进必须刷取、可选毕业追求|保留stage，不能用主线体验证明毕业阶段也不刷|S03、S04|
|更高面板与改变打法并非一回事|构筑改变打法、组件联动、临场构筑、强势套路压缩选择|“装备很多”不足以证明构筑有意义|S02、S04、S06|
|失败后的成本各不相同|死亡损失、跑尸距离、快速重试、整局重开|不能把“死亡惩罚低”自动翻成地图简单|S07、S10|
|探索有惊喜，但任务引导难用|探索发现、互联地图、寻路负担、知识进展|开放世界/银河城标签不能说明导航是否轻松|S07、S15|
|局面随机与行动结果随机被不同玩家评价|输入随机、结果随机、位置战术、风险收益|不能用一个“随机性高”分数概括|S05、S06|
|认可文字质量，仍难以长时间阅读|叙事驱动、阅读负担、分段游玩便利|喜欢剧情不等于愿意一直读文本|S08|
|可爱经营也会产生日程焦虑|放松节奏、时间压力、资源规划|温馨画风不证明无压力|S09|
|角色免费获得，仍耗时间；短日常与长期养成并存|日常任务负担、抽取门槛、付费影响实力、挂机成长|免费不等于不肝；五星也可能反讽|S14、S16、S17|
|生产自动化在消除手工重复|生产自动化、资源规划、挂机成长|设计系统与等待资源不是同一种体验|S18|
|单人能玩、合作更有趣、无需开麦可以同时成立|单人游玩可行、合作分工、沟通负担|多人类型不应自动排除独狼，也不证明社交轻松|S21|
|同一海洋环境让一些人放松、另一些人害怕|持续恐惧、突发惊吓、生存维护、探索发现|类型与个人情绪不同；恐惧也不等于跳吓|S22|
|殖民者与系统事件形成故事，模组改变体验|系统涌现故事、模组扩展、资源规划|预设剧情与玩家故事不同；转载评价不能算两票|S23|
|差评仍赞美声音、视觉和操控，却反感躲藏等待|氛围、艺术风格、响应手感、潜行等待|好差评二分类不能替代句子语义；作者使用修改器要保留|S24|

以上是对玩家表达的归纳，不把个人观点写成游戏共识。每个标签的定义、别名和不能推出的含义均在词典中。

## 3. 词典结构与映射原则

种子词典共64项，覆盖15个体验类别。每项保存稳定ID、中文名称、定义、中英表达、混淆边界、研究来源、平台映射及IGDB代理检索词。

标签的“方向”独立于标签本身：同一个 `resource_grind`，可以是want，也可以是avoid。词典不预设每个玩家都讨厌刷取，也不会把“挑战”天然视为正面。

|意图示例|初步召回|评价必须回答的问题|
|---|---|---|
|想要有意义的构筑|Steam Action RPG / Deckbuilding；GOG Role-playing / Strategy 等宽泛线索|具体组合是否改变操作或决策，还是仅增加数值？|
|喜欢魂类，但不想反复刷装备|Souls-like可直接作为标签名称线索；刷取没有可靠的一对一平台标签|刷取是否必要，适用主线还是毕业，玩家有何不同经历？|
|喜欢合作但不想开麦|Steam Co-op / PvE / Class-Based|是否可用标记完成协作；是否有职业依赖、难度差异？|
|想要探索，但怕突然惊吓|Exploration / Adventure等召回|紧张来自环境还是跳吓，评价中的否定是否明确？|
|希望低跑尸和轻松寻路|没有经过核对的一对一直接映射|必须在评价中分别核对重试距离、地图引导，不能借标签代答|

Steam的用户标签、平台功能字段和IGDB keywords并非同一套事实。一个具体反例是本次访问的 Subnautica 玩家标签包含 Multiplayer，而功能区列 Single-player；这说明标签可以是噪声或特定玩法线索，不能直接证明原版功能。GOG也区分genre/tag；Epic的ratings/polls不能当成自由文本完整评价；itch.io作者填写的标签和玩家评论必须分开。[P01、P03、P04、P13]

**运行时边界**：当前四商店范围仍通过IGDB的链接、类型与关键词召回；映射表不是已接入原生标签API的声明。Steam接入完整review正文与总体评价摘要；GOG/itch/Epic独占游戏可能只有目录，显示待调查。机核、TapTap、Reddit用于建立词典，还不是自动抓取适配器。没有直接对应的平台标签保留空值，不能为了表格完整编造标签。

## 4. 输入与卡片

个人资料分析先形成类别，再写概括。例如标题“战斗与操作”，正文概括喜欢观察敌人、把握时机、容忍练习但不愿为数值重复刷取的取舍。标题只负责分类，不额外生成约束。

最多主动附加6张已确认卡片。卡片显示在输入框下方，可移除；添加/删除不改写原提示词。提交时检查卡片ID、确认状态和版本，保存标题及正文快照。任务中途卡片更新不会悄悄改变旧任务。

真实LLM首先输出：标签ID、want/avoid、重要度1/2/3、逐字输入依据、来源ID、适用场景、尚未明确的原句。当前输入覆盖同标签的附件意愿；附件只能补充，重要度最多1。未附加的历史不直接进入研究。参考作品名不是从模型记忆补齐机制的授权。

无法解释的条件保留为unmapped，不静默忽略，也不把整次输入判失败。若没有任何可解释标签，结束为部分结果、提示补充，不先发起无方向的全网搜寻。离线模式明确使用规则与合成注释演示；真实模式不得回退到它。

## 5. 完整评价与证据链

新流程取得整篇清洗正文后再提取标签；正文中的命令只是资料，不改变工具权限。默认好评/差评/近期分层抽样最多120篇；每款送入模型的整篇文档最多60篇，同时受32,000字符上限约束。无法容纳的长评价标为延期，不计全文阅读。

每次成功提取记录文档ID、正文哈希、`start=0`、`end=正文长度`。这表示完整文本确实提供给了模型，不意味着能证明模型关注了每一个字。UI分别展示取得/全文分析/未分析数量，JSON保留快照和账本，证据抽屉默认展开完整评价。

每条主张保存连续逐字引文、game/document ID、标签方向、适用范围和来源时间。逐字定位失败拒收；评论以外来源不能支持玩家体验标签。第二次独立语义核验检查：

- 否定和转折是否反转了关键词；没有提及能否被误认为不存在。
- 赞美的是本作还是比较对象；说的是主线、后期、特定难度还是模组。
- 星级与语句是否相反；是否有后续编辑修正前文。
- 短引文是否真正支撑主张，完整上下文是否推翻引文。

本文规则与自动回归不能替代真实模型质量评测。模型仍可能误判讽刺、中文歧义和具体机制，推荐页面保留原文以供人工核对。

## 6. 排序如何计算

对用户要求的每个标签，统计独立支持数S、反对数O。近义词出现次数不计票；同一作者或标准化重复正文合并，跨平台相同正文也合并。一个评论可对不同标签提供证据；同一评论里冲突观点会同时展示，不假装形成两个独立玩家。

- 匹配度：S/(S+O)，按用户重要度对已知标签加权；完全无证据为null。
- 覆盖度：有适用证据的标签权重 / 所有需求标签权重。
- 数量贡献：每标签min(S,3)/3，再按重要度加权。3是初始饱和参数，不是最低准入门槛。
- 平台口碑：Steam总体正负评价计数的Wilson 95%下界；不从平衡抽样计算好评率。页面同时显示总体好评率与样本总数。
- 排序分：100 × (0.60×匹配度×覆盖度 + 0.25×数量贡献 + 0.15×口碑下界)。口碑缺失时只用0.5的中性先验排序，仍显示未知；没有任何已知标签时不生成排序分。

这些参数是透明的工程起点，没有声称已经经过统计验证。不同平台评分尺度尚未混合。普通偏好缺证据仍可展示；明确必要要求有反证则排除、未知或冲突则待确认；没有匹配证据的候选是待调查。事实上的语言、平台、发售和预算范围单独标明。

重排复用固定游戏/主张/文档快照，并检查来源当前是否有效；不会重新搜索或调用模型。历史报告不覆盖。下一步质量评估应使用玩家标注的成对偏好，比较召回率、前列合拍程度、未知保留率与断章取义错误，而不是只优化“有多少张推荐卡”。

## 7. 已实现与仍需验证

本次代码实现标签协议、版本化附件、全文提取/核验账本、独立计数、平台总体口碑、排序、本地重排和新UI。保留旧报告兼容与现有数据，不迁移用户历史机制值为新标签事实。

尚未完成的外部工作：真实密钥下的IGDB+LLM端到端验收；GOG/itch等完整评价运行时适配器；原生商店标签检索；对同义转载的语义去重；超长评论的多调用完整处理；体育、竞速、竞技匹配、公会/MMO、无障碍等领域的进一步采样。不能把现有64项称为“全游戏通用且穷尽”的终版词表。

浏览器测试因环境缺少Chromium且下载失败而阻塞。后端、构建和单元验证见 `../TEST_REPORT.md`。真实体验质量需要实际玩家反馈，离线合成通过只证明流程与边界可运行。

## 来源链接

- [S01 · Reddit · Hades: how is this fun?](https://www.reddit.com/r/patientgamers/comments/s3xtcd/hades_how_is_this_fun/)
- [S02 · Reddit · Hades: repetitiveness and builds](https://www.reddit.com/r/patientgamers/comments/1antgbz/hades_a_good_game_whose_repetitiveness_holds_it/)
- [S03 · Steam Community · Nioh 2: Is worth to grind?](https://steamcommunity.com/app/1325200/discussions/0/5760637065632031377/)
- [S04 · 机核 · 动作深奥，画面有瑕：仁王2玩家评测](https://www.gcores.com/articles/188414)
- [S05 · Reddit · Into the Breach: tactics and randomness](https://www.reddit.com/r/patientgamers/comments/w8ubse/into_the_breach_is_one_of_the_finest_tactics/)
- [S06 · 机核 · 杀戮尖塔卡组构筑玩法系统分析](https://www.gcores.com/articles/212672)
- [S07 · Reddit · Outer Wilds: knowledge as progression](https://www.reddit.com/r/patientgamers/comments/1gpjf3h/outer_wilds_what_does_knowledge_is_the_only/)
- [S08 · Reddit · What am I missing with Disco Elysium?](https://www.reddit.com/r/patientgamers/comments/15ujpcv/what_am_i_missing_with_disco_elysium/)
- [S09 · Reddit · Am I missing something about Stardew Valley?](https://www.reddit.com/r/patientgamers/comments/eyaamp/am_i_missing_something_about_stardew_valley/)
- [S10 · Steam Community · Tomatoshadow: Silksong review](https://steamcommunity.com/id/tomatoshadow/recommended/1030300/)
- [S11 · GOG · Agarest: Generations of War user reviews](https://www.gog.com/en/game/agarest_generations_of_war)
- [S12 · GOG Forum · How do you score/rate games?](https://www.gog.com/forum/general/how_do_you_scorerate_games/page1)
- [S13 · itch.io · Baba Is You Jam Build comments](https://hempuli.itch.io/baba-is-you/comments?after=0)
- [S14 · TapTap · 二重螺旋玩家评价](https://www.taptap.cn/app/388717/review)
- [S15 · TapTap · 幻塔玩家评价](https://www.taptap.cn/app/192675/review)
- [S16 · TapTap · 洛伊的移动要塞玩家评价](https://www.taptap.cn/app/739892/review)
- [S17 · TapTap · 无名江湖玩家评价](https://www.taptap.cn/app/235106/review)
- [S18 · Steam Community · Factorio: what should I automate?](https://steamcommunity.com/app/427520/discussions/0/604161119746127162/)
- [S19 · Reddit · Slay the Spire: give me the real tips](https://www.reddit.com/r/slaythespire/comments/1ja39hd/give_me_the_real_tips/)
- [S20 · Reddit · Into the Breach: an amazing indie](https://www.reddit.com/r/patientgamers/comments/g9ng51/into_the_breach_an_amazing_indie/)（仅搜索线索）
- [S21 · Steam Community · Deep Rock Galactic: Worth playing solo?](https://steamcommunity.com/app/548430/discussions/1/3203746811791854833/)
- [S22 · Steam Community · Subnautica: how scary is the game?](https://steamcommunity.com/app/264710/discussions/0/5154944131320023232/)
- [S23 · GOG · RimWorld user reviews](https://www.gog.com/en/game/rimworld)
- [S24 · Reddit · Alien Isolation: disappointing slog but beautiful](https://www.reddit.com/r/patientgamers/comments/1806zsq/alien_isolation_is_a_disappointing_slogbut_its_so/)
- [P12 · Steam · Deep Rock Galactic tags](https://store.steampowered.com/app/548430/Deep_Rock_Galactic/)
- [P13 · Steam · Subnautica tags](https://store.steampowered.com/app/264710/Subnautica/)
- [P01 · Steamworks · Steam Tags](https://partner.steamgames.com/doc/store/tags)
- [P02 · Steamworks · IUserReviewsService](https://partner.steamgames.com/doc/webapi/IUserReviewsService)
- [P03 · Epic · Ratings and Polls](https://store.epicgames.com/en-US/news/the-epic-games-store-ratings-and-polls-update)
- [P04 · itch.io · Content creator quality guidelines](https://itch.io/docs/creators/quality-guidelines)
- [P05 · Steam · Hades store tags](https://store.steampowered.com/app/1145360/Hades/)
- [P06 · Steam · Slay the Spire store tags](https://store.steampowered.com/app/646570/Slay_the_Spire/)
- [P07 · Steam · Hollow Knight store tags](https://store.steampowered.com/app/367520/Hollow_Knight/)
- [P08 · GOG · Hollow Knight store tags](https://www.gog.com/en/game/hollow_knight)
- [P09 · Epic · Hades genres and features](https://store.epicgames.com/en-US/p/hades)
- [P10 · itch.io · Top tags: genres](https://itch.io/tags/genres)
- [P11 · IGDB · API reference](https://api-docs.igdb.com/)
