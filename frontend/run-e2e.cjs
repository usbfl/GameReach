// A portable CJS test entry avoids Node 24 ESM loader stalls in managed runtimes.
const fs=require('node:fs');
const ts=require('typescript');
const {spawnSync}=require('node:child_process');
for(const file of ['playwright.config.ts','tests/outcome.ts',...fs.readdirSync('tests').filter(x=>x.endsWith('.e2e.ts')).map(x=>'tests/'+x)]){
  let source=fs.readFileSync(file,'utf8');
  source=source.replace("from './outcome'","from './outcome.cjs'");
  if(file.includes('config'))source=source.replace("'**/*.e2e.ts'","'**/*.e2e.cjs'");
  fs.writeFileSync(file.replace(/\.ts$/,'.cjs'),ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText);
}
const run=spawnSync(process.execPath,['node_modules/playwright/cli.js','test','--config','playwright.config.cjs',...process.argv.slice(2)],{stdio:'inherit',env:{...process.env,PW_DISABLE_TS_ESM:'1',GAMEREACH_E2E_DATABASE_URL:'sqlite:///'+require('node:path').join(require('node:os').tmpdir(),'gamereach-e2e-'+Date.now()+'.db')}});
for(const file of ['playwright.config.cjs','tests/outcome.cjs',...fs.readdirSync('tests').filter(x=>x.endsWith('.e2e.cjs')).map(x=>'tests/'+x)])fs.unlinkSync(file);
process.exit(run.status??1);
