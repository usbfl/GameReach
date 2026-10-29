import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {IntentRows,EditableIntent} from '../src/components/IntentEditor';
const tags=[{id:'build_variety',label:'构筑改变打法'},{id:'resource_grind',label:'装备资源刷取'}];
const base:EditableIntent={tag_id:'build_variety',polarity:'want',priority:3,source_id:'current',source_quote:'丰富构筑',scope:{},explicit_redline:false};
describe('Final condition controls',()=>{
 it('exposes direction, priority and soft-hard selectors for each condition',()=>{
  const html=renderToStaticMarkup(<IntentRows intents={[base]} tags={tags} onChange={()=>{}}/>);
  for(const label of ['玩法标签 1','偏好方向 1','偏好重要度 1','条件类型 1','移除标签 1'])expect(html).toContain(label);
  expect(html).toContain('value="soft" selected=""');
 });
 it('represents an explicit avoid condition as cannot accept',()=>{
  const html=renderToStaticMarkup(<IntentRows intents={[{...base,tag_id:'resource_grind',polarity:'avoid',explicit_redline:true}]} tags={tags} onChange={()=>{}}/>);
  expect(html).toContain('不能接受');expect(html).toContain('value="hard" selected=""');
 });
 it('preserves scoped conditions visibly instead of silently generalizing',()=>{
  const html=renderToStaticMarkup(<IntentRows intents={[{...base,scope:{stage:'endgame'}}]} tags={tags} onChange={()=>{}}/>);
  expect(html).toContain('适用场景：endgame');
 });
});
