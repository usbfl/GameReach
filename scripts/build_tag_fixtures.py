import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
games=json.loads((root/'data/fixtures/catalog.json').read_text())['games']
# Explicit prose and annotations; no conversion from numeric DNA feature values.
profiles={
'fx-01': [('read_patterns','present','敌人的抬手动作能看清，记住出招节奏后再出手，比盲按更有效。'),('build_variety','present','我分别试了格挡反击和远程异常流，两套搭配改变了交战方式。'),('resource_grind','absent','通关需要的装备都在正常流程中拿到了，我没有重复刷装备或材料。'),('quick_retry','present','倒下后就在首领门前重来，重新挑战很快。')],
'fx-03': [('read_patterns','present','我靠观察敌人的攻击前摇和停顿找到反击时机。'),('build_variety','absent','可调整的装备很少，换装备没有形成新的打法。'),('runback','present','每次倒下都要从远处篝火跑回首领房。')],
'fx-07': [('read_patterns','present','逐步认出首领的连招后，我才稳定打出了反击。'),('parry_counter','present','格挡必须按在攻击命中的瞬间，成功后能马上反击。'),('resource_grind','absent','没有刷装备的循环，推进只需要学会战斗。')],
'fx-09': [('build_variety','present','武器插件可以组成持续灼烧或护盾反伤等不同流派。'),('resource_grind','present','我为了升级插件，必须反复刷同一张矿洞地图获取材料。'),('loot_rng','present','想要的武器词条是随机掉落的，十次探索也可能一件都没有。')],
'fx-02': [('turn_based_tactics','present','每回合先看敌人行动，再决定部队如何移动和攻击。'),('build_variety','present','我可以组炮击队，也可以用控制和护盾队，两种编队玩法不同。'),('resource_grind','absent','完成主线即可解锁单位，我没有为材料重复打关卡。')],
'fx-08': [('deckbuilding','present','卡组在探索中逐步组成，每次加入或移除卡牌都会影响后面的战斗。'),('synergy','present','遗物和卡牌能连成触发循环，单张牌的效果不算突出。'),('run_restart','present','失败就需要重新开始这一整趟航行。')],
'fx-18': [('read_patterns','present','学会看敌人的动作再闪避，胜率比提高面板攻击更明显。'),('build_variety','present','技能和武器的组合允许我试近战连击或远程控制。'),('resource_grind','present','中段解锁装备需要反复清几张旧地图，我觉得重复。')],
}
docs=[]
for g in games:
 if g['id'] not in profiles:continue
 # Independent authored examples, deliberately mixed praise/tradeoffs. Always marked synthetic.
 annotations=profiles[g['id']]
 for n in range(3):
  subset=annotations if n==0 else annotations[:3] if n==1 else annotations[:1]
  texts=[x[2] for x in subset]
  if n==1:
   texts=[{'read_patterns':'我反复练习看攻击前摇和出手间隔，熟悉招式后更容易过关。',
           'build_variety':'不同装备搭配能形成不同的战斗流派，我换了一套打法后体验明显变化。' if p=='present' else '几乎没有可更换的构筑方向，配装改变不了战斗方式。',
           'resource_grind':'我没有重复刷关拿装备材料，正常推进就够用了。' if p=='absent' else '为升级装备，我重复打了很多次旧关卡收集资源。'}.get(t,txt) for t,p,txt in subset]
  body='【虚构玩家评价 · 离线功能演示，非真实用户】'+f'样本作者 {g["id"]}-{n+1}。我体验了《{g["name"]}》。'+' '.join(texts)+' 我对画面的偏好可能与你不同，这份感受仅供演示。'
  eid=f'{g["id"]}-tag-review-{n+1}'
  doc={'game_id':g['id'],'provider':'fixture','external_id':eid,'title':g['name']+f' · 完整合成评价 {n+1}',
       'text':body,'kind':'reviews','dataset_id':'synthetic-v1','provenance_type':'synthetic','url':None,
       'published_at':'2026-10-01T00:00:00+00:00','language':'zh-CN','complete':True,'author_key':eid,
       'sampling_stratum':['positive_helpful','negative_helpful','recent'][n],
       'annotations':[{'aspect':'tag','tag_id':t,'tag_polarity':p,'claim_text':text,'quote':text,'scope':{}} for (t,p,_),text in zip(subset,texts)]}
  docs.append(doc)
(root/'data/fixtures/tag_reviews.json').write_text(json.dumps(docs,ensure_ascii=False,indent=2))
