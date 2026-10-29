import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {RelatedGames} from '../src/components/InterestCards';
describe('Interest card game groups',()=>{
 it('lists named members with the requested related-games label',()=>{
  const html=renderToStaticMarkup(<RelatedGames games={[{key:'a',name:'Authored game A'},{key:'b',name:'Authored game B'},{key:'c',name:'Authored game C'}]}/>);
  expect(html).toContain('相关游戏');expect(html).toContain('3 款');
  expect(html.match(/<li>/g)?.length).toBe(3);expect(html).toContain('Authored game B');
 });
 it('explains that legacy prose has no grouped games without inventing members',()=>{
  const html=renderToStaticMarkup(<RelatedGames/>);
  expect(html).toContain('旧版文字卡片');expect(html).not.toContain('<li>');
 });
});
