import fs from 'node:fs';
import { matchNames } from './server/ingest.js';
const SP='C:/Users/zelid/AppData/Local/Temp/claude/C--Users-zelid/f8891681-de67-4843-8903-8132c496800e/scratchpad';
const html=fs.readFileSync('index.html','utf8');
const m=html.match(/var DATA=(\[[\s\S]*?\]);\n/);
const DATA=JSON.parse(m[1]);
const doors=DATA.map(d=>({n:d.n,c:d.c,co:d.co,a:d.a,lic:d.lic}));
const gr=JSON.parse(fs.readFileSync(SP+'/gr_sales.json','utf8'));
const names=gr.unmatched;
console.log(`matching ${names.length} names against ${doors.filter(d=>d.lic).length} licensed doors...`);
const out=await matchNames(names, doors);
const byLic=Object.fromEntries(DATA.filter(d=>d.lic).map(d=>[d.lic,d]));
let hit=0;
for(const n of names){
  const lic=out[n];
  if(lic){hit++; const d=byLic[lic];
    console.log(`  OK   ${n.padEnd(34)} -> ${(d?.n||'?').slice(0,30).padEnd(30)} ${(d?.a||'').slice(0,26).padEnd(26)} ${d?.c||''}`);}
}
console.log('');
for(const n of names) if(!out[n]) console.log(`  --   ${n}`);
console.log(`\nmatched ${hit} / ${names.length}`);
fs.writeFileSync(SP+'/match_result.json', JSON.stringify(out,null,1));
