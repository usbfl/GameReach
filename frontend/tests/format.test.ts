import {describe,it,expect} from 'vitest';
import {value} from '../src/api';
describe('Unknown and non-numeric DNA display',()=>{it('does not conflate missing, false and zero',()=>{expect(value(null)).toBe('未知');expect(value(false)).toBe('不支持');expect(value(0)).toBe('0');expect(value([15,30])).toBe('15–30');});});
