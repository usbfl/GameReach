import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sources=[
('S01','Reddit','Hades: how is this fun?','https://www.reddit.com/r/patientgamers/comments/s3xtcd/hades_how_is_this_fun/','同一循环有人视为磨耗，有人享受每局武器和祝福变化。','discussion'),
('S02','Reddit','Hades: repetitiveness and builds','https://www.reddit.com/r/patientgamers/comments/1antgbz/hades_a_good_game_whose_repetitiveness_holds_it/','临场构筑、闪避和风险收益受到赞赏，但该作者也质疑地图重复、数值换皮与强势套路。','review'),
('S03','Steam Community','Nioh 2: Is worth to grind?','https://steamcommunity.com/app/1325200/discussions/0/5760637065632031377/','讨论对早期刷级和多周目追装备有不同回答，必须保留阶段与个人判断。','discussion'),
('S04','机核','动作深奥，画面有瑕：仁王2玩家评测','https://www.gcores.com/articles/188414','作者讨论残心、架势、技能衔接、研究成本和视觉可读性；属于个人投稿。','review'),
('S05','Reddit','Into the Breach: tactics and randomness','https://www.reddit.com/r/patientgamers/comments/w8ubse/into_the_breach_is_one_of_the_finest_tactics/','评论区区分局面生成随机性与行动结果随机性，提到位移、状态和解题感。','discussion'),
('S06','机核','杀戮尖塔卡组构筑玩法系统分析','https://www.gcores.com/articles/212672','用户投稿以路线、过渡牌、遗物联动和未来敌人解释构筑取舍。','player_analysis'),
('S07','Reddit','Outer Wilds: knowledge as progression','https://www.reddit.com/r/patientgamers/comments/1gpjf3h/outer_wilds_what_does_knowledge_is_the_only/','知识进展与数值成长不同；提问者仍不喜欢失败后的重走过程。','discussion'),
('S08','Reddit','What am I missing with Disco Elysium?','https://www.reddit.com/r/patientgamers/comments/15ujpcv/what_am_i_missing_with_disco_elysium/','有人肯定对白但难以长时间阅读，叙事质量与阅读负担应分开。','discussion'),
('S09','Reddit','Am I missing something about Stardew Valley?','https://www.reddit.com/r/patientgamers/comments/eyaamp/am_i_missing_something_about_stardew_valley/','可爱画面与经营压力可以并存，日夜和体力机制会使部分玩家焦虑。','review'),
('S10','Steam Community','Tomatoshadow: Silksong review','https://steamcommunity.com/id/tomatoshadow/recommended/1030300/','好评同时提到长跑尸、反复练习、移动操作和探索惊喜；喜欢与代价并不互斥。','review'),
('S11','GOG','Agarest: Generations of War user reviews','https://www.gog.com/en/game/agarest_generations_of_war','可见差评讨论重复战斗升级、材料链和推进节奏；只是该评论观点。','review'),
('S12','GOG Forum','How do you score/rate games?','https://www.gog.com/forum/general/how_do_you_scorerate_games/page1','玩家区分响应速度、清晰度、重复跑腿和个人评分尺度。','discussion'),
('S13','itch.io','Baba Is You Jam Build comments','https://hempuli.itch.io/baba-is-you/comments?after=0','玩家提及逻辑解谜、创意与过难；Jam版反馈不能当正式版证据。','comments'),
('S14','TapTap','二重螺旋玩家评价','https://www.taptap.cn/app/388717/review','同一评价可肯定角色和探索、反感固定副本与随机碎片；免费获得不等于低时间成本。','review'),
('S15','TapTap','幻塔玩家评价','https://www.taptap.cn/app/192675/review','玩家喜欢闲逛景观但批评任务引导与寻路，开放世界不等于易导航。','review'),
('S16','TapTap','洛伊的移动要塞玩家评价','https://www.taptap.cn/app/739892/review','可见更新评价从初期兴奋转为后期资源停滞，必须保留评论修订与进度。','review'),
('S17','TapTap','无名江湖玩家评价','https://www.taptap.cn/app/235106/review','短日常、配队与长期数值、抽取概率可并存；有反讽五星评价，星级不能决定句子含义。','review'),
('S18','Steam Community','Factorio: what should I automate?','https://steamcommunity.com/app/427520/discussions/0/604161119746127162/','讨论将重复手工制作改成生产自动化，区分自动化经营与无操作挂机。','discussion'),
('S19','Reddit','Slay the Spire: give me the real tips','https://www.reddit.com/r/slaythespire/comments/1ja39hd/give_me_the_real_tips/','讨论围绕下一场挑战而选牌，构筑不只指预设最终流派。','discussion'),
('S20','Reddit','Into the Breach: an amazing indie','https://www.reddit.com/r/patientgamers/comments/g9ng51/into_the_breach_an_amazing_indie/','检索发现有人把确定性当优点也当缺少惊喜的代价；仅作待验证扩展线索。','search_lead'),
('S21','Steam Community','Deep Rock Galactic: Worth playing solo?','https://steamcommunity.com/app/548430/discussions/1/3203746811791854833/','单人机器人协助、职业合作、无需语音与路人社交风险分开记录；有人觉得单人无聊，也有人喜欢。','discussion'),
('S22','Steam Community','Subnautica: how scary is the game?','https://steamcommunity.com/app/264710/discussions/0/5154944131320023232/','深水未知引发的紧张与突发惊吓不同；相同环境有人放松、有人恐惧，需保留主观分歧。','discussion'),
('S23','GOG','RimWorld user reviews','https://www.gog.com/en/game/rimworld','玩家从殖民者关系、灾难和模组谈系统涌现叙事；一条评价明确自Steam转载，跨站不能重复计票。','review'),
('S24','Reddit','Alien Isolation: disappointing slog but beautiful','https://www.reddit.com/r/patientgamers/comments/1806zsq/alien_isolation_is_a_disappointing_slogbut_its_so/','同一评论赞美美术、声音与控制，批评躲藏等待和重复；还提到修改弹药，不能泛化到默认难度。','review'),
('P12','Steam','Deep Rock Galactic tags','https://store.steampowered.com/app/548430/Deep_Rock_Galactic/','核对Co-op、PvE、Class-Based、Shooter；Single-player是功能字段。','catalog'),
('P13','Steam','Subnautica tags','https://store.steampowered.com/app/264710/Subnautica/','核对Survival、Crafting、Base Building、Horror；玩家标签含Multiplayer而功能标单人，反映标签不能直接证明功能。','catalog'),
('P01','Steamworks','Steam Tags','https://partner.steamgames.com/doc/store/tags','平台标签用于可见性和召回，不能替代体验证据。','official'),
('P02','Steamworks','IUserReviewsService','https://partner.steamgames.com/doc/webapi/IUserReviewsService','完整review字段；query_summary评分仅在首个all类型请求返回。','official'),
('P03','Epic','Ratings and Polls','https://store.epicgames.com/en-US/news/the-epic-games-store-ratings-and-polls-update','官方说明评分和玩家投票标签；不是自由文本评价。','official'),
('P04','itch.io','Content creator quality guidelines','https://itch.io/docs/creators/quality-guidelines','优先建议标签，避免同义重复；元数据分类与标签不同。','official'),
('P05','Steam','Hades store tags','https://store.steampowered.com/app/1145360/Hades/','核对Action Roguelike、Roguelite、Hack and Slash等标签。','catalog'),
('P06','Steam','Slay the Spire store tags','https://store.steampowered.com/app/646570/Slay_the_Spire/','核对Deckbuilding、Turn-Based、Strategy、Roguelike Deckbuilder等。','catalog'),
('P07','Steam','Hollow Knight store tags','https://store.steampowered.com/app/367520/Hollow_Knight/','核对Souls-like、Metroidvania、Platformer、Exploration等。','catalog'),
('P08','GOG','Hollow Knight store tags','https://www.gog.com/en/game/hollow_knight','核对Action、Difficult、Exploration、Platformer、Souls-like等。','catalog'),
('P09','Epic','Hades genres and features','https://store.epicgames.com/en-US/p/hades','核对Action、Rogue-Lite、RPG；Single Player属于features。','catalog'),
('P10','itch.io','Top tags: genres','https://itch.io/tags/genres','核对Action、Adventure、Role Playing、Visual Novel等分类。','catalog'),
('P11','IGDB','API reference','https://api-docs.igdb.com/','目录的genres、keywords、platforms和商店链接用于召回。','official'),
]
records=[dict(id=i,platform=p,title=t,url=u,observation=o,kind=k,access='search_snippet_only' if k=='search_lead' else 'page_body_available',accessed_at='2026-10-21') for i,p,t,u,o,k in sources]
# Mapping families use only labels observed above. A broad mapping is an OR recall hint, not equivalence.
profiles={
'action':([25,12],['action-rpg','character action'],{'steam':['Action','Action RPG','Hack and Slash'],'gog':['Action'],'epic':['Action','RPG'],'itch':['Action']}),
'souls':([12,25],['soulslike','souls-like'],{'steam':['Souls-like'],'gog':['Souls-like'],'epic':['Action','RPG'],'itch':['Action']}),
'build':([12,15],['build','character customization','deckbuilding'],{'steam':['Action RPG','Deckbuilding'],'gog':['Role-playing','Strategy'],'epic':['RPG'],'itch':['Role Playing']}),
'rogue':([12,15,32],['roguelike','roguelite','rogue-lite'],{'steam':['Action Roguelike','Roguelite'],'gog':[],'epic':['Rogue-Lite'],'itch':[]}),
'deck':([15,16],['deckbuilding','deck-building','deckbuilder'],{'steam':['Deckbuilding','Roguelike Deckbuilder'],'gog':['Strategy','Turn-Based'],'epic':[],'itch':[]}),
'tactics':([15,16,24],['tactical','turn-based'],{'steam':['Turn-Based','Strategy'],'gog':['Tactical','Turn-Based'],'epic':[],'itch':[]}),
'explore':([31,9,8],['exploration','metroidvania'],{'steam':['Exploration','Metroidvania','Adventure'],'gog':['Exploration','Metroidvania','Adventure'],'epic':[],'itch':['Adventure']}),
'platform':([8],['platformer'],{'steam':['Platformer'],'gog':['Platformer'],'epic':['Action'],'itch':['Action']}),
'puzzle':([9],['puzzle','logic'],{'steam':['Strategy'],'gog':[],'epic':[],'itch':[]}),
'narrative':([31,12],['story rich','narrative'],{'steam':['Story Rich'],'gog':['Story Rich','Visual Novel'],'epic':['RPG'],'itch':['Visual Novel','Adventure']}),
'cozy':([13],['life simulation','farming'],{'steam':['Casual'],'gog':[],'epic':[],'itch':[]}),
'factory':([13,15],['automation','resource management'],{'steam':['Strategy'],'gog':['Strategy'],'epic':[],'itch':[]}),
'none':([],[],{'steam':[],'gog':[],'epic':[],'itch':[]}),
}
profiles.update({
'coop':([5],['co-operative','cooperative','co-op'],{'steam':['Co-op','PvE','Class-Based'],'gog':[],'epic':[],'itch':[]}),
'survival':([13,31],['survival','crafting'],{'steam':['Survival','Crafting','Base Building'],'gog':['Survival','Base Building'],'epic':[],'itch':[]}),
'sandbox':([13,15],['sandbox','management'],{'steam':['Sandbox'],'gog':['Sandbox','Management','Simulation'],'epic':[],'itch':[]}),
'horror':([31],['horror','survival horror'],{'steam':['Horror','Survival Horror'],'gog':[],'epic':[],'itch':[]}),
})
rows='''
action_combat|玩法类型|实时动作战斗|需要实时操控角色的攻击或移动，而非仅下回合指令。|动作;即时战斗;action combat|动作类型不等于高难或必须读招；仍需评论说明。|S02,S04|action
cooperative_roles|合作与社交|合作分工|玩家之间以职业、能力或资源互补完成目标。|合作;联机;组队;分工;co-op;cooperative|联网或多人标签不能证明合作深度，竞技另查。|S21|coop
solo_viability|合作与社交|单人游玩可行|独自游玩时仍有可完成的流程与合适的辅助。|单人;独狼;solo;singleplayer|有单人按钮不代表体验等价；必须保留难度与模式。|S21|none
communication_load|合作与社交|沟通与社交负担|完成目标需要多大程度的语音、文字或默契协调。|开麦;语音;社交压力;沟通;voice chat|联机不自动等于强社交；陌生人踢人和配合成本分开。|S21|coop
survival_needs|生存与建造|生存维护|氧气、饥饿或其他环境约束需要持续管理。|生存;氧气;饥饿;survival|生存类型不等于高战斗难度或必须刷材料。|S22,S23|survival
base_building|生存与建造|基地建造|建造、布置和扩张持续使用的据点。|建家;基地;建造;base building|搭建功能不等于自由美观布置；需具体评论。|S22,S23|survival
emergent_stories|经营与资源|系统涌现故事|角色关系与系统事件相互作用形成玩家自己的故事。|涌现;故事生成;殖民者;emergent;story generator|预设剧情丰富不是同一概念；坏结果也可能有趣。|S23|sandbox
mod_flexibility|运行体验|模组扩展|模组提供玩法调整或内容扩展的空间。|模组;mod;modding|模组体验不等于原版，需标明mode。|S23,S24|none
horror_tension|叙事与氛围|持续恐惧与压迫|通过未知、环境或追踪形成持续的紧张感。|恐怖;压迫;害怕;恐惧;horror;dread|是主观体验，不能用平均星级否认某位玩家的恐惧。|S22,S24|horror
jump_scares|叙事与氛围|突发惊吓|突然的画面、声音或事件造成惊吓。|跳吓;突脸;突然吓人;jumpscare;jump scare|持续氛围恐怖与突发惊吓不同，没提到不能当没有。|S22|horror
stealth_waiting|战斗与操作|潜行与躲藏等待|通过隐蔽、观察巡逻或等待时机推进。|潜行;躲柜子;躲藏;stealth;hiding|安静氛围不是潜行；等待时间与紧张感可同时存在。|S24|none

read_patterns|战斗与操作|读招抓时机|观察敌人前摇、攻击节奏，选择反击窗口。|读招;战斗技巧;抓时机;看破招式;learn patterns;timing windows|难或高伤害本身不能证明读招；策略思考不等于反应。|S04,S10|action
precise_dodge|战斗与操作|走位与精确闪避|通过位置、方向或闪避时机规避攻击。|走位;闪避;躲招;dodge;positioning|有闪避键不等于操作精确；不能仅凭类型推断。|S02,S10|action
parry_counter|战斗与操作|弹反与反击|在敌人攻击窗口用格挡或反击改变攻防。|弹反;招架;妖反;parry;deflect;counter|自动格挡与时机反击不同。|S04|action
combo_expression|战斗与操作|连段与招式衔接|通过取消、派生、切换招式表达熟练操作。|连段;连招;派生;残心;取消后摇;combo;cancel|按钮多不必然自由；按键复杂与选择丰富分开。|S04|action
stamina_management|战斗与操作|战斗资源管理|攻击防御与移动之间需要管理体力等资源。|精力管理;体力条;stamina;ki pulse|局外材料刷取与战斗内资源不是一回事。|S04|action
responsive_movement|战斗与操作|移动响应与操控感|输入反馈及时，移动或跳跃能稳定按意图执行。|手感;操作流畅;响应;snappy;responsive|好评里的手感只是主观判断，不能泛化到所有设备。|S10,S12|platform
boss_practice|战斗与操作|练习攻克Boss|通过重复挑战学习对手并获得熟练收益。|Boss;boss;反复挑战;练习;背板|练Boss不等于刷装备；练习次数不是客观难度。|S10|action
combat_readability|战斗与操作|战斗信息清晰|能辨认敌人动作、危险范围和自己的状态。|光污染;看不清;特效;readability;visual clutter|画面精美与战斗可读性可以相反。|S04,S12|none
soulslike|玩法类型|魂类体验|玩家明确描述本作具有类魂的动作挑战体验。|魂类;类魂;魂;Soulslike;souls-like|不继承刷取、惩罚或地图复杂等未提及特征。|S03,S10|souls
build_variety|构筑与成长|构筑改变打法|不同技能、装备或组合导致有意义的操作或策略变化。|构筑;多构筑;流派;配装;不同打法;build variety;buildcraft|不把只换伤害颜色、纯数值更高当作打法多样。|S02,S04,S06,S17|build
synergy|构筑与成长|组件联动|技能、道具、遗物或卡牌之间有可解释的相互作用。|联动;组合技;协同;synergy;combo pieces|物品多或属性相加不自动等于协同。|S06|build
adaptive_builds|构筑与成长|临场构筑|根据本局奖励、敌人和资源改变方案。|临场构筑;随机应变;现抓现组;adapt;improvised build|不等同于预先规划固定毕业配装。|S02,S06,S19|rogue
deckbuilding|构筑与成长|牌组构筑|通过选择、删除或升级卡牌改变牌组结构。|卡牌构筑;牌组构筑;卡组;deckbuilding;deckbuilder|抽角色养卡与对局中构筑牌组不同。|S06,S19|deck
weapon_styles|构筑与成长|武器与角色玩法差异|更换武器、架势或角色会改变可用动作和决策。|武器差异;架势;职业差异;weapon;stance;loadout|外观换皮不能独立证明。|S02,S04|action
meta_progression|构筑与成长|局外成长|一次尝试结束后保留升级或解锁以影响后续尝试。|局外成长;永久升级;meta progression;permanent upgrades|局外成长不等于必须大量刷资源。|S02|rogue
build_balance|构筑与成长|流派平衡与自由|多种打法有可行空间，不被单一强势套路明显挤压。|流派平衡;套路单一;最优解;build balance;overpowered|存在多种组件不能证明每种都有效。|S02|build
learning_curve|学习与挑战|系统学习成本|理解规则、组合和操作需要多大投入。|上手;学习成本;教程;复杂系统;learning curve|熟练后顺手不代表新手容易；脑力与手速分开。|S04,S17|none
configurable_difficulty|学习与挑战|难度辅助选项|有能改变游玩负担的难度或辅助模式。|难度可调;辅助模式;上帝模式;god mode;assist mode|特殊模式的结论必须标明模式。|S02|none
resource_grind|重复与刷取|装备资源刷取|为装备、材料、经验反复重复已掌握内容。|刷装备;刷材料;刷级;肝;grind;farming|重复挑战、重开和新剧情不自动属于刷装备；区分可选和必需。|S01,S03,S11,S14|none
mandatory_grind|重复与刷取|推进依赖刷取|正常推进在某阶段需要重复刷取资源或等级。|必须刷;不刷过不去;卡资源;required grind;mandatory farming|必须有推进依赖证据，不能由存在刷取系统推出。|S03,S11,S14|none
loot_rng|重复与刷取|随机掉落与洗练|获得目标装备或词条依赖概率并可能反复尝试。|随机掉落;洗词条;看脸;drop rate;loot rng;reroll|局面随机和掉落随机不同；低概率需具体描述。|S14,S17|none
repeated_content|重复与刷取|关卡内容重复|场景、敌人、任务或流程反复出现。|重复内容;重复关卡;同一张图;repetitive;same rooms|重复场景不等于构筑不变；主观厌烦不能替代内容描述。|S01,S02,S14|rogue
optional_endgame|重复与刷取|可选后期追求|主线之外的毕业装备、极限挑战或成就目标。|毕业;多周目;后期;endgame;ng+|不能把后期负担回填一周目，也不能反向推断。|S03,S10|none
death_loss|失败与重试|死亡损失|失败带来货币、物品或进度损失及其恢复条件。|死亡惩罚;死亡损失;掉钱;捡尸;death penalty;corpse run|重走距离、重新开局和资源损失分别判断。|S03,S07,S10|none
runback|失败与重试|失败后重走|重试同一挑战前需重复移动或清理的路程。|跑尸;回Boss;重走;runback;walk back|不要把跑图简单直接解释成短跑尸。|S10|none
quick_retry|失败与重试|快速重试|失败后能较快回到核心挑战。|秒重开;快速重试;就近复活;quick retry;checkpoint|死亡没有损失不等于重试方便；未提跑尸不能算快速。|S10|none
run_restart|失败与重试|失败后重开一局|一次失败结束当前运行，需要新的流程尝试。|重开;重新开局;permadeath;restart run|不等于删除全部长期进度。|S01,S02,S07|rogue
navigation|探索与空间|寻路负担|辨认地图、路线、目标位置的认知负担。|跑图;寻路;迷路;导航;navigation;getting lost|地图大不等于难找路；旅途长与认路难不同。|S10,S15|explore
interconnected_world|探索与空间|互联地图|区域之间通过捷径和可回访连接形成空间关系。|箱庭;互联地图;捷径;interconnected;shortcuts|开放世界标签不能证明关卡互联质量。|S10|explore
exploration_discovery|探索与空间|自主探索与发现|偏离明确路线能发现地点、秘密或新信息。|探索;发现秘密;闲逛;exploration;discovery|问号清单数量不能直接等同发现感。|S07,S10,S15|explore
knowledge_progression|探索与空间|知识驱动进展|理解线索或规则本身让玩家能够抵达新目标。|知识驱动;信息解锁;knowledge;learn the rules|知识不是可永久升级的能力值，注意剧透。|S07|puzzle
precision_platforming|探索与空间|精确跳跃与移动挑战|关卡要求控制跳跃、冲刺或连续移动。|跳跳乐;平台跳跃;跑酷;platforming;wall jump|战斗难度与跳跃难度分开。|S10|platform
logical_puzzles|思考与随机|逻辑解谜|通过推理规则或关系求出解法。|解谜;谜题;逻辑;推理;puzzle;logic|搜索隐藏物品与推理不等价；版本需明确。|S13|puzzle
turn_based_tactics|思考与随机|回合与战术决策|行动前可思考，决策涉及位置、顺序或资源。|回合制;战术;策略;turn-based;tactics|不是一切策略游戏都有回合制。|S05,S06,S17|tactics
positioning_tactics|思考与随机|位移与环境解题|利用推拉、地形或站位改变局面。|位移解题;地形;推拉;positioning;terrain|战棋外观不能证明位移机制。|S05|tactics
input_randomness|思考与随机|随机局面下决策|先生成随机局面，再由玩家选择应对。|随机地图;随机奖励;input randomness;procedural|不自动意味着行动结果靠运气。|S05,S06|rogue
outcome_randomness|思考与随机|行动结果依赖概率|作出选择后命中、效果或胜负仍受随机影响。|命中率;看运气;概率判定;output randomness;rng|不要把所有随机机制混为一个随机性分数。|S05,S17|none
risk_reward|思考与随机|风险收益取舍|选择额外收益时需要承担可说明的代价。|风险收益;资源取舍;risk reward;trade-off|单纯高难或奖励多不足以支持。|S02,S06|tactics
narrative_focus|叙事与氛围|剧情与角色驱动|故事、对白或角色关系构成主要吸引力。|剧情;角色;叙事;story;narrative;dialogue|商店Story Rich不能证明用户会喜欢该故事。|S01,S08,S15|narrative
reading_load|叙事与氛围|文字阅读负担|理解体验需要连续阅读大量对白或说明。|大量文字;阅读;长对话;reading;dialogue heavy|对白质量好不等于阅读量适合所有人。|S08|narrative
atmosphere|叙事与氛围|世界氛围与沉浸|场景、声音和叙事共同塑造明确的氛围体验。|氛围;沉浸;世界观;atmosphere;immersive|精美截图不能代替玩家体验依据。|S10,S15|explore
art_style|叙事与氛围|画风与视觉审美|玩家对美术风格和场景表现的偏好。|画风;美术;手绘;像素;art style;visuals|画面偏好不推出玩法偏好；不要用主观审美填事实。|S02,S09,S15|none
time_pressure|节奏与日常|时间与效率压力|计时、日程或资源损耗使玩家感到赶进度。|时间压力;赶时间;日夜;体力限制;time pressure;energy bar|可爱或休闲标签不保证无压力。|S09|none
daily_chores|节奏与日常|日常维护与上线负担|为了进展或收益需重复每日任务或定时上线。|每日任务;打卡;日常;上线;daily chores;daily quest|短日常与低总投入不等同。|S16,S17|none
relaxed_pace|节奏与日常|自主节奏|能按自己的节奏推进，不被明确日程或竞争催促。|轻松;慢节奏;休闲;不赶时间;relaxing;own pace|特定玩家感觉放松不能证明所有玩法无压力。|S09,S17|cozy
idle_progress|节奏与日常|挂机成长|离线或低操作情况下获得持续成长。|放置;挂机;收菜;idle;afk|挂机不代表不氪、无日常或短时间完成。|S16,S17|none
session_flexibility|节奏与日常|分段游玩便利|短时游玩、暂离或回归仍可推进和理解目标。|碎片时间;短局;随时停;短时间;short sessions|单局短不代表可中途保存；需逐项核实。|S08,S17|none
automation|经营与资源|生产自动化|设计生产和物流系统以替代重复手工劳动。|自动化;流水线;生产链;automation;factory|与挂机收菜、自动战斗不同。|S18|factory
resource_planning|经营与资源|资源规划与经营|分配有限资源，在扩张、效率和风险之间选择。|经营;资源管理;规划;management;resource planning|资源稀缺不自动等于有策略深度。|S06,S09,S18|factory
pay_power|付费与体验|付费影响实力|付费影响强度、成长速度或竞争条件。|氪金;逼氪;pay to win;p2w|不付费能玩不代表与付费者处境相同。|S11,S16,S17|none
gacha_dependency|付费与体验|抽取与收集门槛|获得角色、技能或关键组件依赖抽取及保底机制。|抽卡;保底;限定;gacha;pity|付费与时间两种成本独立记录。|S14,S16,S17|none
performance_stability|运行体验|性能与稳定性|卡顿、崩溃或影响推进的错误。|卡顿;崩溃;掉帧;bug;stutter;crash|必须保留平台与补丁日期，不把旧问题自动当现状。|S04,S13,S14|none
roguelite|玩法类型|单局循环与变化|玩家明确描述多次运行与局间或局内变化。|肉鸽;肉鸽类;roguelike;roguelite;rogue-lite|只提重玩不足以证明类型，局外成长另查。|S01,S02,S06|rogue
'''
tags=[]
for line in filter(str.strip, rows.strip().splitlines()):
 id,family,label,definition,aliases,confusions,refs,profile=line.split('|')
 genres,keys,maps=profiles[profile]
 mappings={}
 for platform,labels in maps.items():
  src={'steam':'P05' if profile in ['action','rogue'] else 'P06' if profile in ['deck','tactics','build','puzzle','cozy','factory'] else 'P07','gog':'P08' if profile in ['action','souls','explore','platform','narrative'] else 'S11','epic':'P09','itch':'P10'}[platform]
  if profile in ['coop','survival','sandbox','horror']:
   src={'steam':'P12' if profile=='coop' else 'P13','gog':'S23','epic':'P09','itch':'P10'}[platform]
  exact=(id=='soulslike' and platform in ['steam','gog']) or (id=='deckbuilding' and platform=='steam') or (id=='roguelite' and platform in ['steam','epic'])
  mappings[platform]={'labels':labels,'relation':'exact_label' if exact else 'broad_recall' if labels else 'no_direct_mapping','source_ids':(['P05','P06'] if profile=='build' and platform=='steam' else ['S11'] if profile=='narrative' and platform=='gog' else [src]) if labels else [],'runtime':'igdb_catalog_proxy','note':'只作召回；不证明体验。原生标签未被实时抓取。' if labels else '无已核对直接标签；使用其他正向标签召回并查评论。'}
 tags.append({'id':id,'family':family,'label':label,'definition':definition,'aliases':aliases.split(';'),'confusions':confusions,'research_sources':refs.split(','),'status':'seed_researched','platform_mappings':mappings,'igdb':{'genre_ids':genres,'keywords':keys}})
obj={'version':'player-tags-1','updated_at':'2026-10-21','method':'目的性跨社区质性编码；不是全网词频统计、人口代表性样本或训练后的模型。','tags':tags}
(root/'backend/app/tags/vocabulary.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
(root/'docs/research/sources.json').write_text(json.dumps({'accessed_at':'2026-10-21','sources':records,'limitations':['No Rest for the Wicked Steam讨论遇到内容提示，未把搜索摘要作为完整证据。','部分Reddit帖子无法打开；未计入完整正文。','豆瓣等定向检索未取得可靠正文，未宣称覆盖。','TapTap部分短评可能模板化；只归纳词义与反例，不作为游戏整体口碑。']},ensure_ascii=False,indent=2)+'\n')
lines=['# GameReach 玩家语言标签词典 v1','',f'共 {len(tags)} 个标签。词表与源码 vocabulary.json 同步生成。所有映射用于扩大召回；没有标签不意味着没有体验。','','|分类|标签 ID / 名称|玩家表达（同义线索）|判断边界|调研依据|','|---|---|---|---|---|']
for t in tags:lines.append('|'+ '|'.join([t['family'],t['id']+' / '+t['label'],'、'.join(t['aliases']),t['definition']+' '+t['confusions'],','.join(t['research_sources'])])+'|')
lines+=['','## 平台映射','', '“—”表示尚无已核对直接标签；不能编造一个标签填上。下表多数为宽泛召回，精确标签对应关系见 JSON。运行时统一经 IGDB 的类型/关键词与商店链接召回，未冒称已接通各商店原生搜索。','','|标签|Steam|GOG|Epic|itch.io|','|---|---|---|---|---|']
for t in tags:lines.append('|'+t['label']+'|'+'|'.join(' / '.join(t['platform_mappings'][p]['labels']) or '—' for p in ['steam','gog','epic','itch'])+'|')
lines+=['','## 来源登记','', '|ID|来源与页面|观察（归纳）|访问结果|','|---|---|---|---|']
for s in records:lines.append(f"|{s['id']}|[{s['platform']} · {s['title']}]({s['url']})|{s['observation']}|{s['access']}|")
(root/'docs/research/PLAYER_TAG_DICTIONARY.md').write_text('\n'.join(lines)+'\n')
print(len(tags),'tags',len(records),'source records')
