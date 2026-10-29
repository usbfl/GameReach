# GameReach 玩家语言标签词典 v1

共 64 个标签。词表与源码 vocabulary.json 同步生成。所有映射用于扩大召回；没有标签不意味着没有体验。

|分类|标签 ID / 名称|玩家表达（同义线索）|判断边界|调研依据|
|---|---|---|---|---|
|玩法类型|action_combat / 实时动作战斗|动作、即时战斗、action combat|需要实时操控角色的攻击或移动，而非仅下回合指令。 动作类型不等于高难或必须读招；仍需评论说明。|S02,S04|
|合作与社交|cooperative_roles / 合作分工|合作、联机、组队、分工、co-op、cooperative|玩家之间以职业、能力或资源互补完成目标。 联网或多人标签不能证明合作深度，竞技另查。|S21|
|合作与社交|solo_viability / 单人游玩可行|单人、独狼、solo、singleplayer|独自游玩时仍有可完成的流程与合适的辅助。 有单人按钮不代表体验等价；必须保留难度与模式。|S21|
|合作与社交|communication_load / 沟通与社交负担|开麦、语音、社交压力、沟通、voice chat|完成目标需要多大程度的语音、文字或默契协调。 联机不自动等于强社交；陌生人踢人和配合成本分开。|S21|
|生存与建造|survival_needs / 生存维护|生存、氧气、饥饿、survival|氧气、饥饿或其他环境约束需要持续管理。 生存类型不等于高战斗难度或必须刷材料。|S22,S23|
|生存与建造|base_building / 基地建造|建家、基地、建造、base building|建造、布置和扩张持续使用的据点。 搭建功能不等于自由美观布置；需具体评论。|S22,S23|
|经营与资源|emergent_stories / 系统涌现故事|涌现、故事生成、殖民者、emergent、story generator|角色关系与系统事件相互作用形成玩家自己的故事。 预设剧情丰富不是同一概念；坏结果也可能有趣。|S23|
|运行体验|mod_flexibility / 模组扩展|模组、mod、modding|模组提供玩法调整或内容扩展的空间。 模组体验不等于原版，需标明mode。|S23,S24|
|叙事与氛围|horror_tension / 持续恐惧与压迫|恐怖、压迫、害怕、恐惧、horror、dread|通过未知、环境或追踪形成持续的紧张感。 是主观体验，不能用平均星级否认某位玩家的恐惧。|S22,S24|
|叙事与氛围|jump_scares / 突发惊吓|跳吓、突脸、突然吓人、jumpscare、jump scare|突然的画面、声音或事件造成惊吓。 持续氛围恐怖与突发惊吓不同，没提到不能当没有。|S22|
|战斗与操作|stealth_waiting / 潜行与躲藏等待|潜行、躲柜子、躲藏、stealth、hiding|通过隐蔽、观察巡逻或等待时机推进。 安静氛围不是潜行；等待时间与紧张感可同时存在。|S24|
|战斗与操作|read_patterns / 读招抓时机|读招、战斗技巧、抓时机、看破招式、learn patterns、timing windows|观察敌人前摇、攻击节奏，选择反击窗口。 难或高伤害本身不能证明读招；策略思考不等于反应。|S04,S10|
|战斗与操作|precise_dodge / 走位与精确闪避|走位、闪避、躲招、dodge、positioning|通过位置、方向或闪避时机规避攻击。 有闪避键不等于操作精确；不能仅凭类型推断。|S02,S10|
|战斗与操作|parry_counter / 弹反与反击|弹反、招架、妖反、parry、deflect、counter|在敌人攻击窗口用格挡或反击改变攻防。 自动格挡与时机反击不同。|S04|
|战斗与操作|combo_expression / 连段与招式衔接|连段、连招、派生、残心、取消后摇、combo、cancel|通过取消、派生、切换招式表达熟练操作。 按钮多不必然自由；按键复杂与选择丰富分开。|S04|
|战斗与操作|stamina_management / 战斗资源管理|精力管理、体力条、stamina、ki pulse|攻击防御与移动之间需要管理体力等资源。 局外材料刷取与战斗内资源不是一回事。|S04|
|战斗与操作|responsive_movement / 移动响应与操控感|手感、操作流畅、响应、snappy、responsive|输入反馈及时，移动或跳跃能稳定按意图执行。 好评里的手感只是主观判断，不能泛化到所有设备。|S10,S12|
|战斗与操作|boss_practice / 练习攻克Boss|Boss、boss、反复挑战、练习、背板|通过重复挑战学习对手并获得熟练收益。 练Boss不等于刷装备；练习次数不是客观难度。|S10|
|战斗与操作|combat_readability / 战斗信息清晰|光污染、看不清、特效、readability、visual clutter|能辨认敌人动作、危险范围和自己的状态。 画面精美与战斗可读性可以相反。|S04,S12|
|玩法类型|soulslike / 魂类体验|魂类、类魂、魂、Soulslike、souls-like|玩家明确描述本作具有类魂的动作挑战体验。 不继承刷取、惩罚或地图复杂等未提及特征。|S03,S10|
|构筑与成长|build_variety / 构筑改变打法|构筑、多构筑、流派、配装、不同打法、build variety、buildcraft|不同技能、装备或组合导致有意义的操作或策略变化。 不把只换伤害颜色、纯数值更高当作打法多样。|S02,S04,S06,S17|
|构筑与成长|synergy / 组件联动|联动、组合技、协同、synergy、combo pieces|技能、道具、遗物或卡牌之间有可解释的相互作用。 物品多或属性相加不自动等于协同。|S06|
|构筑与成长|adaptive_builds / 临场构筑|临场构筑、随机应变、现抓现组、adapt、improvised build|根据本局奖励、敌人和资源改变方案。 不等同于预先规划固定毕业配装。|S02,S06,S19|
|构筑与成长|deckbuilding / 牌组构筑|卡牌构筑、牌组构筑、卡组、deckbuilding、deckbuilder|通过选择、删除或升级卡牌改变牌组结构。 抽角色养卡与对局中构筑牌组不同。|S06,S19|
|构筑与成长|weapon_styles / 武器与角色玩法差异|武器差异、架势、职业差异、weapon、stance、loadout|更换武器、架势或角色会改变可用动作和决策。 外观换皮不能独立证明。|S02,S04|
|构筑与成长|meta_progression / 局外成长|局外成长、永久升级、meta progression、permanent upgrades|一次尝试结束后保留升级或解锁以影响后续尝试。 局外成长不等于必须大量刷资源。|S02|
|构筑与成长|build_balance / 流派平衡与自由|流派平衡、套路单一、最优解、build balance、overpowered|多种打法有可行空间，不被单一强势套路明显挤压。 存在多种组件不能证明每种都有效。|S02|
|学习与挑战|learning_curve / 系统学习成本|上手、学习成本、教程、复杂系统、learning curve|理解规则、组合和操作需要多大投入。 熟练后顺手不代表新手容易；脑力与手速分开。|S04,S17|
|学习与挑战|configurable_difficulty / 难度辅助选项|难度可调、辅助模式、上帝模式、god mode、assist mode|有能改变游玩负担的难度或辅助模式。 特殊模式的结论必须标明模式。|S02|
|重复与刷取|resource_grind / 装备资源刷取|刷装备、刷材料、刷级、肝、grind、farming|为装备、材料、经验反复重复已掌握内容。 重复挑战、重开和新剧情不自动属于刷装备；区分可选和必需。|S01,S03,S11,S14|
|重复与刷取|mandatory_grind / 推进依赖刷取|必须刷、不刷过不去、卡资源、required grind、mandatory farming|正常推进在某阶段需要重复刷取资源或等级。 必须有推进依赖证据，不能由存在刷取系统推出。|S03,S11,S14|
|重复与刷取|loot_rng / 随机掉落与洗练|随机掉落、洗词条、看脸、drop rate、loot rng、reroll|获得目标装备或词条依赖概率并可能反复尝试。 局面随机和掉落随机不同；低概率需具体描述。|S14,S17|
|重复与刷取|repeated_content / 关卡内容重复|重复内容、重复关卡、同一张图、repetitive、same rooms|场景、敌人、任务或流程反复出现。 重复场景不等于构筑不变；主观厌烦不能替代内容描述。|S01,S02,S14|
|重复与刷取|optional_endgame / 可选后期追求|毕业、多周目、后期、endgame、ng+|主线之外的毕业装备、极限挑战或成就目标。 不能把后期负担回填一周目，也不能反向推断。|S03,S10|
|失败与重试|death_loss / 死亡损失|死亡惩罚、死亡损失、掉钱、捡尸、death penalty、corpse run|失败带来货币、物品或进度损失及其恢复条件。 重走距离、重新开局和资源损失分别判断。|S03,S07,S10|
|失败与重试|runback / 失败后重走|跑尸、回Boss、重走、runback、walk back|重试同一挑战前需重复移动或清理的路程。 不要把跑图简单直接解释成短跑尸。|S10|
|失败与重试|quick_retry / 快速重试|秒重开、快速重试、就近复活、quick retry、checkpoint|失败后能较快回到核心挑战。 死亡没有损失不等于重试方便；未提跑尸不能算快速。|S10|
|失败与重试|run_restart / 失败后重开一局|重开、重新开局、permadeath、restart run|一次失败结束当前运行，需要新的流程尝试。 不等于删除全部长期进度。|S01,S02,S07|
|探索与空间|navigation / 寻路负担|跑图、寻路、迷路、导航、navigation、getting lost|辨认地图、路线、目标位置的认知负担。 地图大不等于难找路；旅途长与认路难不同。|S10,S15|
|探索与空间|interconnected_world / 互联地图|箱庭、互联地图、捷径、interconnected、shortcuts|区域之间通过捷径和可回访连接形成空间关系。 开放世界标签不能证明关卡互联质量。|S10|
|探索与空间|exploration_discovery / 自主探索与发现|探索、发现秘密、闲逛、exploration、discovery|偏离明确路线能发现地点、秘密或新信息。 问号清单数量不能直接等同发现感。|S07,S10,S15|
|探索与空间|knowledge_progression / 知识驱动进展|知识驱动、信息解锁、knowledge、learn the rules|理解线索或规则本身让玩家能够抵达新目标。 知识不是可永久升级的能力值，注意剧透。|S07|
|探索与空间|precision_platforming / 精确跳跃与移动挑战|跳跳乐、平台跳跃、跑酷、platforming、wall jump|关卡要求控制跳跃、冲刺或连续移动。 战斗难度与跳跃难度分开。|S10|
|思考与随机|logical_puzzles / 逻辑解谜|解谜、谜题、逻辑、推理、puzzle、logic|通过推理规则或关系求出解法。 搜索隐藏物品与推理不等价；版本需明确。|S13|
|思考与随机|turn_based_tactics / 回合与战术决策|回合制、战术、策略、turn-based、tactics|行动前可思考，决策涉及位置、顺序或资源。 不是一切策略游戏都有回合制。|S05,S06,S17|
|思考与随机|positioning_tactics / 位移与环境解题|位移解题、地形、推拉、positioning、terrain|利用推拉、地形或站位改变局面。 战棋外观不能证明位移机制。|S05|
|思考与随机|input_randomness / 随机局面下决策|随机地图、随机奖励、input randomness、procedural|先生成随机局面，再由玩家选择应对。 不自动意味着行动结果靠运气。|S05,S06|
|思考与随机|outcome_randomness / 行动结果依赖概率|命中率、看运气、概率判定、output randomness、rng|作出选择后命中、效果或胜负仍受随机影响。 不要把所有随机机制混为一个随机性分数。|S05,S17|
|思考与随机|risk_reward / 风险收益取舍|风险收益、资源取舍、risk reward、trade-off|选择额外收益时需要承担可说明的代价。 单纯高难或奖励多不足以支持。|S02,S06|
|叙事与氛围|narrative_focus / 剧情与角色驱动|剧情、角色、叙事、story、narrative、dialogue|故事、对白或角色关系构成主要吸引力。 商店Story Rich不能证明用户会喜欢该故事。|S01,S08,S15|
|叙事与氛围|reading_load / 文字阅读负担|大量文字、阅读、长对话、reading、dialogue heavy|理解体验需要连续阅读大量对白或说明。 对白质量好不等于阅读量适合所有人。|S08|
|叙事与氛围|atmosphere / 世界氛围与沉浸|氛围、沉浸、世界观、atmosphere、immersive|场景、声音和叙事共同塑造明确的氛围体验。 精美截图不能代替玩家体验依据。|S10,S15|
|叙事与氛围|art_style / 画风与视觉审美|画风、美术、手绘、像素、art style、visuals|玩家对美术风格和场景表现的偏好。 画面偏好不推出玩法偏好；不要用主观审美填事实。|S02,S09,S15|
|节奏与日常|time_pressure / 时间与效率压力|时间压力、赶时间、日夜、体力限制、time pressure、energy bar|计时、日程或资源损耗使玩家感到赶进度。 可爱或休闲标签不保证无压力。|S09|
|节奏与日常|daily_chores / 日常维护与上线负担|每日任务、打卡、日常、上线、daily chores、daily quest|为了进展或收益需重复每日任务或定时上线。 短日常与低总投入不等同。|S16,S17|
|节奏与日常|relaxed_pace / 自主节奏|轻松、慢节奏、休闲、不赶时间、relaxing、own pace|能按自己的节奏推进，不被明确日程或竞争催促。 特定玩家感觉放松不能证明所有玩法无压力。|S09,S17|
|节奏与日常|idle_progress / 挂机成长|放置、挂机、收菜、idle、afk|离线或低操作情况下获得持续成长。 挂机不代表不氪、无日常或短时间完成。|S16,S17|
|节奏与日常|session_flexibility / 分段游玩便利|碎片时间、短局、随时停、短时间、short sessions|短时游玩、暂离或回归仍可推进和理解目标。 单局短不代表可中途保存；需逐项核实。|S08,S17|
|经营与资源|automation / 生产自动化|自动化、流水线、生产链、automation、factory|设计生产和物流系统以替代重复手工劳动。 与挂机收菜、自动战斗不同。|S18|
|经营与资源|resource_planning / 资源规划与经营|经营、资源管理、规划、management、resource planning|分配有限资源，在扩张、效率和风险之间选择。 资源稀缺不自动等于有策略深度。|S06,S09,S18|
|付费与体验|pay_power / 付费影响实力|氪金、逼氪、pay to win、p2w|付费影响强度、成长速度或竞争条件。 不付费能玩不代表与付费者处境相同。|S11,S16,S17|
|付费与体验|gacha_dependency / 抽取与收集门槛|抽卡、保底、限定、gacha、pity|获得角色、技能或关键组件依赖抽取及保底机制。 付费与时间两种成本独立记录。|S14,S16,S17|
|运行体验|performance_stability / 性能与稳定性|卡顿、崩溃、掉帧、bug、stutter、crash|卡顿、崩溃或影响推进的错误。 必须保留平台与补丁日期，不把旧问题自动当现状。|S04,S13,S14|
|玩法类型|roguelite / 单局循环与变化|肉鸽、肉鸽类、roguelike、roguelite、rogue-lite|玩家明确描述多次运行与局间或局内变化。 只提重玩不足以证明类型，局外成长另查。|S01,S02,S06|

## 平台映射

“—”表示尚无已核对直接标签；不能编造一个标签填上。下表多数为宽泛召回，精确标签对应关系见 JSON。运行时统一经 IGDB 的类型/关键词与商店链接召回，未冒称已接通各商店原生搜索。

|标签|Steam|GOG|Epic|itch.io|
|---|---|---|---|---|
|实时动作战斗|Action / Action RPG / Hack and Slash|Action|Action / RPG|Action|
|合作分工|Co-op / PvE / Class-Based|—|—|—|
|单人游玩可行|—|—|—|—|
|沟通与社交负担|Co-op / PvE / Class-Based|—|—|—|
|生存维护|Survival / Crafting / Base Building|Survival / Base Building|—|—|
|基地建造|Survival / Crafting / Base Building|Survival / Base Building|—|—|
|系统涌现故事|Sandbox|Sandbox / Management / Simulation|—|—|
|模组扩展|—|—|—|—|
|持续恐惧与压迫|Horror / Survival Horror|—|—|—|
|突发惊吓|Horror / Survival Horror|—|—|—|
|潜行与躲藏等待|—|—|—|—|
|读招抓时机|Action / Action RPG / Hack and Slash|Action|Action / RPG|Action|
|走位与精确闪避|Action / Action RPG / Hack and Slash|Action|Action / RPG|Action|
|弹反与反击|Action / Action RPG / Hack and Slash|Action|Action / RPG|Action|
|连段与招式衔接|Action / Action RPG / Hack and Slash|Action|Action / RPG|Action|
|战斗资源管理|Action / Action RPG / Hack and Slash|Action|Action / RPG|Action|
|移动响应与操控感|Platformer|Platformer|Action|Action|
|练习攻克Boss|Action / Action RPG / Hack and Slash|Action|Action / RPG|Action|
|战斗信息清晰|—|—|—|—|
|魂类体验|Souls-like|Souls-like|Action / RPG|Action|
|构筑改变打法|Action RPG / Deckbuilding|Role-playing / Strategy|RPG|Role Playing|
|组件联动|Action RPG / Deckbuilding|Role-playing / Strategy|RPG|Role Playing|
|临场构筑|Action Roguelike / Roguelite|—|Rogue-Lite|—|
|牌组构筑|Deckbuilding / Roguelike Deckbuilder|Strategy / Turn-Based|—|—|
|武器与角色玩法差异|Action / Action RPG / Hack and Slash|Action|Action / RPG|Action|
|局外成长|Action Roguelike / Roguelite|—|Rogue-Lite|—|
|流派平衡与自由|Action RPG / Deckbuilding|Role-playing / Strategy|RPG|Role Playing|
|系统学习成本|—|—|—|—|
|难度辅助选项|—|—|—|—|
|装备资源刷取|—|—|—|—|
|推进依赖刷取|—|—|—|—|
|随机掉落与洗练|—|—|—|—|
|关卡内容重复|Action Roguelike / Roguelite|—|Rogue-Lite|—|
|可选后期追求|—|—|—|—|
|死亡损失|—|—|—|—|
|失败后重走|—|—|—|—|
|快速重试|—|—|—|—|
|失败后重开一局|Action Roguelike / Roguelite|—|Rogue-Lite|—|
|寻路负担|Exploration / Metroidvania / Adventure|Exploration / Metroidvania / Adventure|—|Adventure|
|互联地图|Exploration / Metroidvania / Adventure|Exploration / Metroidvania / Adventure|—|Adventure|
|自主探索与发现|Exploration / Metroidvania / Adventure|Exploration / Metroidvania / Adventure|—|Adventure|
|知识驱动进展|Strategy|—|—|—|
|精确跳跃与移动挑战|Platformer|Platformer|Action|Action|
|逻辑解谜|Strategy|—|—|—|
|回合与战术决策|Turn-Based / Strategy|Tactical / Turn-Based|—|—|
|位移与环境解题|Turn-Based / Strategy|Tactical / Turn-Based|—|—|
|随机局面下决策|Action Roguelike / Roguelite|—|Rogue-Lite|—|
|行动结果依赖概率|—|—|—|—|
|风险收益取舍|Turn-Based / Strategy|Tactical / Turn-Based|—|—|
|剧情与角色驱动|Story Rich|Story Rich / Visual Novel|RPG|Visual Novel / Adventure|
|文字阅读负担|Story Rich|Story Rich / Visual Novel|RPG|Visual Novel / Adventure|
|世界氛围与沉浸|Exploration / Metroidvania / Adventure|Exploration / Metroidvania / Adventure|—|Adventure|
|画风与视觉审美|—|—|—|—|
|时间与效率压力|—|—|—|—|
|日常维护与上线负担|—|—|—|—|
|自主节奏|Casual|—|—|—|
|挂机成长|—|—|—|—|
|分段游玩便利|—|—|—|—|
|生产自动化|Strategy|Strategy|—|—|
|资源规划与经营|Strategy|Strategy|—|—|
|付费影响实力|—|—|—|—|
|抽取与收集门槛|—|—|—|—|
|性能与稳定性|—|—|—|—|
|单局循环与变化|Action Roguelike / Roguelite|—|Rogue-Lite|—|

## 来源登记

|ID|来源与页面|观察（归纳）|访问结果|
|---|---|---|---|
|S01|[Reddit · Hades: how is this fun?](https://www.reddit.com/r/patientgamers/comments/s3xtcd/hades_how_is_this_fun/)|同一循环有人视为磨耗，有人享受每局武器和祝福变化。|page_body_available|
|S02|[Reddit · Hades: repetitiveness and builds](https://www.reddit.com/r/patientgamers/comments/1antgbz/hades_a_good_game_whose_repetitiveness_holds_it/)|临场构筑、闪避和风险收益受到赞赏，但该作者也质疑地图重复、数值换皮与强势套路。|page_body_available|
|S03|[Steam Community · Nioh 2: Is worth to grind?](https://steamcommunity.com/app/1325200/discussions/0/5760637065632031377/)|讨论对早期刷级和多周目追装备有不同回答，必须保留阶段与个人判断。|page_body_available|
|S04|[机核 · 动作深奥，画面有瑕：仁王2玩家评测](https://www.gcores.com/articles/188414)|作者讨论残心、架势、技能衔接、研究成本和视觉可读性；属于个人投稿。|page_body_available|
|S05|[Reddit · Into the Breach: tactics and randomness](https://www.reddit.com/r/patientgamers/comments/w8ubse/into_the_breach_is_one_of_the_finest_tactics/)|评论区区分局面生成随机性与行动结果随机性，提到位移、状态和解题感。|page_body_available|
|S06|[机核 · 杀戮尖塔卡组构筑玩法系统分析](https://www.gcores.com/articles/212672)|用户投稿以路线、过渡牌、遗物联动和未来敌人解释构筑取舍。|page_body_available|
|S07|[Reddit · Outer Wilds: knowledge as progression](https://www.reddit.com/r/patientgamers/comments/1gpjf3h/outer_wilds_what_does_knowledge_is_the_only/)|知识进展与数值成长不同；提问者仍不喜欢失败后的重走过程。|page_body_available|
|S08|[Reddit · What am I missing with Disco Elysium?](https://www.reddit.com/r/patientgamers/comments/15ujpcv/what_am_i_missing_with_disco_elysium/)|有人肯定对白但难以长时间阅读，叙事质量与阅读负担应分开。|page_body_available|
|S09|[Reddit · Am I missing something about Stardew Valley?](https://www.reddit.com/r/patientgamers/comments/eyaamp/am_i_missing_something_about_stardew_valley/)|可爱画面与经营压力可以并存，日夜和体力机制会使部分玩家焦虑。|page_body_available|
|S10|[Steam Community · Tomatoshadow: Silksong review](https://steamcommunity.com/id/tomatoshadow/recommended/1030300/)|好评同时提到长跑尸、反复练习、移动操作和探索惊喜；喜欢与代价并不互斥。|page_body_available|
|S11|[GOG · Agarest: Generations of War user reviews](https://www.gog.com/en/game/agarest_generations_of_war)|可见差评讨论重复战斗升级、材料链和推进节奏；只是该评论观点。|page_body_available|
|S12|[GOG Forum · How do you score/rate games?](https://www.gog.com/forum/general/how_do_you_scorerate_games/page1)|玩家区分响应速度、清晰度、重复跑腿和个人评分尺度。|page_body_available|
|S13|[itch.io · Baba Is You Jam Build comments](https://hempuli.itch.io/baba-is-you/comments?after=0)|玩家提及逻辑解谜、创意与过难；Jam版反馈不能当正式版证据。|page_body_available|
|S14|[TapTap · 二重螺旋玩家评价](https://www.taptap.cn/app/388717/review)|同一评价可肯定角色和探索、反感固定副本与随机碎片；免费获得不等于低时间成本。|page_body_available|
|S15|[TapTap · 幻塔玩家评价](https://www.taptap.cn/app/192675/review)|玩家喜欢闲逛景观但批评任务引导与寻路，开放世界不等于易导航。|page_body_available|
|S16|[TapTap · 洛伊的移动要塞玩家评价](https://www.taptap.cn/app/739892/review)|可见更新评价从初期兴奋转为后期资源停滞，必须保留评论修订与进度。|page_body_available|
|S17|[TapTap · 无名江湖玩家评价](https://www.taptap.cn/app/235106/review)|短日常、配队与长期数值、抽取概率可并存；有反讽五星评价，星级不能决定句子含义。|page_body_available|
|S18|[Steam Community · Factorio: what should I automate?](https://steamcommunity.com/app/427520/discussions/0/604161119746127162/)|讨论将重复手工制作改成生产自动化，区分自动化经营与无操作挂机。|page_body_available|
|S19|[Reddit · Slay the Spire: give me the real tips](https://www.reddit.com/r/slaythespire/comments/1ja39hd/give_me_the_real_tips/)|讨论围绕下一场挑战而选牌，构筑不只指预设最终流派。|page_body_available|
|S20|[Reddit · Into the Breach: an amazing indie](https://www.reddit.com/r/patientgamers/comments/g9ng51/into_the_breach_an_amazing_indie/)|检索发现有人把确定性当优点也当缺少惊喜的代价；仅作待验证扩展线索。|search_snippet_only|
|S21|[Steam Community · Deep Rock Galactic: Worth playing solo?](https://steamcommunity.com/app/548430/discussions/1/3203746811791854833/)|单人机器人协助、职业合作、无需语音与路人社交风险分开记录；有人觉得单人无聊，也有人喜欢。|page_body_available|
|S22|[Steam Community · Subnautica: how scary is the game?](https://steamcommunity.com/app/264710/discussions/0/5154944131320023232/)|深水未知引发的紧张与突发惊吓不同；相同环境有人放松、有人恐惧，需保留主观分歧。|page_body_available|
|S23|[GOG · RimWorld user reviews](https://www.gog.com/en/game/rimworld)|玩家从殖民者关系、灾难和模组谈系统涌现叙事；一条评价明确自Steam转载，跨站不能重复计票。|page_body_available|
|S24|[Reddit · Alien Isolation: disappointing slog but beautiful](https://www.reddit.com/r/patientgamers/comments/1806zsq/alien_isolation_is_a_disappointing_slogbut_its_so/)|同一评论赞美美术、声音与控制，批评躲藏等待和重复；还提到修改弹药，不能泛化到默认难度。|page_body_available|
|P12|[Steam · Deep Rock Galactic tags](https://store.steampowered.com/app/548430/Deep_Rock_Galactic/)|核对Co-op、PvE、Class-Based、Shooter；Single-player是功能字段。|page_body_available|
|P13|[Steam · Subnautica tags](https://store.steampowered.com/app/264710/Subnautica/)|核对Survival、Crafting、Base Building、Horror；玩家标签含Multiplayer而功能标单人，反映标签不能直接证明功能。|page_body_available|
|P01|[Steamworks · Steam Tags](https://partner.steamgames.com/doc/store/tags)|平台标签用于可见性和召回，不能替代体验证据。|page_body_available|
|P02|[Steamworks · IUserReviewsService](https://partner.steamgames.com/doc/webapi/IUserReviewsService)|完整review字段；query_summary评分仅在首个all类型请求返回。|page_body_available|
|P03|[Epic · Ratings and Polls](https://store.epicgames.com/en-US/news/the-epic-games-store-ratings-and-polls-update)|官方说明评分和玩家投票标签；不是自由文本评价。|page_body_available|
|P04|[itch.io · Content creator quality guidelines](https://itch.io/docs/creators/quality-guidelines)|优先建议标签，避免同义重复；元数据分类与标签不同。|page_body_available|
|P05|[Steam · Hades store tags](https://store.steampowered.com/app/1145360/Hades/)|核对Action Roguelike、Roguelite、Hack and Slash等标签。|page_body_available|
|P06|[Steam · Slay the Spire store tags](https://store.steampowered.com/app/646570/Slay_the_Spire/)|核对Deckbuilding、Turn-Based、Strategy、Roguelike Deckbuilder等。|page_body_available|
|P07|[Steam · Hollow Knight store tags](https://store.steampowered.com/app/367520/Hollow_Knight/)|核对Souls-like、Metroidvania、Platformer、Exploration等。|page_body_available|
|P08|[GOG · Hollow Knight store tags](https://www.gog.com/en/game/hollow_knight)|核对Action、Difficult、Exploration、Platformer、Souls-like等。|page_body_available|
|P09|[Epic · Hades genres and features](https://store.epicgames.com/en-US/p/hades)|核对Action、Rogue-Lite、RPG；Single Player属于features。|page_body_available|
|P10|[itch.io · Top tags: genres](https://itch.io/tags/genres)|核对Action、Adventure、Role Playing、Visual Novel等分类。|page_body_available|
|P11|[IGDB · API reference](https://api-docs.igdb.com/)|目录的genres、keywords、platforms和商店链接用于召回。|page_body_available|
