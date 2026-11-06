/* Optional local bench fixtures only. Never reads production data or .env. */
'use strict';
const fs=require('node:fs'),path=require('node:path');
const {working}=require('../tests/js/readability_fixture.cjs');
const root=path.resolve(__dirname,'..');
const base=['/api/me','/api/queue','/api/reports','/api/deals','/api/contracts','/api/contracts/config','/api/contracts/next-number','/api/staff','/api/access','/api/prompts','/api/automations','/api/robot/cab','/api/robot/cab/campaigns','/api/gpt','/api/gpt/history'];
for(const n of ['00000000001','00000000002','00000000003']){base.push('/api/card?number='+n,'/api/deals/'+n);}
for(const ext of ['101','102'])base.push('/api/person?person='+ext);
for(const id of ['call-1','call-2','call-3','call-weekly'])base.push('/api/review/'+id);
for(const ext of ['','101','102']){base.push('/api/callbacks'+(ext?'?ext='+ext:''));for(const period of ['today','7','30','month'])for(const section of ['summary','journal'])base.push('/api/'+section+'?period='+period+(ext?'&ext='+ext:''));}
async function main(){const result={};for(const role of ['manager','director']){const api=working(role),records={};for(const url of base){try{records[url]=await api.request(url);}catch(error){/* Unsupported native panels remain explicitly unavailable on the demo. */}}result[role]=records;}
const dest=path.join(root,'tests/fixtures/crm_guide_demo.json');fs.mkdirSync(path.dirname(dest),{recursive:true});fs.writeFileSync(dest,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(Object.fromEntries(Object.entries(result).map(([role,rows])=>[role,Object.keys(rows).length]))));}
main().catch(error=>{console.error(error);process.exitCode=1;});
