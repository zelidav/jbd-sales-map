import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
const API='https://api.pistildata.com', RANK='77593c14-e360-409d-8f52-9e9e89fa8385';
const STORE='The Unit powered by Indoor Treez';
async function tok(){
  const b=await puppeteer.connect({browserURL:'http://127.0.0.1:9222',defaultViewport:null,protocolTimeout:240000});
  const p=(await b.pages()).find(x=>/app\.pistildata\.com/.test(x.url()));
  let t=null; p.on('request',r=>{const a=r.headers()['authorization'];if(a&&/pistildata/.test(r.url())&&!t)t=a;});
  try{await p.reload({waitUntil:'domcontentloaded',timeout:120000});}catch(e){}
  for(let i=0;i<40&&!t;i++)await new Promise(r=>setTimeout(r,1000));
  b.disconnect(); return t;
}
const T=await tok();
if(!T){console.log('no token');process.exit(1);}
async function q(dim,range,cmp){
  const r=await fetch(`${API}/api/dashboards/${RANK}/widgets/widget-rankings-table/query`,{
    method:'POST',headers:{authorization:T,'content-type':'application/json'},
    body:JSON.stringify({filters:{'sales_estimates.state':['NY'],'sales_estimates.store_name':[STORE]},
      dateRange:range,compareDateRange:cmp,selectedDimension:dim,
      pagination:{pageNumber:1,pageSize:100},timeZone:'America/New_York'})});
  const j=await r.json();
  return j.data||j.rows||j.results||j;
}
for(const [lbl,range,cmp] of [['30d','last 30 days','from 60 days ago to 31 days ago'],
                              ['90d','last 90 days','from 180 days ago to 91 days ago']]){
  const rows=await q('sales_estimates.category',range,cmp);
  console.log('==',lbl, Array.isArray(rows)?rows.length+' rows':'?');
  if(Array.isArray(rows)) for(const r of rows)
    console.log('  ',r['sales_estimates.category'],'$'+Math.round(r['sales_estimates.sum_sale_dollars']),
      'units',r['sales_estimates.sum_units_sold'],'avg$'+(r['listings.avg_menu_price']||'').toString().slice(0,6));
  fs.writeFileSync('_unit_'+lbl+'.json',JSON.stringify(rows));
}
