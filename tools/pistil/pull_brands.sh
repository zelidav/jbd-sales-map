#!/usr/bin/env bash
# Pull one store_rank export per brand (ctrl_BRAND), so every door can be marked
# as carrying that brand or not. Sequential by necessity: one browser, one filter.
#
#   bash pull_brands.sh brands.txt [DATE_RANGE]
#
# Resumable — a brand whose file already exists and validates is skipped, so a
# killed run can be restarted without repeating ~2 minutes per brand.
set -u
cd "$(dirname "$0")"

LIST="${1:?usage: pull_brands.sh <brands.txt> [date_range]}"
DR="${2:-min:prior-month-1,max:prior-month-1}"
WB=a9dd9a81-12fd-497d-ad27-c19a1cf0c946
SAVED=c847fe85-edc2-4b05-9905-4bcc8809ba7d
DL="$HOME/Downloads"
LOG="pull_brands.log"

# An ignored ctrl_BRAND (Trap 4) returns the FULL dataset: ~640 rows, ~$149M.
# Row count is a poor test — Ayrloom legitimately covers 564 doors — but volume is
# decisive: the largest brand in NY is $6.4M, so anything near $149M is unfiltered.
MAX_VOL=20000000

slug(){ echo "$1" | tr -c 'A-Za-z0-9' '_' | sed 's/__*/_/g;s/^_//;s/_$//'; }

ok(){ # ok <file> -> prints "<rows> doors $<total>" if the file is a valid brand cut
  python -c "
import sys,openpyxl
try:
    wb=openpyxl.load_workbook(sys.argv[1],data_only=True,read_only=True)
    ws=wb[wb.sheetnames[0]]
    rows=[r for r in ws.iter_rows(min_row=2,values_only=True) if r[0] is not None]
    n=len(rows); t=sum(r[4] or 0 for r in rows)
    print(f'{n} doors \${t:,.0f}' if n>0 and t<$MAX_VOL else '')
except Exception: print('')
" "$1" 2>/dev/null
}

total=$(grep -c . "$LIST")
i=0
echo "=== $(date '+%F %T')  $total brands  window=$DR ===" | tee -a "$LOG"

while IFS= read -r brand; do
  # A CRLF brands.txt (Python on Windows writes one by default) leaves a trailing
  # \r that lands inside the control JSON and makes JSON.parse throw in pull.js —
  # every brand fails instantly. Strip it rather than trust the file's endings.
  brand="${brand%$'\r'}"
  [ -z "$brand" ] && continue
  i=$((i+1))
  s=$(slug "$brand")
  f="$DL/PULL_BRAND_$s.xlsx"

  if [ -f "$f" ] && [ -n "$(ok "$f")" ]; then
    echo "[$i/$total] SKIP  $brand  ($(ok "$f"))" | tee -a "$LOG"
    continue
  fi

  # Keep stderr: a swallowed error here cost a full 50-brand run that failed
  # identically on every brand with nothing to diagnose from.
  err=$(node pull.js "BRAND_$s" store_rank "$WB" "$SAVED" \
    "{\"ctrl_STATE_CODE\":\"NY\",\"ctrl_PISTIL_CATEGORY_ID\":\"\",\"ctrl_PISTIL_CLASS_ID\":\"\",\"ctrl_BRAND\":\"$brand\",\"ctrl_DATE_RANGE\":\"$DR\"}" 2>&1)
  rc=$?

  v=$(ok "$f")
  if [ $rc -ne 0 ] || [ -z "$v" ]; then
    echo "[$i/$total] FAIL  $brand  (rc=$rc) $(echo "$err" | tail -1)" | tee -a "$LOG"
    [ -f "$f" ] && [ -z "$v" ] && mv "$f" "$f.rejected"
  else
    echo "[$i/$total] OK    $brand  ($v)" | tee -a "$LOG"
  fi
done < "$LIST"

# Shared account — do not leave a brand filter armed for the next person.
node -e "
const p=require('puppeteer-core');
(async()=>{
  const b=await p.connect({browserURL:'http://127.0.0.1:9222',defaultViewport:null});
  const ck=(await b.cookies()).filter(c=>c.domain.endsWith('pistildata.com'));
  const jar=[...new Map(ck.map(c=>[c.name,c.value])).entries()].map(([k,v])=>k+'='+v).join('; ');
  const raw=(ck.find(c=>c.name==='user_accessToken')||{}).value||'';
  const bearer=Buffer.from(decodeURIComponent(raw),'base64').toString('utf8');
  const h={Cookie:jar,Authorization:'Bearer '+bearer,'app-origin':'insights','Content-Type':'application/json'};
  const u='https://services.pistildata.com/api/v2/Controls/workbook/$WB/saved/$SAVED';
  const cur=await(await fetch(u,{headers:h})).json();
  const ctrls=cur.sigmaControls.map(c=>({sigmaControlId:c.sigmaControlId,values:c.values}));
  for(const c of ctrls) if(['ctrl_BRAND','ctrl_PISTIL_CATEGORY_ID','ctrl_PISTIL_CLASS_ID'].includes(c.sigmaControlId)) c.values='';
  const r=await fetch(u,{method:'PUT',headers:h,body:JSON.stringify({isDefault:false,isAutoSaving:true,savedFilterName:'',sigmaControls:ctrls})});
  console.log('reset shared filter ->',r.status);
  b.disconnect();
})().catch(e=>console.log('reset failed:',e.message));
" 2>&1 | tee -a "$LOG"

echo "=== done $(date '+%F %T') ===" | tee -a "$LOG"
