import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
const b=await puppeteer.connect({browserURL:'http://127.0.0.1:9222',defaultViewport:null,protocolTimeout:240000});
const pages=await b.pages();
const p=pages.find(x=>/app\.pistildata\.com/.test(x.url()));
let token=null, seen=0;
p.on('request',r=>{const a=r.headers()['authorization'];
  if(/pistildata/.test(r.url())){seen++; if(a&&!token){token=a;}}});
console.log('listening, then nudging the page...');
try{ await p.reload({waitUntil:'domcontentloaded',timeout:120000}); }catch(e){ console.log('reload:',e.message.slice(0,60)); }
for(let i=0;i<40&&!token;i++){ await new Promise(r=>setTimeout(r,1000)); }
console.log('pistil requests seen:',seen,'| token captured:',!!token);
if(token){ fs.writeFileSync('_token.txt',token); console.log('token length:',token.length); }
b.disconnect();
