import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import ResearchStrategy from '../src/components/ResearchStrategy';
describe('Budget strategy controls',()=>{
 it('shows balanced as selected and explicitly keeps budget limits',()=>{
  const html=renderToStaticMarkup(<ResearchStrategy value="balanced" onChange={()=>{}}/>);
  expect(html).toContain('aria-pressed="true">均衡精选');
  expect(html).toContain('快速探索');expect(html).toContain('扩大调查');
  expect(html).toContain('预算上限约束');expect(html).toContain('未达目标再逐款递补');
 });
 it('explains quick exploration caps without promising the requested count',()=>{
  const html=renderToStaticMarkup(<ResearchStrategy value="quick" onChange={()=>{}}/>);
  expect(html).toContain('最多150条目录');expect(html).toContain('每款1批');
  expect(html).toContain('未达到推荐数量会说明原因');
 });
});
