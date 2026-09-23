#!/usr/bin/env python3
"""Let a rep pick several products to sell, and rank each door by its BEST one.

A rep almost never carries one category. Forcing a single pick made them plan the day
twice and eyeball the overlap. But summing the selected categories is the wrong answer
too: a door doing $50k of flower and nothing else is a better flower call than a door
doing $26k flower and $26k vapes, and the sum says the opposite.

So the rule is max, not sum -- the door surfaces on its strongest selected category, and
the stop says which one. That is how a rep actually decides: "I can sell my flower there."

Run:  python tools/plan_multicat.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(ROOT, "index.html")


def main():
    s = io.open(IDX, encoding="utf-8").read()

    def sub(old, new, what):
        nonlocal s
        assert old in s, "NOT FOUND: " + what
        assert s.count(old) == 1, "AMBIGUOUS (%d): %s" % (s.count(old), what)
        s = s.replace(old, new, 1)
        print("  ok:", what)

    # 1. the control itself
    sub('   <select id="pcat"></select>',
        '   <div id="pcats" class="pcats"></div>',
        "select -> checkbox chips")

    # 2. styles for the chip group
    sub("</style>",
        """.pcats{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}
.pcats .pc{
  display:inline-flex;align-items:center;gap:6px;cursor:pointer;user-select:none;
  border:1px solid #cfd8d1;border-radius:999px;padding:5px 11px 5px 8px;
  font-size:13px;line-height:1;background:#fff;color:#333;
}
.pcats .pc.on{background:#14241a;border-color:#14241a;color:#fff}
.pcats .pc input{width:13px;height:13px;margin:0;accent-color:#1f7a44;pointer-events:none}
.pcats .pc.any{font-style:italic}
</style>""",
        "chip styles")

    # 3. state + value + candidates
    sub("""function planValue(d,cat){
 if(cat)return (d.cat&&d.cat[cat]&&d.cat[cat].v)||0;
 var t=0;for(var c in (d.cat||{}))t+=d.cat[c].v||0;return t;}""",
        """var planCats=new Set();
/* The door's value for this trip, and WHICH product earned it.

   With nothing ticked this is total store volume. With one or more ticked it is the
   single best of them -- never the sum. A rep carrying flower and vapes wants the door
   where one of those moves hardest, not the door that is mediocre at both. */
function planBest(d,cats){
 var best=0,who=null;
 if(!cats||!cats.size){
  var t=0;for(var c in (d.cat||{}))t+=d.cat[c].v||0;
  return {v:t,cat:null};}
 cats.forEach(function(c){
  var v=(d.cat&&d.cat[c]&&d.cat[c].v)||0;
  if(v>best){best=v;who=c;}});
 return {v:best,cat:who};}
function planValue(d,cats){return planBest(d,cats).v;}""",
        "planBest: max over the ticked categories")

    sub("""function planCandidates(cat){
 return visible().filter(function(d){
  if(!d.lat||!d.lng)return false;
  if(!cat)return true;
  return d.cat&&d.cat[cat]&&d.cat[cat].v>0;});}""",
        """function planCandidates(cats){
 return visible().filter(function(d){
  if(!d.lat||!d.lng)return false;
  if(!cats||!cats.size)return true;
  // any one of the ticked products selling here is enough to make the door worth a stop
  var ok=false;cats.forEach(function(c){if(d.cat&&d.cat[c]&&d.cat[c].v>0)ok=true;});
  return ok;});}""",
        "candidates: qualify on any ticked category")

    # 4. build the chips when the panel opens
    sub(""" var sel=document.getElementById('pcat');
 if(!sel.options.length){
  sel.innerHTML='<option value="">Anything — rank by total store volume</option>'
   +CATS_BY_SIZE.map(function(c){return '<option value="'+c+'">'+cl(c)+'</option>';}).join('');
  if(activeCats.size){var first=null;activeCats.forEach(function(c){if(!first)first=c;});sel.value=first;}
 }""",
        """ var box=document.getElementById('pcats');
 if(!box.dataset.built){
  box.dataset.built='1';
  // Seed from whatever the rep already filtered the map to: they have usually said what
  // they sell once already, and asking twice is the kind of admin this app exists to remove.
  activeCats.forEach(function(c){planCats.add(c);});
  CATS_BY_SIZE.forEach(function(c){
   var on=planCats.has(c);
   var el=document.createElement('label');
   el.className='pc'+(on?' on':'');
   el.innerHTML='<input type="checkbox"'+(on?' checked':'')+'><span>'+cl(c)+'</span>';
   el.onclick=function(e){
    e.preventDefault();
    var cb=el.querySelector('input');
    if(planCats.has(c)){planCats.delete(c);cb.checked=false;el.classList.remove('on');}
    else{planCats.add(c);cb.checked=true;el.classList.add('on');}
    paintPlanCount();};
   box.appendChild(el);});
 }""",
        "chips built from the categories, seeded from the map filter")

    # 5. one place that counts the candidates, used by open and by every tick
    sub(""" var n=planCandidates(sel.value).length;
 document.getElementById('plancount').innerHTML='Planning from <b>'+n+'</b> door'+(n===1?'':'s')
  +' currently shown by your filters. Change the filters to widen or narrow that.';
 document.getElementById('planerr').textContent='';""",
        """ paintPlanCount();
 document.getElementById('planerr').textContent='';""",
        "openPlan uses the shared counter")

    sub("""document.getElementById('pcat').onchange=function(){
 var n=planCandidates(this.value).length;
 document.getElementById('plancount').innerHTML='Planning from <b>'+n+'</b> door'+(n===1?'':'s')
  +' currently shown by your filters. Change the filters to widen or narrow that.';};""",
        """function paintPlanCount(){
 var n=planCandidates(planCats).length;
 var what=planCats.size
  ?('ranked by the best of '+Array.from(planCats).map(cl).join(', '))
  :'ranked by total store volume';
 document.getElementById('plancount').innerHTML='Planning from <b>'+n+'</b> door'+(n===1?'':'s')
  +' currently shown by your filters, '+what+'.';}""",
        "shared counter names what it ranks on")

    # 6. the build button reads the set
    sub(" var cat=document.getElementById('pcat').value;",
        " var cat=planCats;",
        "build reads the ticked set")

    tmp = IDX + ".new"
    io.open(tmp, "w", encoding="utf-8", newline="").write(s)
    os.replace(tmp, IDX)
    print("wrote index.html")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
