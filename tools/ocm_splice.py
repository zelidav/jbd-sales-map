#!/usr/bin/env python3
"""Splice the OCM-derived doors into index.html and teach the app to handle them.

These doors are real and licensed but nobody measured their sales, so the rule is simple:
never write anything into the fields the app treats as measured. `cat`, `svol30`, `psr`,
`qt` stay absent. Everything estimated lives under `area`, everything scraped lives under
`site`, and both are labelled as such wherever they surface.

The app changes that follow from that rule:

  * `areaVol()` mirrors `catVol()` but reads the estimate, so a category filter can still
    surface a no-data door without pretending its numbers were observed.
  * `isPriority()` refuses them outright. A priority target is a claim about measured
    category dollars; a neighbourhood median is not evidence, and a 🎯 on a guess is the
    exact "confident wrong number" this app is built to avoid.
  * The marker is hollow, so a rep can see at a glance which pins are inferred.

Usage:  python tools/ocm_splice.py
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "_ocm")
IDX = os.path.join(ROOT, "index.html")


def main():
    doors = json.load(io.open(os.path.join(CACHE, "ocm_doors.json"), encoding="utf-8"))
    raw = json.load(io.open(os.path.join(CACHE, "new.json"), encoding="utf-8"))
    byLic = {o.get("license_number"): o for o in raw}

    s = io.open(IDX, encoding="utf-8").read()
    m = re.search(r"var DATA\s*=\s*(\[.*?\]);\s*\n", s, re.S)
    DATA = json.loads(m.group(1))
    have = {(d.get("lic") or "").strip().upper() for d in DATA if d.get("lic")}

    add = []
    for d in doors:
        lic = (d.get("lic") or "").strip().upper()
        if not lic or lic in have:
            continue
        o = byLic.get(d["lic"], {})
        rec = {
            "n": d["n"], "lat": d["lat"], "lng": d["lng"],
            "a": d.get("a", ""), "c": d.get("c", ""), "co": d.get("co", ""),
            "rg": o.get("region") or "", "nb": "",
            "role": "Prospect", "ds": "", "days": None, "rev": None,
            "tier": "", "rep": "", "poc": "", "ph": "",
            "lic": d["lic"], "op": "Active", "opened": d.get("opened", ""),
            "nd": 1, "src": "ocm",
        }
        if d.get("area"):
            rec["area"] = d["area"]
        if d.get("site"):
            rec["site"] = d["site"]
        add.append(rec)

    print("adding %d doors (map had %d, will have %d)"
          % (len(add), len(DATA), len(DATA) + len(add)))
    DATA.extend(add)
    new_line = "var DATA=" + json.dumps(DATA, ensure_ascii=False) + ";\n"
    s = s[:m.start()] + new_line + s[m.end():]

    # ── app logic ────────────────────────────────────────────────────────────
    def sub(old, new, what, marker=None):
        """Apply one edit.

        `marker` must be a string unique to the NEW text. An earlier version keyed
        idempotency off the first line of the replacement, which is often generic enough
        to already exist in the file — so four real edits reported success and were
        silently skipped. An edit that cannot be applied is a hard failure, never a no-op.
        """
        nonlocal s
        if marker and marker in s:
            print("  ok(already applied):", what)
            return
        assert old in s, "NOT FOUND: " + what
        assert s.count(old) == 1, "AMBIGUOUS (%d matches): %s" % (s.count(old), what)
        s = s.replace(old, new, 1)
        print("  ok:", what)

    # areaVol mirrors catVol but over the estimate.
    sub("""var BRANDLIST=(window.BRANDS||[]);""",
        """// A no-data door's neighbourhood read. Deliberately a separate function from
// catVol: nothing here was measured at this address, and the two must never be
// added together or compared as if they were the same kind of number.
function areaVol(d){var a=d&&d.area&&d.area.cat;if(!a)return 0;var t=0;
 for(var c in a){if(!activeCats.size||activeCats.has(c))t+=a[c].v||0;}
 return t;}
function isNoData(d){return !!(d&&d.nd);}
function ndTag(d){return isNoData(d)
 ?'<span class="ndb" title="This door is licensed and open, but no measured sales data covers it. Figures shown are estimated from the nearest measured doors.">NO DATA</span>':'';}
var BRANDLIST=(window.BRANDS||[]);""",
        "areaVol + isNoData + ndTag")

    # Category filter: a no-data door qualifies on its area read, not on nothing.
    sub(""" if(activeCats.size){if(!d.cat)return false;
  var any=false;activeCats.forEach(function(c){if(d.cat[c]&&d.cat[c].v>0)any=true;});
  if(!any)return false;}""",
        """ if(activeCats.size){
  if(isNoData(d)){var aa=d.area&&d.area.cat;if(!aa)return false;
   var anyA=false;activeCats.forEach(function(c){if(aa[c]&&aa[c].v>0)anyA=true;});
   if(!anyA)return false;}
  else{if(!d.cat)return false;
   var any=false;activeCats.forEach(function(c){if(d.cat[c]&&d.cat[c].v>0)any=true;});
   if(!any)return false;}}""",
        "category filter admits no-data doors on the area read")

    sub(" if(minCatVol>0&&catVol(d)<minCatVol)return false;",
        " if(minCatVol>0&&(isNoData(d)?areaVol(d):catVol(d))<minCatVol)return false;",
        "volume floor uses the estimate for no-data doors")

    # Tier and quality filters are statements about measured price data.
    sub(" if(activeTiers.size){var ks=pickCats(d);if(!ks.length)return false;",
        " if(activeTiers.size){if(isNoData(d))return false;var ks=pickCats(d);if(!ks.length)return false;",
        "tier filter excludes no-data doors")
    sub(" if(activeQual.size&&!(d.qt&&activeQual.has(d.qt)))return false;",
        " if(activeQual.size&&(isNoData(d)||!(d.qt&&activeQual.has(d.qt))))return false;",
        "quality filter excludes no-data doors")

    # Never flag an inferred door as a priority target.
    sub("""function isPriority(d){
 if(isCustomer(d.role))return false;      // already ours -- grow it, do not "target" it""",
        """function isPriority(d){
 if(isNoData(d))return false;             // a neighbourhood median is not evidence
 if(isCustomer(d.role))return false;      // already ours -- grow it, do not "target" it""",
        "isPriority refuses inferred doors")

    # Sorting: measured doors first, then inferred, each by their own number.
    sub(" else if(activeCats.size)vis.sort(function(a,b){return catVol(b)-catVol(a);});",
        """ else if(activeCats.size)vis.sort(function(a,b){
   // Measured doors rank above inferred ones at equal dollars: the rep should spend the
   // day on numbers somebody actually observed.
   var an=isNoData(a),bn=isNoData(b);
   if(an!==bn)return an?1:-1;
   return (an?areaVol(b):catVol(b))-(an?areaVol(a):catVol(a));});""",
        "sort keeps measured above inferred")

    # A hollow marker reads as "inferred" at a glance.
    sub("""  var mk=L.marker([d.lat,d.lng],{icon:mkIcon(COLORS[d.role],momSize(d),ring),zIndexOffset:(ring?1000:0)+(d.momr>=8?200:0)}).bindPopup(popup(d));""",
        """  var mk=L.marker([d.lat,d.lng],{icon:isNoData(d)?ndIcon():mkIcon(COLORS[d.role],momSize(d),ring),zIndexOffset:(ring?1000:0)+(d.momr>=8?200:0)}).bindPopup(popup(d));""",
        "hollow marker for inferred doors")

    sub("function mkIcon(c,sz,ring){",
        """function ndIcon(){
 // Hollow, dashed, grey: visibly not one of the measured pins.
 return L.divIcon({className:'',iconSize:[15,15],iconAnchor:[7,7],
  html:'<div style="width:15px;height:15px;border-radius:50%;border:2px dashed #8a8f93;'
      +'background:rgba(255,255,255,.55);box-sizing:border-box"></div>'});}
function mkIcon(c,sz,ring){""",
        "ndIcon")

    # The popup has to say plainly where the numbers came from.
    sub(" s+=catBlock(d);",
        " s+=catBlock(d);\n s+=areaBlock(d);",
        "popup shows the area block")

    sub("function popup(d){",
        """function areaBlock(d){
 if(!isNoData(d))return '';
 var a=d.area,out='<div class="areab"><b>No measured data for this door.</b>';
 if(a&&a.vol){
  out+=' Estimated from the <b>'+a.n+'</b> nearest measured doors (within '+a.mi+' mi): '
     +'about <b>'+k(a.vol)+'</b>/30d for a store in this area';
  if(a.tier)out+=', typical quality tier <b>'+a.tier+'</b>';
  out+='.';
  var ks=[];for(var c in (a.cat||{})){if(!activeCats.size||activeCats.has(c))ks.push(c);}
  ks.sort(function(x,y){return a.cat[y].v-a.cat[x].v;});
  if(ks.length){out+='<br><span class="est">est. by category: '
   +ks.slice(0,4).map(function(c){return cl(c)+' '+k(a.cat[c].v)
     +(a.cat[c].p?' @ $'+a.cat[c].p:'');}).join(' \\u00b7 ')+'</span>';}
  if(a.peers&&a.peers.length)out+='<br><span class="est">neighbours used: '
   +a.peers.map(esc).join(', ')+'</span>';
 } else { out+=' No measured door near enough to estimate from.'; }
 if(d.site&&d.site.url){
  out+='<br><a href="'+(d.site.url.indexOf('http')===0?d.site.url:'https://'+d.site.url)
     +'" target="_blank" rel="noopener">their site</a>';
  if(d.site.sig&&d.site.sig.length)out+=' says: <b>'+d.site.sig.map(esc).join(', ')+'</b>';
 }
 if(d.opened)out+='<br><span class="est">opened '+esc(d.opened)+'</span>';
 return out+'</div>';}
function popup(d){""",
        "areaBlock")

    sub("""function popup(d){var s='<b>'+d.n+'</b> '+decTag(d)""",
        """function popup(d){var s='<b>'+d.n+'</b> '+ndTag(d)+decTag(d)""",
        "popup carries the NO DATA badge")

    sub("""card.innerHTML='<div class="nm">'+d.n+' '+decTag(d)""",
        """card.innerHTML='<div class="nm">'+d.n+' '+ndTag(d)+decTag(d)""",
        "list card carries the NO DATA badge")

    # styles
    sub("</style>",
        """.ndb{display:inline-block;font:700 9px/1.4 ui-monospace,Menlo,monospace;
 letter-spacing:.08em;color:#6b7075;background:#eceff1;border:1px dashed #b0b6bb;
 border-radius:3px;padding:1px 4px;vertical-align:middle;margin-left:3px}
.areab{margin-top:6px;padding:6px 8px;border-left:3px dashed #b0b6bb;background:#f7f8f9;
 font-size:11.5px;line-height:1.5;color:#41484d;border-radius:0 3px 3px 0}
.areab b{color:#22282c}
.areab .est{color:#6b7075;font-size:11px}
</style>""",
        "no-data styles")

    tmp = IDX + ".new"
    io.open(tmp, "w", encoding="utf-8", newline="").write(s)
    os.replace(tmp, IDX)
    print("wrote index.html (%.0f KB)" % (len(s) / 1024))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
