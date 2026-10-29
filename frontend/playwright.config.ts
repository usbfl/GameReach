import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'tests',testMatch:'**/*.e2e.ts',timeout:60000,expect:{timeout:20000},fullyParallel:false,workers:1,
 reporter:[['list'],['json',{outputFile:'../artifacts/playwright-results.json'}],['html',{open:'never'}]],
 use:{baseURL:'http://127.0.0.1:8000',trace:'retain-on-failure',screenshot:'only-on-failure'},
 projects:[{name:'desktop',use:{viewport:{width:1440,height:1000}}},{name:'mobile',use:{viewport:{width:390,height:844},isMobile:true,hasTouch:true}}],
 webServer:{command:'cd ../backend && uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log',url:'http://127.0.0.1:8000/healthz',reuseExistingServer:false,timeout:30000,
 env:{DATA_MODE:'fixtures',LLM_MODE:'mock',DATABASE_URL:process.env.GAMEREACH_E2E_DATABASE_URL||'sqlite:///./artifacts/e2e.db'}}
});
