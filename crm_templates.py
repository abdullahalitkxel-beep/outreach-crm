"""HTML templates for the CRM (Lightning-inspired layout). Kept separate so crm.py stays readable."""

BASE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}BSO{% endblock %} · BlacSwanken Outreach</title>
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="/app-icon.png">
<link rel="apple-touch-icon" href="/app-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#1B1F24">
<style>
:root{--brand:#0176d3;--brand-d:#014486;--brand-l:#eef4ff;--bg:#f3f3f3;--card:#fff;--line:#dddbda;--line2:#e5e5e5;
--ink:#181818;--muted:#706e6b;--ok:#2e844a;--ok-l:#cdefc4;--err:#ba0517;--err-l:#feded8;--warn:#dd7a01;--warn-l:#fef1ee;
--r:4px;--sh:0 2px 2px rgba(0,0,0,.1)}
*{box-sizing:border-box}
body{margin:0;font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);
background:linear-gradient(#c7dcf3 0,#e3edf9 120px,var(--bg) 260px) no-repeat,var(--bg);min-height:100vh}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
h1,h2,h3{margin:0;font-weight:700}
/* global header */
.gh{display:flex;align-items:center;gap:12px;height:50px;padding:0 12px;background:#fff;border-bottom:1px solid var(--line)}
.logo{width:32px;height:32px;border-radius:8px;background:var(--brand);color:#fff;display:grid;place-items:center;font-weight:800;font-size:16px;flex:none}
.gsearch{flex:1;max-width:560px;margin:0 auto;position:relative}
.gsearch input{width:100%;height:32px;border:1px solid #c9c9c9;border-radius:var(--r);padding:0 10px 0 32px;font:inherit;background:#fff}
.gsearch svg{position:absolute;left:10px;top:9px;fill:var(--muted)}
.gh .tools{display:flex;gap:6px;align-items:center}
.iconbtn{width:32px;height:32px;border-radius:50%;border:1px solid var(--line);background:#fff;display:grid;place-items:center;cursor:pointer;color:var(--muted);font-weight:700;position:relative}
.menu{position:absolute;right:0;top:38px;background:#fff;border:1px solid var(--line);border-radius:var(--r);box-shadow:0 2px 8px rgba(0,0,0,.16);min-width:220px;z-index:30;display:none;text-align:left}
.menu a,.menu button{display:block;width:100%;padding:9px 14px;color:var(--ink);background:none;border:0;font:inherit;text-align:left;cursor:pointer}
.menu a:hover,.menu button:hover{background:var(--bg);text-decoration:none}
.open .menu{display:block}
/* app nav */
.nav{display:flex;align-items:stretch;background:#fff;border-bottom:3px solid var(--brand);box-shadow:var(--sh);padding:0 12px;overflow-x:auto;white-space:nowrap}
.nav .app{display:flex;align-items:center;gap:8px;font-size:18px;font-weight:400;padding-right:18px;margin-right:6px;border-right:1px solid var(--line2)}
.nav .app svg{fill:var(--muted)}
.nav a.tab{display:flex;align-items:center;gap:6px;padding:0 14px;height:40px;color:var(--ink);border-bottom:3px solid transparent;margin-bottom:-3px}
.nav a.tab:hover{background:var(--bg);text-decoration:none}
.nav a.tab.on{border-bottom-color:var(--brand-d);font-weight:700}
.pill{display:inline-block;min-width:18px;padding:0 6px;border-radius:9px;background:var(--err);color:#fff;font-size:11px;line-height:18px;text-align:center;font-weight:700}
.testbar{background:var(--warn);color:#fff;text-align:center;padding:5px 12px;font-weight:600}
.testbar a{color:#fff;text-decoration:underline}
main{padding:12px;max-width:1440px;margin:0 auto}
/* page header */
.ph{background:#fff;border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);padding:12px 16px;margin-bottom:12px}
.ph-top{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.ph .ttl{flex:1;min-width:200px}.ph .ttl small{display:block;color:var(--muted);font-size:12px}
.ph .ttl h1{font-size:18px;line-height:1.3}
.ph .acts{display:flex;gap:6px;flex-wrap:wrap}
.tile{width:32px;height:32px;border-radius:4px;display:grid;place-items:center;color:#fff;font-weight:700;flex:none}
.t-lead{background:#f88962}.t-acc{background:#7f8de1}.t-opp{background:#fcb95b}.t-task{background:#3ba755}.t-rep{background:#2ecbbe}
.t-rev{background:var(--brand)}.t-home{background:#ff538a}.t-setup{background:#9050e9}.t-search{background:#706e6b}
.hl{display:flex;gap:28px;flex-wrap:wrap;margin-top:12px;padding-top:10px;border-top:1px solid var(--line2)}
.hl div small{display:block;color:var(--muted);font-size:12px}
/* cards & layout */
.card{background:#fff;border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);margin-bottom:12px}
.card-h{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--line2)}
.card-h h2{font-size:14px;flex:1}.card-h .tile{width:24px;height:24px;font-size:12px}
.card-b{padding:12px 14px}.card-f{padding:8px 14px;border-top:1px solid var(--line2);text-align:center}
.cols{display:grid;grid-template-columns:2fr 1fr;gap:12px}.cols3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:900px){.cols,.cols3{grid-template-columns:1fr}}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:12px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);padding:12px 14px}
.kpi small{color:var(--muted);display:block}.kpi b{font-size:22px;font-weight:300}
/* buttons */
.btn{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 14px;border-radius:var(--r);border:1px solid #c9c9c9;background:#fff;color:var(--brand);font:inherit;cursor:pointer;white-space:nowrap}
.btn:hover{background:var(--bg);text-decoration:none}
.btn.brand{background:var(--brand);border-color:var(--brand);color:#fff}.btn.brand:hover{background:var(--brand-d)}
.btn.danger{background:var(--err);border-color:var(--err);color:#fff}
.btn.sm{height:26px;padding:0 10px;font-size:12px}
.btn:disabled{opacity:.5;cursor:not-allowed}
/* forms */
label{display:block;font-size:12px;color:var(--muted);margin:10px 0 2px}
input,select,textarea{width:100%;padding:6px 10px;border:1px solid #c9c9c9;border-radius:var(--r);font:inherit;background:#fff;color:var(--ink)}
input:focus,select:focus,textarea:focus{outline:0;border-color:var(--brand);box-shadow:0 0 3px var(--brand)}
textarea{min-height:80px}.inline{display:flex;gap:8px;align-items:end;flex-wrap:wrap}.inline>*{flex:1;min-width:120px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}@media(max-width:600px){.grid2{grid-template-columns:1fr}}
.ro{padding:6px 0;border-bottom:1px solid var(--line2);min-height:32px}
/* tables */
.tbl{width:100%;border-collapse:collapse}
.tbl th{background:#fafaf9;color:var(--muted);font-size:12px;font-weight:600;text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl th a{color:var(--muted)}
.tbl td{padding:7px 10px;border-bottom:1px solid var(--line2);vertical-align:top}
.tbl tr:hover td{background:#f9fbfe}
.scroll{overflow-x:auto}
/* badges */
.badge{display:inline-block;padding:0 8px;border-radius:12px;font-size:11px;line-height:20px;background:#ecebea;color:var(--ink);white-space:nowrap}
.b-ok{background:var(--ok-l);color:#194e31}.b-err{background:var(--err-l);color:#8e030f}.b-warn{background:#fff0d1;color:#8c4b02}.b-info{background:var(--brand-l);color:var(--brand-d)}
.fit{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;font-size:11px;font-weight:700;color:#fff}
.fit-A{background:var(--ok)}.fit-B{background:#fe9339}.fit-C{background:#a0a0a0}
/* path */
.pathbox{background:#fff;border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);padding:10px 12px;margin-bottom:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.path{display:flex;flex:1;min-width:280px;overflow-x:auto}
.path .st{flex:1;min-width:92px;height:32px;display:flex;align-items:center;justify-content:center;border:0;margin:0 -4px 0 0;
background:#ecebea;color:var(--ink);font:inherit;font-size:12px;cursor:pointer;padding:0 16px;
clip-path:polygon(0 0,calc(100% - 10px) 0,100% 50%,calc(100% - 10px) 100%,0 100%,10px 50%)}
.path .st:first-child{clip-path:polygon(0 0,calc(100% - 10px) 0,100% 50%,calc(100% - 10px) 100%,0 100%);border-radius:16px 0 0 16px}
.path .st:last-child{clip-path:polygon(0 0,100% 0,100% 100%,0 100%,10px 50%);border-radius:0 16px 16px 0}
.path .st:hover{background:#dddbda;text-decoration:none}
.path .done{background:#3ba755;color:#fff}.path .done:hover{background:#2e844a}
.path .cur{background:var(--brand-d);color:#fff;font-weight:700}.path .cur:hover{background:var(--brand-d)}
.path .won{background:#3ba755;color:#fff;font-weight:700}.path .lost{background:var(--err);color:#fff;font-weight:700}
/* kanban */
.kb{display:flex;gap:10px;overflow-x:auto;padding-bottom:8px;align-items:flex-start}
.kcol{flex:0 0 250px;background:#f3f2f2;border:1px solid var(--line);border-radius:var(--r)}
.kcol h3{font-size:12px;padding:8px 10px;border-bottom:1px solid var(--line);background:#fafaf9;display:flex;justify-content:space-between}
.kcol h3 small{font-weight:400;color:var(--muted)}
.kcards{padding:8px;max-height:70vh;overflow-y:auto;min-height:60px}
.kcard{display:block;background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:8px 10px;margin-bottom:8px;color:var(--ink);box-shadow:var(--sh)}
.kcard:hover{border-color:var(--brand);text-decoration:none}.kcard small{color:var(--muted);display:block}
/* timeline */
.tl{list-style:none;margin:0;padding:0}
.tl li{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line2)}
.tl .ic{width:24px;height:24px;border-radius:4px;flex:none;display:grid;place-items:center;color:#fff;font-size:11px;font-weight:700}
.tl .ic.email{background:#939393}.tl .ic.note{background:#e6d478}.tl .ic.call{background:#48c3cc}.tl .ic.meeting{background:#eb7092}
.tl .ic.task{background:#4bc076}.tl .ic.sys{background:#a094ed}
.tl .tx{flex:1;min-width:0}.tl small{color:var(--muted);display:block}.tl pre{white-space:pre-wrap;font:inherit;margin:4px 0 0}
/* tabs */
.tabs{display:flex;border-bottom:1px solid var(--line);margin:-12px -14px 12px;padding:0 14px}
.tabs button{border:0;background:none;padding:10px 12px;font:inherit;cursor:pointer;color:var(--ink);border-bottom:3px solid transparent}
.tabs button.on{border-bottom-color:var(--brand);font-weight:700}
.tabpane{display:none}.tabpane.on{display:block}
/* help + toast */
.help{display:flex;gap:10px;background:var(--brand-l);border:1px solid #b0c7e6;border-radius:var(--r);padding:10px 14px;margin-bottom:12px}
.help b{color:var(--brand-d)}.help .x{margin-left:auto;background:none;border:0;cursor:pointer;color:var(--muted);font-size:16px;line-height:1}
.help ol,.help ul{margin:4px 0 0;padding-left:18px}
.toast{background:#2e844a;color:#fff;border-radius:var(--r);padding:10px 14px;margin-bottom:12px;box-shadow:var(--sh)}
.toast.err{background:var(--err)}
.empty{text-align:center;padding:28px 16px;color:var(--muted)}
.empty b{display:block;color:var(--ink);font-size:15px;margin-bottom:4px}
.muted{color:var(--muted)}.right{text-align:right}.nowrap{white-space:nowrap}
.bar{height:16px;background:var(--brand);border-radius:2px;min-width:2px}
.bar.alt{background:#3ba755}.bar.warn{background:#fe9339}
/* setup */
.step{display:flex;gap:12px;padding:14px;border-bottom:1px solid var(--line2)}
.step .n{width:28px;height:28px;border-radius:50%;flex:none;display:grid;place-items:center;font-weight:700;border:2px solid var(--line);color:var(--muted)}
.step.ok .n{background:var(--ok);border-color:var(--ok);color:#fff}
.step.next{background:#f9fbfe}.step.next .n{border-color:var(--brand);color:var(--brand)}
.step .bd{flex:1}.step h3{font-size:14px}.step p{margin:4px 0 8px;color:#3e3e3c}
code{background:#f3f2f2;border:1px solid var(--line2);border-radius:3px;padding:0 4px;font-size:12px}
.progress{height:8px;background:#ecebea;border-radius:4px;overflow:hidden}.progress div{height:100%;background:var(--ok)}
pre.log{background:#16325c;color:#e6eef8;padding:12px;border-radius:var(--r);max-height:420px;overflow:auto;white-space:pre-wrap;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace}

/* settings */
.settings{display:grid;grid-template-columns:230px 1fr;gap:12px}@media(max-width:800px){.settings{grid-template-columns:1fr}}
.snav{background:#fff;border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);padding:6px 0;align-self:start}
.snav a{display:flex;gap:10px;align-items:center;padding:9px 14px;color:var(--ink);border-left:3px solid transparent}
.snav a:hover{background:var(--bg);text-decoration:none}.snav a.on{border-left-color:var(--brand);background:var(--brand-l);font-weight:700}
.sn{width:22px;height:22px;border-radius:50%;background:#ecebea;display:grid;place-items:center;font-size:11px;font-weight:700;color:var(--muted)}
.snav a.on .sn{background:var(--brand);color:#fff}
.ibox{border:1px solid var(--line);border-radius:var(--r);padding:4px 14px 14px;margin:0 0 14px}
.ibox legend{font-weight:700;padding:0 6px}.ibox[data-optional]{background:#fcfcfc}
.hint{color:var(--muted);font-size:12px;margin:6px 0 0}
details.adv{margin-top:8px}details.adv summary{cursor:pointer;color:var(--brand)}
details.variant{border-top:1px solid var(--line2);padding:8px 0}details.variant summary{cursor:pointer;font-weight:600;padding:2px 0}details.variant[open] summary{margin-bottom:4px}
.checks{display:flex;flex-wrap:wrap;gap:4px 16px}.checks label{display:flex;gap:6px;align-items:center;color:var(--ink);margin:4px 0;font-size:13px}.checks input{width:auto}
.switch{display:flex!important;gap:10px;align-items:center;color:var(--ink)!important;font-size:13px!important;margin:14px 0 2px!important;cursor:pointer}
.switch input{display:none}.switch span{width:36px;height:20px;border-radius:10px;background:#c9c9c9;position:relative;flex:none;transition:.2s}
.switch span:after{content:"";position:absolute;width:16px;height:16px;border-radius:50%;background:#fff;top:2px;left:2px;transition:.2s}
.switch input:checked+span{background:var(--brand)}.switch input:checked+span:after{left:18px}
.legal h1{font-size:22px;margin-bottom:4px}.legal h2{font-size:16px;margin:22px 0 6px}.legal p,.legal li{line-height:1.6}
/* tour */
#tour-dim{position:fixed;inset:0;z-index:90;display:none}
#tour-spot{position:fixed;z-index:91;border-radius:6px;box-shadow:0 0 0 9999px rgba(8,7,7,.55);transition:all .25s;pointer-events:none}
#tour-pop{position:fixed;z-index:92;width:320px;max-width:calc(100vw - 24px);background:#fff;border-radius:var(--r);box-shadow:0 4px 16px rgba(0,0,0,.3);padding:16px}
#tour-pop h3{font-size:15px;margin-bottom:6px}#tour-pop p{margin:0 0 12px;color:#3e3e3c}
#tour-pop .ft{display:flex;align-items:center;gap:6px}#tour-pop .ft span{flex:1;color:var(--muted);font-size:12px}
@media(max-width:700px){.hl{gap:14px}.gsearch input{font-size:16px}}
{{ logo_css|safe }}
</style></head><body>
{% if test_redirect %}<div class="testbar">TEST MODE: every email goes to {{ test_redirect }}, never to leads. <a href="{{ url_for('setup') }}#golive">How to go live</a></div>
{% elif test_mode_broken %}<div class="testbar">TEST MODE is on but has no test address, so the bot won't send anything. <a href="{{ url_for('setup') }}">Fix in Getting Started</a></div>{% endif %}
<div class="gh">
 <span style="flex:none;display:flex">{{ logo(32, tile=True, uid="hd", weight="b", label="BSO")|safe }}</span>
 {% if request.endpoint != 'welcome' %}<form class="gsearch" action="{{ url_for('search') }}" id="t-search">
  <svg width="14" height="14" viewBox="0 0 24 24"><path d="M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 9.5 16a6.5 6.5 0 0 0 4.2-1.6l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z"/></svg>
  <input name="q" placeholder="Search leads, prospects, emails, comments, campaigns…" value="{{ request.args.get('q','') if request.endpoint=='search' else '' }}">
 </form>{% else %}<div style="flex:1"></div>{% endif %}
 <div class="tools">
  <div class="iconbtn" id="helpbtn" title="Help" onclick="this.classList.toggle('open')">?
   <div class="menu">
    <button onclick="startTour(true)">Take the guided tour</button>
    <a href="{{ url_for('setup') }}">Getting started checklist</a>
    <button onclick="resetHelp()">Show all help tips again</button>
    <a href="{{ url_for('settings') }}">Settings</a>
    <a href="{{ url_for('about') }}">About &amp; legal</a>
    {% if team_user %}<div class="muted" style="padding:6px 12px">Signed in as {{ team_name or team_user }}{{ ' (bot, stays signed in)' if team_bot }}</div><form method="post" action="{{ url_for('signout') }}"><button>Sign out</button></form>{% endif %}
    <form method="post" action="{{ url_for('quit_app') }}" onsubmit="return confirm('Stop BSO? Automatic runs stop until you open it again.')"><button>Quit the app</button></form>
   </div>
  </div>
 </div>
</div>
{% if request.endpoint not in ('welcome', 'signin') %}<nav class="nav">
 <span class="app"><svg width="18" height="18" viewBox="0 0 24 24"><path d="M4 4h4v4H4zm6 0h4v4h-4zm6 0h4v4h-4zM4 10h4v4H4zm6 0h4v4h-4zm6 0h4v4h-4zM4 16h4v4H4zm6 0h4v4h-4zm6 0h4v4h-4z"/></svg>BlacSwanken Outreach</span>
 {% for ep, label, extra in nav_items %}
 <a class="tab {{ 'on' if active == ep }}" id="nav-{{ ep }}" href="{{ url_for(ep) }}">{{ label }}{% if extra %} <span class="pill">{{ extra }}</span>{% endif %}</a>
 {% endfor %}
</nav>{% endif %}
<main>
{% if request.args.get('msg') %}<div class="toast {{ 'err' if request.args.get('err') }}">{{ request.args.get('msg') }}</div>{% endif %}
{% block body %}{% endblock %}
</main>
<div id="tour-dim"><div id="tour-spot"></div><div id="tour-pop"><h3></h3><p></p><div class="ft"><span></span>
<button class="btn sm" id="tour-skip">Skip</button><button class="btn sm" id="tour-back">Back</button><button class="btn brand sm" id="tour-next">Next</button></div></div></div>
<script>
// ---- tell the app this window is open; it quits about a minute after the last window is closed (unless auto-run is on)
function beat(){ try{ fetch('/heartbeat',{method:'POST',keepalive:true}); }catch(e){} }
beat(); setInterval(beat, 10000);
document.addEventListener('visibilitychange', function(){ if(!document.hidden) beat(); });
window.addEventListener('focus', beat);
window.addEventListener('pageshow', beat);
window.addEventListener('pagehide', function(){ try{ navigator.sendBeacon('/bye'); }catch(e){} });
// ---- dismissible help boxes
function ls(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v)}catch(e){return null}}
document.querySelectorAll('.help[data-id]').forEach(function(h){
  if(ls('help-'+h.dataset.id)==='hidden') h.style.display='none';
  var x=h.querySelector('.x'); if(x) x.onclick=function(){h.style.display='none';ls('help-'+h.dataset.id,'hidden')};
});
function resetHelp(){try{Object.keys(localStorage).forEach(function(k){if(k.indexOf('help-')===0)localStorage.removeItem(k)})}catch(e){} location.reload()}
document.addEventListener('click',function(e){var hb=document.getElementById('helpbtn'); if(hb && !hb.contains(e.target)) hb.classList.remove('open')});
// ---- tabs inside cards
document.querySelectorAll('[data-tabs]').forEach(function(box){
  var btns=box.querySelectorAll('.tabs button');
  btns.forEach(function(b){b.onclick=function(){btns.forEach(function(o){o.classList.remove('on')});b.classList.add('on');
    box.querySelectorAll('.tabpane').forEach(function(p){p.classList.toggle('on',p.id===b.dataset.t)})}});
});
// ---- bulk select
var all=document.getElementById('selall'); if(all) all.onchange=function(){document.querySelectorAll('input[name=ids]').forEach(function(c){c.checked=all.checked})};
// ---- guided tour
var TOUR=[
 {sel:null,t:'Welcome to BSO',p:'This 60-second tour shows where everything lives. You can replay it any time from the ? menu at the top right.'},
 {sel:'#nav-setup',t:'Start here: Getting Started',p:'A checklist that walks you through setup, with a button for every step. It ticks itself off as you go.'},
 {sel:'#nav-settings',t:'Settings',p:'Connect your inbox, paste your keys, edit your emails and targeting. Simple forms with a Test button. No files to edit.'},
 {sel:'#nav-home',t:'Home',p:'Your daily dashboard: tasks due, hot replies, emails waiting for review, and recent activity.'},
 {sel:'#nav-leads',t:'Leads',p:'Everyone you are prospecting. Filter, sort and bulk-update them. Each lead has an A/B/C fit grade based on their job title.'},
 {sel:'#nav-review',t:'Review',p:'Nothing is emailed until you approve it here. Read each draft, edit it, ask for a new AI opener, or reject the lead.'},
 {sel:'#nav-opportunities',t:'Opportunities',p:'When a lead is interested, click Convert on their page. That creates an Account and an Opportunity you can move through the deal stages.'},
 {sel:'#nav-tasks',t:'Tasks',p:'Your to-do list. The bot adds a task automatically whenever someone replies with interest.'},
 {sel:'#nav-reports',t:'Reports',p:'Funnel, reply rates per email step, lead source performance and your pipeline forecast.'},
 {sel:'#t-search',t:'Search everything',p:'Find any lead, account or opportunity by name, email or company.'},
 {sel:null,t:"You're all set",p:'Open Getting Started next. It takes you from zero to your first test email in about 10 minutes.',go:'{{ url_for("setup") }}'}
];
var ti=0;
function placeTour(){
  var s=TOUR[ti],dim=document.getElementById('tour-dim'),spot=document.getElementById('tour-spot'),pop=document.getElementById('tour-pop');
  pop.querySelector('h3').textContent=s.t; pop.querySelector('p').textContent=s.p;
  pop.querySelector('.ft span').textContent=(ti+1)+' of '+TOUR.length;
  document.getElementById('tour-back').style.display=ti?'':'none';
  document.getElementById('tour-next').textContent=ti===TOUR.length-1?(s.go?'Open Getting Started':'Done'):'Next';
  var el=s.sel&&document.querySelector(s.sel);
  if(el&&el.offsetParent!==null){
    el.scrollIntoView({block:'nearest',inline:'center'});
    var r=el.getBoundingClientRect();
    spot.style.cssText='left:'+(r.left-4)+'px;top:'+(r.top-4)+'px;width:'+(r.width+8)+'px;height:'+(r.height+8)+'px';
    var top=r.bottom+12; if(top+180>innerHeight) top=Math.max(12,r.top-190);
    pop.style.left=Math.min(Math.max(12,r.left),innerWidth-pop.offsetWidth-12)+'px'; pop.style.top=top+'px';
  }else{
    spot.style.cssText='left:50%;top:40%;width:0;height:0';
    pop.style.left=Math.max(12,(innerWidth-pop.offsetWidth)/2)+'px'; pop.style.top=(innerHeight*0.3)+'px';
  }
}
function startTour(force){ if(!force&&ls('tour-done'))return; ti=0; document.getElementById('tour-dim').style.display='block'; placeTour(); }
function endTour(){ document.getElementById('tour-dim').style.display='none'; ls('tour-done','1'); }
document.getElementById('tour-next').onclick=function(){ if(ti===TOUR.length-1){endTour(); if(TOUR[ti].go) location.href=TOUR[ti].go; return;} ti++; placeTour(); };
document.getElementById('tour-back').onclick=function(){ if(ti>0){ti--;placeTour();} };
document.getElementById('tour-skip').onclick=endTour;
addEventListener('resize',function(){ if(document.getElementById('tour-dim').style.display==='block') placeTour(); });
addEventListener('load',function(){ if(location.pathname.indexOf('/welcome')!==0 && location.pathname.indexOf('/legal')!==0) startTour(false); });
</script>
{% block scripts %}{% endblock %}
</body></html>"""

MACROS = r"""
{% macro header(tile, letter, label, title) %}
<div class="ph"><div class="ph-top">
 <div class="tile {{ tile }}">{{ letter }}</div>
 <div class="ttl"><small>{{ label }}</small><h1>{{ title }}</h1></div>
 <div class="acts">{{ caller() if caller }}</div>
</div>{{ varargs[0] if varargs }}</div>
{% endmacro %}

{% macro help(id, title) %}
<div class="help" data-id="{{ id }}"><div style="font-size:16px">💡</div><div><b>{{ title }}</b>{{ caller() }}</div><button class="x" title="Hide this tip">×</button></div>
{% endmacro %}

{% macro path(open_stages, current, url, closed_label, closed_cls, next_stage) %}
<div class="pathbox">
 <form class="path" method="post" action="{{ url }}">
  {% set ns = namespace(done=true) %}
  {% for s in open_stages %}
   {% if s == current %}{% set ns.done = false %}{% set cls = 'cur' %}{% elif ns.done %}{% set cls = 'done' %}{% else %}{% set cls = '' %}{% endif %}
   <button class="st {{ cls }}" name="stage" value="{{ s }}" title="Set stage: {{ s|title }}">{{ '✓ ' if cls == 'done' }}{{ s|title }}</button>
  {% endfor %}
  <a class="st {{ closed_cls }}" href="#close" title="Close this record">{{ closed_label }}</a>
 </form>
 {% if next_stage %}<form method="post" action="{{ url }}"><button class="btn brand" name="stage" value="{{ next_stage }}">✓ Mark stage as complete</button></form>{% endif %}
</div>
{% endmacro %}

{% macro status_badge(status, labels) %}
{% set cls = {'replied':'b-ok','active':'b-info','bounced':'b-err','unsubscribed':'b-err','not_interested':'b-err','paused':'b-warn'}.get(status, '') %}
<span class="badge {{ cls }}">{{ labels.get(status, status) }}</span>
{% endmacro %}

{% macro stage_badge(stage) %}
{% set cls = {'replied':'b-ok','qualified':'b-ok','converted':'b-ok','unqualified':'b-err','Closed Won':'b-ok','Closed Lost':'b-err'}.get(stage, 'b-info') %}
<span class="badge {{ cls }}">{{ stage|title }}</span>
{% endmacro %}

{% macro fit(g) %}{% if g %}<span class="fit fit-{{ g }}" title="Fit grade {{ g }}: {{ {'A':'technical decision maker','B':'executive or founder','C':'outside your target roles'}[g] }}">{{ g }}</span>{% endif %}{% endmacro %}
"""

HOME = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Home{% endblock %}{% block body %}
{% call m.header('t-home', '⌂', 'Home', 'Good to see you') %}
 <a class="btn" href="{{ url_for('setup') }}">Getting started ({{ setup_done }}/{{ setup_total }})</a>
 <form method="post" action="{{ url_for('job_start') }}" style="display:inline"><input type="hidden" name="cmd" value="run"><button class="btn brand">▶ Run bot now</button></form>
{% endcall %}
{% if setup_done < setup_total %}
{% call m.help('home-setup', 'New here?') %} You've finished {{ setup_done }} of {{ setup_total }} setup steps. <a href="{{ url_for('setup') }}">Continue Getting Started →</a>{% endcall %}
{% endif %}
<div class="kpis">
 <div class="kpi"><small>Leads</small><b>{{ k.leads }}</b></div>
 <div class="kpi"><small>Contacted</small><b>{{ k.contacted }}</b></div>
 <div class="kpi"><small>Reply rate</small><b>{{ k.reply_rate }}</b></div>
 <div class="kpi"><small>Open opportunities</small><b>{{ k.open_opps }}</b></div>
 <div class="kpi"><small>Open pipeline</small><b>{{ money(k.pipeline) }}</b></div>
 <div class="kpi"><small>Closed won</small><b>{{ money(k.won) }}</b></div>
</div>
<div class="cols">
<div>
 <div class="card"><div class="card-h"><div class="tile t-task">✓</div><h2>My tasks</h2><a href="{{ url_for('tasks') }}">View all</a></div>
  {% if tasks %}<table class="tbl">{% for t in tasks %}<tr>
   <td style="width:30px"><form method="post" action="{{ url_for('task_toggle', task_id=t.id) }}"><input type="checkbox" onchange="this.form.submit()" title="Mark done"></form></td>
   <td>{{ t.subject }}{% if t.lead_name %}<br><a class="muted" href="{{ url_for('lead', lead_id=t.lead_id) }}">{{ t.lead_name }}</a>{% endif %}</td>
   <td class="nowrap right {{ 'b-err badge' if t.due_date < today }}">{{ 'Overdue · ' if t.due_date < today }}{{ t.due_date }}</td></tr>{% endfor %}</table>
  {% else %}<div class="empty"><b>No open tasks</b>When someone replies with interest, a follow-up task appears here automatically.</div>{% endif %}
 </div>
 <div class="card"><div class="card-h"><div class="tile t-lead">L</div><h2>Hot leads: replied and waiting on you</h2></div>
  {% if hot %}<table class="tbl">{% for l in hot %}<tr><td>{{ m.fit(l.fit) }}</td><td><a href="{{ url_for('lead', lead_id=l.id) }}"><b>{{ l.name }}</b></a><br><span class="muted">{{ l.title }}{{ ' · ' if l.title }}{{ l.company }}</span></td><td class="right"><a class="btn sm" href="{{ url_for('lead', lead_id=l.id) }}#close">Convert</a></td></tr>{% endfor %}</table>
  {% else %}<div class="empty"><b>No replies yet</b>Replies are detected automatically each time the bot runs.</div>{% endif %}
 </div>
</div>
<div>
 <div class="card"><div class="card-h"><div class="tile t-rev">✉</div><h2>Waiting for your review</h2></div>
  <div class="card-b" style="text-align:center"><div style="font-size:32px;font-weight:300">{{ review_count }}</div><div class="muted">draft email(s)</div>
  <a class="btn brand" style="margin-top:8px" href="{{ url_for('review') }}">Open review queue</a></div></div>
 <div class="card"><div class="card-h"><div class="tile t-setup">⚙</div><h2>Last bot run</h2><a href="{{ url_for('jobs') }}">History</a></div>
  <div class="card-b">{% if last_job %}<b>{{ job_labels.get(last_job.cmd, last_job.cmd) }}</b> · {{ m.status_badge(last_job.status, {'running':'Running…','ok':'Finished','failed':'Failed'}) }}<br><span class="muted">{{ last_job.started_at[:16].replace('T',' ') }} UTC</span>
   <br><a href="{{ url_for('job', job_id=last_job.id) }}">See output</a>{% else %}<span class="muted">The bot hasn't run from here yet.</span>{% endif %}</div></div>
 <div class="card"><div class="card-h"><h2>Recent activity</h2></div>
  <ul class="tl" style="padding:0 14px">{% for e in activity %}<li><div class="ic {{ e.icon }}">{{ e.letter }}</div><div class="tx"><a href="{{ url_for('lead', lead_id=e.lead_id) }}">{{ e.name }}</a> · {{ e.text }}<br><small>{{ e.at[:16].replace('T',' ') }} UTC</small></div></li>
  {% else %}<li class="muted">Nothing yet.</li>{% endfor %}</ul></div>
</div></div>
{% endblock %}"""

LEADS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Leads{% endblock %}{% block body %}
{% call m.header('t-lead', 'L', 'Leads', view_name) %}
 <a class="btn {{ 'brand' if view != 'kanban' }}" href="{{ qs(view='list') }}">☰ List</a>
 <a class="btn {{ 'brand' if view == 'kanban' }}" href="{{ qs(view='kanban') }}">▥ Kanban</a>
 <a class="btn" href="{{ url_for('new_lead') }}">New</a>
 <a class="btn" href="{{ url_for('import_page') }}">Import</a>
 <form method="post" action="{{ url_for('job_start') }}" style="display:inline"><input type="hidden" name="cmd" value="apollo-pull"><button class="btn" title="Uses Apollo credits">Find leads in Apollo</button></form>
{% endcall %}
{% call m.help('leads', 'How this page works') %}
<ul><li>Each lead moves through <b>New → Contacted → Replied → Qualified</b>, then is closed as <b>Converted</b> or <b>Unqualified</b>.</li>
<li>The <b>Fit</b> grade comes from the job title: <span class="fit fit-A">A</span> technical decision maker, <span class="fit fit-B">B</span> executive or founder, <span class="fit fit-C">C</span> outside your target roles.</li>
<li>Tick several leads to change their stage or pause their emails in one go.</li></ul>{% endcall %}
<div class="card"><div class="card-b">
<form class="inline" method="get">
 <input type="hidden" name="view" value="{{ view }}">
 <div><label>Search</label><input name="q" value="{{ request.args.get('q','') }}" placeholder="Name, email, company"></div>
 <div><label>Stage</label><select name="stage"><option value="">All stages</option>{% for s in stages %}<option value="{{ s }}" {{ 'selected' if request.args.get('stage')==s }}>{{ s|title }}</option>{% endfor %}</select></div>
 <div><label>Email status</label><select name="status"><option value="">Any</option>{% for k,v in status_labels.items() %}<option value="{{ k }}" {{ 'selected' if request.args.get('status')==k }}>{{ v }}</option>{% endfor %}</select></div>
 <div><label>Fit</label><select name="fit"><option value="">Any</option>{% for g in 'ABC' %}<option {{ 'selected' if request.args.get('fit')==g }}>{{ g }}</option>{% endfor %}</select></div>
 <div><label>Source</label><select name="source"><option value="">Any</option>{% for s in sources %}<option {{ 'selected' if request.args.get('source')==s }}>{{ s }}</option>{% endfor %}</select></div>
 <div style="flex:0"><button class="btn brand">Filter</button></div>
</form></div></div>
{% if view == 'kanban' %}
<div class="kb">{% for s in stages %}<div class="kcol"><h3>{{ s|title }} <small>{{ cols[s]|length }}</small></h3><div class="kcards">
 {% for l in cols[s] %}<a class="kcard" href="{{ url_for('lead', lead_id=l.id) }}">{{ m.fit(l.fit) }} <b>{{ l.name }}</b><small>{{ l.title }}</small><small>{{ l.company }}</small>{{ m.status_badge(l.status, status_labels) }}</a>
 {% else %}<div class="muted" style="text-align:center;padding:8px">None</div>{% endfor %}</div></div>{% endfor %}</div>
{% else %}
<form method="post" action="{{ url_for('leads_bulk') }}" class="card">
 <div class="card-h"><h2>{{ rows|length }} lead(s){% if rows|length == 500 %} (first 500){% endif %}</h2>
  <select name="action" style="width:auto"><option value="">Bulk action…</option>
   {% for s in stages if s not in ('converted',) %}<option value="stage:{{ s }}">Change stage to {{ s|title }}</option>{% endfor %}
   <option value="pause">Pause emails</option><option value="resume">Resume emails</option><option value="suppress">Do not contact</option><option value="delete">Delete permanently</option></select>
  <button class="btn" onclick="return this.form.action.value ? confirm('Apply to the selected leads?') : false">Apply</button></div>
 {% if rows %}<div class="scroll"><table class="tbl"><thead><tr><th><input type="checkbox" id="selall" style="width:auto"></th>
  {% for key, label in [('name','Name'),('fit','Fit'),('title','Title'),('company','Company'),('stage','Stage'),('status','Emails'),('source','Source'),('created','Created')] %}
  <th><a href="{{ qs(sort=key, dir=('asc' if sort != key or dir == 'desc' else 'desc')) }}">{{ label }}{{ (' ▲' if dir=='asc' else ' ▼') if sort == key }}</a></th>{% endfor %}</tr></thead>
 <tbody>{% for l in rows %}<tr><td><input type="checkbox" name="ids" value="{{ l.id }}" style="width:auto"></td>
  <td><a href="{{ url_for('lead', lead_id=l.id) }}"><b>{{ l.name }}</b></a><br><span class="muted">{{ l.email }}</span></td>
  <td>{{ m.fit(l.fit) }}</td><td>{{ l.title }}</td><td>{{ l.company }}</td><td>{{ m.stage_badge(l.stage) }}</td>
  <td>{{ m.status_badge(l.status, status_labels) }}</td><td class="muted">{{ l.source }}</td><td class="nowrap muted">{{ l.created_at[:10] }}</td></tr>{% endfor %}</tbody></table></div>
 {% else %}<div class="empty"><b>No leads match</b>Import a spreadsheet, add one by hand, or use <i>Find leads in Apollo</i>.<br><br><a class="btn brand" href="{{ url_for('import_page') }}">Import leads</a>
 <button class="btn" formaction="{{ url_for('sample_load') }}" formmethod="post">Add sample leads</button></div>{% endif %}
</form>
{% endif %}
{% endblock %}"""

TIMELINE = r"""
<ul class="tl">{% for e in timeline %}<li><div class="ic {{ e.icon }}">{{ e.letter }}</div><div class="tx"><b>{{ e.title }}</b>{% if e.body %}<pre>{{ e.body }}</pre>{% endif %}<small>{{ e.at[:16].replace('T',' ') }} UTC</small></div></li>
{% else %}<li class="muted">No activity yet.</li>{% endfor %}</ul>"""

COMPOSER = r"""
<div class="card" data-tabs><div class="card-b">
 <div class="tabs"><button type="button" class="on" data-t="c-note">Note</button><button type="button" data-t="c-call">Log a call</button><button type="button" data-t="c-meet">Meeting</button><button type="button" data-t="c-task">New task</button></div>
 <form class="tabpane on" id="c-note" method="post" action="{{ url_for('add_note', lead_id=lead_id) }}"><input type="hidden" name="kind" value="note"><input type="hidden" name="next" value="{{ request.path }}">
  <textarea name="body" placeholder="Write a note…" required></textarea><button class="btn brand" style="margin-top:8px">Save note</button></form>
 <form class="tabpane" id="c-call" method="post" action="{{ url_for('add_note', lead_id=lead_id) }}"><input type="hidden" name="kind" value="call"><input type="hidden" name="next" value="{{ request.path }}">
  <textarea name="body" placeholder="What was discussed? What's next?" required></textarea><button class="btn brand" style="margin-top:8px">Log call</button></form>
 <form class="tabpane" id="c-meet" method="post" action="{{ url_for('add_note', lead_id=lead_id) }}"><input type="hidden" name="kind" value="meeting"><input type="hidden" name="next" value="{{ request.path }}">
  <textarea name="body" placeholder="Meeting notes, attendees, outcomes…" required></textarea><button class="btn brand" style="margin-top:8px">Log meeting</button></form>
 <form class="tabpane" id="c-task" method="post" action="{{ url_for('add_task_route', lead_id=lead_id) }}"><input type="hidden" name="next" value="{{ request.path }}">{% if opp_id %}<input type="hidden" name="opp_id" value="{{ opp_id }}">{% endif %}
  <label>Subject</label><input name="subject" placeholder="e.g. Send case study" required>
  <label>Due date</label><input type="date" name="due" value="{{ today }}"><button class="btn brand" style="margin-top:8px">Create task</button></form>
</div></div>
{% if open_tasks %}<div class="card"><div class="card-h"><div class="tile t-task">✓</div><h2>Open tasks</h2></div><table class="tbl">
{% for t in open_tasks %}<tr><td style="width:30px"><form method="post" action="{{ url_for('task_toggle', task_id=t.id) }}"><input type="hidden" name="next" value="{{ request.path }}"><input type="checkbox" style="width:auto" onchange="this.form.submit()"></form></td><td>{{ t.subject }}</td><td class="right nowrap {{ 'b-err badge' if t.due_date < today }}">{{ t.due_date }}</td></tr>{% endfor %}</table></div>{% endif %}"""

LEAD = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}{{ l.name }}{% endblock %}{% block body %}
{% set hl %}<div class="hl">
 <div><small>Title</small>{{ x.title or '—' }}</div><div><small>Company</small>{% if account %}<a href="{{ url_for('account', account_id=account.id) }}">{{ l.company }}</a>{% else %}{{ l.company or '—' }}{% endif %}</div>
 <div><small>Email</small>{{ l.email }}</div><div><small>Fit</small>{{ m.fit(x.fit) or '—' }}</div>
 <div><small>Emails</small>{{ m.status_badge(l.status, status_labels) }}{% if l.status == 'active' %} <span class="muted">step {{ l.step + 1 }}/{{ n_steps }}</span>{% endif %}</div>
 <div><small>Source</small>{{ x.source or 'Manual' }}</div></div>{% endset %}
{% call m.header('t-lead', 'L', 'Lead', l.name, hl) %}
 {% if l.stage not in ('converted','unqualified') %}<a class="btn brand" href="#close">Convert</a>{% endif %}
 <form method="post" action="{{ url_for('sequence_action', lead_id=l.id) }}" style="display:inline">
 {% if l.status == 'active' %}<button class="btn" name="action" value="pause">Pause emails</button>{% endif %}
 {% if l.status in ('paused','done') and l.step < n_steps %}<button class="btn" name="action" value="resume">Resume emails</button>{% endif %}
 {% if l.status not in ('unsubscribed','bounced') %}<button class="btn" name="action" value="suppress" onclick="return confirm('Never email this person again?')">Do not contact</button>{% endif %}
 </form>
{% endcall %}
{{ m.path(lead_open, l.stage, url_for('lead_stage', lead_id=l.id), closed_label, closed_cls, next_stage) }}
{% if l.handed_from %}
<div class="card" id="convo"><div class="card-h"><div class="tile t-opp">★</div><h2>Conversation with {{ l.first_name or l.email }}</h2>
 <span class="muted" style="margin-left:6px">handed over by {{ l.handed_from }}</span><span style="flex:1"></span>
 <form method="post" action="{{ url_for('prospect_stage', lead_id=l.id) }}" style="margin:0"><input type="hidden" name="back" value="{{ url_for('lead', lead_id=l.id) }}"><select name="stage" onchange="this.form.submit()">{% for k, lbl in prospect_labels.items() %}<option value="{{ k }}" {{ 'selected' if (l.prospect_stage or 'new') == k }}>{{ lbl }}</option>{% endfor %}</select></form>
 {% if mailbox_ok %}<form method="post" action="{{ url_for('mailbox_check') }}" style="margin:0 0 0 6px"><input type="hidden" name="back" value="{{ url_for('lead', lead_id=l.id) }}"><button class="btn sm">↻ Check mailbox</button></form>{% endif %}</div>
 <div class="card-b">
 {% if not mailbox_ok %}<div class="help" style="margin-top:0">Connect your mailbox in <a href="{{ url_for('settings', tab='handover') }}#mailbox">Settings → Handover</a> to see and send this prospect's emails here.</div>{% endif %}
 {% for msg in convo %}<div style="border:1px solid var(--line);border-left:4px solid {{ '#2e844a' if msg.direction == 'in' else '#0176d3' }};border-radius:6px;padding:10px 12px;margin-bottom:10px;{{ 'background:#f3fbf5' if msg.id in unseen_ids }}">
  <div style="display:flex;gap:8px;flex-wrap:wrap"><b>{{ 'From ' ~ (l.first_name or l.email) if msg.direction == 'in' else msg.frm }}</b>{% if msg.id in unseen_ids %}<span class="badge b-ok">new</span>{% endif %}<span class="muted">{{ msg.at[:16].replace('T', ' ') }}{% if msg.via == 'bso' %} · sent from BSO{% endif %}</span></div>
  <div class="muted" style="font-size:12px">{{ msg.subject }}{% if msg.cc %} · CC {{ msg.cc }}{% endif %}</div>
  <div style="white-space:pre-wrap;margin-top:6px">{{ msg.body }}</div></div>
 {% else %}<p class="muted">{{ 'No emails with this prospect in your mailbox yet. The reply that led to the handover is in the timeline below.' if mailbox_ok else '' }}</p>{% endfor %}
<div id="comments" style="margin:6px 0 14px;padding:10px 12px;background:#f6f8fb;border-radius:6px"><b>Team comments</b> <span class="muted">(only your team sees these; mention someone with @ and their first name)</span>
 {% for c in comments %}<div style="border-left:3px solid {{ '#2e844a' if c.id in new_comment_ids else '#0176d3' }};padding:4px 10px;margin-top:8px"><b>{{ c.author_name }}</b> <span class="muted">{{ c.at[:16].replace('T', ' ') }}</span>{% if c.id in new_comment_ids %} <span class="badge b-ok">new</span>{% endif %}<div style="white-space:pre-wrap">{{ c.text }}</div></div>{% endfor %}
 {% if on_team %}<form method="post" action="{{ url_for('comment_add') }}" style="margin-top:8px"><input type="hidden" name="lead_id" value="{{ l.id }}"><input type="hidden" name="back" value="{{ url_for('lead', lead_id=l.id) }}#comments">
 <textarea name="text" style="min-height:60px" placeholder="e.g. @Omar they asked for a 20% discount, OK?" required></textarea>{% if people %}<div class="muted" style="font-size:12px">{% for p in people %}@{{ p.name.split()[0] }}{{ ', ' if not loop.last }}{% endfor %}</div>{% endif %}<button class="btn sm" style="margin-top:4px">Add comment</button></form>{% endif %}</div>
  {% if mailbox_ok %}<form method="post" action="{{ url_for('prospect_reply', lead_id=l.id) }}" onsubmit="this.querySelector('button').disabled=true">
  <label>Reply to {{ l.email }} <span class="muted">(from your mailbox, in the same thread)</span></label>
  <textarea name="body" style="min-height:140px" required placeholder="Hi {{ l.first_name or '' }},">{{ request.args.get('draft', '') }}</textarea>
  <div style="display:flex;gap:8px;align-items:end;margin-top:8px"><div style="flex:1"><label>CC (optional)</label><input name="cc" placeholder="colleague@company.com"></div><button class="btn brand">Send reply</button></div></form>{% endif %}
 </div></div>
{% endif %}
{% if pending_draft %}{% call m.help('lead-draft-'~pending_draft.id, 'A draft email is waiting for your review.') %} <a href="{{ url_for('review') }}#d{{ pending_draft.id }}">Review it now →</a>{% endcall %}{% endif %}
<div class="cols"><div>
 <div class="card"><div class="card-h"><h2>Campaign</h2></div><div class="card-b"><form method="post" action="{{ url_for('lead_campaign', lead_id=l.id) }}" style="display:flex;gap:8px;align-items:end">
  <div style="flex:1"><select name="campaign_id">{% for cp in all_campaigns %}<option value="{{ cp.id }}" {{ 'selected' if (l.campaign_id or 1) == cp.id }}>{{ cp.name }}{{ ' (paused)' if cp.status != 'active' }}</option>{% endfor %}</select></div>
  <button class="btn">Move</button> <a class="btn" href="{{ url_for('campaign', cid=l.campaign_id or 1) }}">Open campaign</a></form></div></div>
 <div class="card"><div class="card-h"><h2>Details</h2></div><div class="card-b">
 <form method="post" action="{{ url_for('update_lead', lead_id=l.id) }}"><div class="grid2">
  <div><label>First name</label><input name="first_name" value="{{ l.first_name or '' }}"></div>
  <div><label>Last name</label><input name="x_last_name" value="{{ x.last_name or '' }}"></div>
  <div><label>Title</label><input name="x_title" value="{{ x.title or '' }}"></div>
  <div><label>Company</label><input name="company" value="{{ l.company or '' }}"></div>
  <div><label>Website</label><input name="website" value="{{ l.website or '' }}"></div>
  <div><label>Email</label><div class="ro">{{ l.email }}</div></div>
  <div><label>LinkedIn</label><input name="x_linkedin" value="{{ x.linkedin or '' }}"></div>
  <div><label>Country</label><input name="x_country" value="{{ x.country or '' }}"></div>
  {% for k, v in x.items() if k not in ('last_name','title','linkedin','country','fit','source','apollo_id') %}
  <div><label>{{ k.replace('_',' ')|capitalize }}</label><input name="x_{{ k }}" value="{{ v }}"></div>{% endfor %}
 </div><button class="btn brand" style="margin-top:12px">Save</button></form></div></div>
 <div class="card"><div class="card-h"><div class="tile t-rev">✉</div><h2>Emails ({{ emails|length }})</h2></div>
  {% if emails %}<table class="tbl">{% for e in emails %}<tr><td>{{ 'First email' if e.step == 0 else 'Follow-up ' ~ e.step }}{% if e.variant %}<br><small class="muted">{{ e.variant }}</small>{% endif %}</td><td>{{ e.subject }}</td><td>{{ m.status_badge(e.status, {'pending':'Awaiting review','approved':'Approved','sent':'Sent','rejected':'Rejected','cancelled':'Cancelled'}) }}</td><td class="muted nowrap">{{ (e.sent_at or e.created_at)[:10] }}</td></tr>{% endfor %}</table>
  {% else %}<div class="empty">No emails yet. The bot drafts the first one on its next run.</div>{% endif %}
  {% if l.personal_line %}<div class="card-b muted"><b>AI opener:</b> {{ l.personal_line }}</div>{% endif %}</div>
 {% if opps %}<div class="card"><div class="card-h"><div class="tile t-opp">O</div><h2>Opportunities</h2></div><table class="tbl">{% for o in opps %}<tr><td><a href="{{ url_for('opp', opp_id=o.id) }}">{{ o.name }}</a></td><td>{{ m.stage_badge(o.stage) }}</td><td class="right">{{ money(o.amount) }}</td></tr>{% endfor %}</table></div>{% endif %}
 {% if l.stage not in ('converted','unqualified') %}
 <div class="card" id="close"><div class="card-h"><h2>Close this lead</h2></div><div class="card-b">
  <form method="post" action="{{ url_for('convert_lead', lead_id=l.id) }}">
  <p class="muted" style="margin-top:0"><b>Convert</b> creates (or links) an Account for the company and a new Opportunity, and stops the email sequence.</p>
  <div class="grid2">
   <div><label>Account name</label><input name="account" value="{{ l.company or '' }}" required></div>
   <div><label>Opportunity name</label><input name="opp_name" value="{{ (l.company or 'New') ~ ' – engineering team' }}" required></div>
   <div><label>Amount ({{ currency }})</label><input name="amount" type="number" step="100" value="0"></div>
   <div><label>Close date</label><input name="close_date" type="date" value="{{ default_close }}"></div>
   <div><label>Stage</label><select name="stage">{% for s in opp_open %}<option>{{ s }}</option>{% endfor %}</select></div>
   <div><label>Next step</label><input name="next_step" placeholder="e.g. Discovery call Thursday"></div>
  </div><button class="btn brand" style="margin-top:12px">Convert</button></form>
  <form method="post" action="{{ url_for('lead_stage', lead_id=l.id) }}" style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line2)">
   <span class="muted">Not a fit?</span> <button class="btn" name="stage" value="unqualified">Mark as unqualified</button></form>
 </div></div>{% endif %}
 <div class="card"><div class="card-h"><h2>Delete this lead</h2></div><div class="card-b">
  <form method="post" action="{{ url_for('delete_lead', lead_id=l.id) }}" onsubmit="return confirm('Permanently delete {{ l.name }} and all their emails, notes and history?')">
  <p class="muted" style="margin-top:0">Erases the contact and their entire history, e.g. when they ask you to delete their data.</p>
  <label style="display:flex;gap:6px;align-items:center;color:var(--ink)"><input type="checkbox" name="keep_blocked" value="1" checked style="width:auto"> Keep their address on the do-not-contact list so they're never emailed or imported again</label>
  <button class="btn danger" style="margin-top:10px">Delete permanently</button></form></div></div>
</div><div>
 {% with lead_id=l.id, opp_id=None %}""" + COMPOSER + r"""{% endwith %}
 <div class="card"><div class="card-h"><h2>Activity</h2></div><div class="card-b">""" + TIMELINE + r"""</div></div>
</div></div>
{% endblock %}"""

ACCOUNTS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Accounts{% endblock %}{% block body %}
{% call m.header('t-acc', 'A', 'Accounts', 'All accounts') %}{% endcall %}
{% call m.help('accounts', 'What are accounts?') %} An account is a company. It's created when you <b>convert</b> a lead, and groups that company's contacts and opportunities together.{% endcall %}
<div class="card">{% if rows %}<div class="scroll"><table class="tbl"><thead><tr><th>Account</th><th>Website</th><th>Industry</th><th>Contacts</th><th>Open opps</th><th class="right">Pipeline</th><th class="right">Won</th></tr></thead>
<tbody>{% for a in rows %}<tr><td><a href="{{ url_for('account', account_id=a.id) }}"><b>{{ a.name }}</b></a></td><td>{{ a.domain or '' }}</td><td>{{ a.industry or '' }}</td><td>{{ a.contacts }}</td><td>{{ a.open_opps }}</td><td class="right">{{ money(a.pipeline) }}</td><td class="right">{{ money(a.won) }}</td></tr>{% endfor %}</tbody></table></div>
{% else %}<div class="empty"><b>No accounts yet</b>Open a lead who replied and click <i>Convert</i>.</div>{% endif %}</div>
{% endblock %}"""

ACCOUNT = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}{{ a.name }}{% endblock %}{% block body %}
{% set hl %}<div class="hl"><div><small>Website</small>{{ a.domain or '—' }}</div><div><small>Industry</small>{{ a.industry or '—' }}</div><div><small>Country</small>{{ a.country or '—' }}</div><div><small>Open pipeline</small>{{ money(pipeline) }}</div></div>{% endset %}
{% call m.header('t-acc', 'A', 'Account', a.name, hl) %}{% endcall %}
<div class="cols"><div>
 <div class="card"><div class="card-h"><div class="tile t-opp">O</div><h2>Opportunities ({{ opps|length }})</h2></div>
 {% if opps %}<table class="tbl"><thead><tr><th>Name</th><th>Stage</th><th>Close date</th><th class="right">Amount</th></tr></thead>{% for o in opps %}<tr><td><a href="{{ url_for('opp', opp_id=o.id) }}">{{ o.name }}</a></td><td>{{ m.stage_badge(o.stage) }}</td><td>{{ o.close_date or '' }}</td><td class="right">{{ money(o.amount) }}</td></tr>{% endfor %}</table>
 {% else %}<div class="empty">No opportunities.</div>{% endif %}</div>
 <div class="card"><div class="card-h"><div class="tile t-lead">C</div><h2>Contacts ({{ contacts|length }})</h2></div>
 <table class="tbl">{% for c in contacts %}<tr><td><a href="{{ url_for('lead', lead_id=c.id) }}">{{ c.name }}</a></td><td>{{ c.title }}</td><td>{{ c.email }}</td></tr>{% endfor %}</table></div>
</div><div>
 <div class="card"><div class="card-h"><h2>Details</h2></div><div class="card-b"><form method="post" action="{{ url_for('update_account', account_id=a.id) }}">
  <label>Name</label><input name="name" value="{{ a.name }}"><label>Website</label><input name="domain" value="{{ a.domain or '' }}">
  <label>Industry</label><input name="industry" value="{{ a.industry or '' }}"><label>Country</label><input name="country" value="{{ a.country or '' }}">
  <button class="btn brand" style="margin-top:12px">Save</button></form></div></div>
</div></div>
{% endblock %}"""

OPPS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Opportunities{% endblock %}{% block body %}
{% call m.header('t-opp', 'O', 'Opportunities', 'Pipeline') %}
 <a class="btn {{ 'brand' if view != 'list' }}" href="{{ url_for('opportunities', view='kanban') }}">▥ Kanban</a>
 <a class="btn {{ 'brand' if view == 'list' }}" href="{{ url_for('opportunities', view='list') }}">☰ List</a>
{% endcall %}
{% call m.help('opps', 'How deals work') %} Opportunities are created when you <b>convert</b> a lead. Move each deal through <b>Discovery → Proposal → Negotiation</b> and close it as <b>Won</b> or <b>Lost</b>. Each stage has a win probability that feeds the forecast in Reports.{% endcall %}
<div class="kpis"><div class="kpi"><small>Open deals</small><b>{{ k.open }}</b></div><div class="kpi"><small>Open pipeline</small><b>{{ money(k.pipeline) }}</b></div>
<div class="kpi"><small>Weighted forecast</small><b>{{ money(k.weighted) }}</b></div><div class="kpi"><small>Closed won</small><b>{{ money(k.won) }}</b></div></div>
{% if view == 'list' %}<div class="card">{% if rows %}<div class="scroll"><table class="tbl"><thead><tr><th>Opportunity</th><th>Account</th><th>Stage</th><th>Probability</th><th>Close date</th><th class="right">Amount</th></tr></thead>
{% for o in rows %}<tr><td><a href="{{ url_for('opp', opp_id=o.id) }}"><b>{{ o.name }}</b></a></td><td>{% if o.account_id %}<a href="{{ url_for('account', account_id=o.account_id) }}">{{ o.account }}</a>{% endif %}</td><td>{{ m.stage_badge(o.stage) }}</td><td>{{ probs[o.stage] }}%</td><td class="{{ 'badge b-err' if o.close_date and o.close_date < today and o.stage in opp_open }}">{{ o.close_date or '' }}</td><td class="right">{{ money(o.amount) }}</td></tr>{% endfor %}</table></div>
{% else %}<div class="empty"><b>No opportunities yet</b>Convert a lead to create your first one.</div>{% endif %}</div>
{% else %}<div class="kb">{% for s in all_stages %}<div class="kcol"><h3>{{ s }} <small>{{ cols[s]|length }} · {{ money(sums[s]) }}</small></h3><div class="kcards">
 {% for o in cols[s] %}<a class="kcard" href="{{ url_for('opp', opp_id=o.id) }}"><b>{{ o.name }}</b><small>{{ o.account or '' }}</small><small>{{ money(o.amount) }}{{ ' · closes ' ~ o.close_date if o.close_date }}</small></a>
 {% else %}<div class="muted" style="text-align:center;padding:8px">None</div>{% endfor %}</div></div>{% endfor %}</div>{% endif %}
{% endblock %}"""

OPP = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}{{ o.name }}{% endblock %}{% block body %}
{% set hl %}<div class="hl"><div><small>Account</small>{% if o.account_id %}<a href="{{ url_for('account', account_id=o.account_id) }}">{{ account_name }}</a>{% else %}—{% endif %}</div>
<div><small>Amount</small>{{ money(o.amount) }}</div><div><small>Close date</small>{{ o.close_date or '—' }}</div><div><small>Probability</small>{{ probs[o.stage] }}%</div>
<div><small>Contact</small>{% if lead %}<a href="{{ url_for('lead', lead_id=lead.id) }}">{{ lead_name }}</a>{% else %}—{% endif %}</div></div>{% endset %}
{% call m.header('t-opp', 'O', 'Opportunity', o.name, hl) %}{% endcall %}
{{ m.path(opp_open, o.stage, url_for('opp_stage', opp_id=o.id), closed_label, closed_cls, next_stage) }}
<div class="cols"><div>
 <div class="card"><div class="card-h"><h2>Details</h2></div><div class="card-b"><form method="post" action="{{ url_for('update_opp', opp_id=o.id) }}"><div class="grid2">
  <div><label>Name</label><input name="name" value="{{ o.name }}"></div><div><label>Amount ({{ currency }})</label><input name="amount" type="number" step="100" value="{{ (o.amount or 0)|int }}"></div>
  <div><label>Close date</label><input name="close_date" type="date" value="{{ o.close_date or '' }}"></div><div><label>Next step</label><input name="next_step" value="{{ o.next_step or '' }}"></div>
 </div><button class="btn brand" style="margin-top:12px">Save</button></form></div></div>
 {% if o.stage in opp_open %}<div class="card" id="close"><div class="card-h"><h2>Close this deal</h2></div><div class="card-b">
  <form method="post" action="{{ url_for('opp_stage', opp_id=o.id) }}"><button class="btn brand" name="stage" value="Closed Won">🏆 Closed Won</button> <button class="btn" name="stage" value="Closed Lost">Closed Lost</button></form></div></div>
 {% else %}<div class="card" id="close"><div class="card-b">Closed as <b>{{ o.stage }}</b>{% if o.closed_at %} on {{ o.closed_at[:10] }}{% endif %}. <form method="post" action="{{ url_for('opp_stage', opp_id=o.id) }}" style="display:inline"><button class="btn sm" name="stage" value="Negotiation">Reopen</button></form></div></div>{% endif %}
</div><div>
 {% if lead %}{% with lead_id=lead.id, opp_id=o.id %}""" + COMPOSER + r"""{% endwith %}{% endif %}
 <div class="card"><div class="card-h"><h2>Activity</h2></div><div class="card-b">""" + TIMELINE + r"""</div></div>
</div></div>
{% endblock %}"""

TASKS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Tasks{% endblock %}{% block body %}
{% call m.header('t-task', '✓', 'Tasks', {'open':'My open tasks','done':'Completed tasks','overdue':'Overdue tasks'}[show]) %}
 {% for s,lbl in [('open','Open'),('overdue','Overdue'),('done','Completed')] %}<a class="btn {{ 'brand' if show==s }}" href="{{ url_for('tasks', show=s) }}">{{ lbl }}</a>{% endfor %}
{% endcall %}
{% call m.help('tasks', 'Your to-do list') %} The bot creates a task whenever someone replies with interest. Add your own from any lead's page. Tick a task to complete it.{% endcall %}
<div class="card">{% if rows %}<table class="tbl"><thead><tr><th></th><th>Subject</th><th>Related to</th><th>Due</th></tr></thead>
{% for t in rows %}<tr><td style="width:30px"><form method="post" action="{{ url_for('task_toggle', task_id=t.id) }}"><input type="hidden" name="next" value="{{ request.full_path }}"><input type="checkbox" style="width:auto" {{ 'checked' if t.status=='done' }} onchange="this.form.submit()"></form></td>
<td>{{ t.subject }}</td><td>{% if t.lead_id %}<a href="{{ url_for('lead', lead_id=t.lead_id) }}">{{ t.lead_name }}</a>{% endif %}{% if t.opp_id %} · <a href="{{ url_for('opp', opp_id=t.opp_id) }}">{{ t.opp_name }}</a>{% endif %}</td>
<td class="nowrap"><span class="{{ 'badge b-err' if t.status=='open' and t.due_date < today }}">{{ t.due_date }}</span></td></tr>{% endfor %}</table>
{% else %}<div class="empty"><b>Nothing here</b>{{ 'You are all caught up.' if show != 'done' else 'No completed tasks yet.' }}</div>{% endif %}</div>
{% endblock %}"""

REPORTS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Reports{% endblock %}{% block body %}
{% call m.header('t-rep', '▤', 'Reports', 'Outreach performance') %}<a class="btn" href="{{ url_for('export') }}">Export leads (CSV)</a>{% endcall %}
{% call m.help('reports', 'Reading these reports') %} Use these to decide what to change: the <b>email steps</b> table shows which email gets replies; the <b>source</b> table shows which lead lists are worth paying for; the <b>forecast</b> weights each deal by its stage's win probability.{% endcall %}
<div class="cols3">
 <div class="card"><div class="card-h"><h2>Lead funnel</h2></div><div class="card-b"><table class="tbl">
 {% for s, n in funnel %}<tr><td style="width:110px">{{ s|title }}</td><td><div class="bar" style="width:{{ (n / funnel_max * 100)|round(1) }}%"></div></td><td class="right" style="width:40px">{{ n }}</td></tr>{% endfor %}</table></div></div>
 <div class="card"><div class="card-h"><h2>Emails by step</h2></div><div class="card-b"><table class="tbl"><thead><tr><th>Step</th><th class="right">Sent</th><th class="right">Replies</th><th class="right">Rate</th></tr></thead>
 {% for r in steps %}<tr><td>{{ r.label }}</td><td class="right">{{ r.sent }}</td><td class="right">{{ r.replies }}</td><td class="right">{{ r.rate }}</td></tr>{% else %}<tr><td colspan="4" class="muted">No emails sent yet.</td></tr>{% endfor %}</table></div></div>
 <div class="card"><div class="card-h"><h2>Replies by type</h2></div><div class="card-b"><table class="tbl">
 {% for label, n in reply_types %}<tr><td>{{ label.replace('_',' ')|capitalize }}</td><td class="right">{{ n }}</td></tr>{% else %}<tr><td class="muted">No replies yet.</td></tr>{% endfor %}</table></div></div>
</div>
<div class="card"><div class="card-h"><h2>Template performance</h2><span class="muted">a reply counts for the last template that lead received</span></div><div class="scroll"><table class="tbl"><thead><tr><th>Email</th><th>Template</th><th class="right">Sent</th><th class="right">Replies</th><th class="right">Positive</th><th class="right">Reply rate</th></tr></thead>
{% for r in templates %}<tr><td class="nowrap">{{ r.step }}</td><td>{{ r.name }}</td><td class="right">{{ r.sent }}</td><td class="right">{{ r.replies }}</td><td class="right">{{ r.positive }}</td><td class="right">{{ r.rate }}</td></tr>
{% else %}<tr><td colspan="6" class="muted">No emails sent yet.</td></tr>{% endfor %}</table></div></div>
<div class="cols">
 <div class="card"><div class="card-h"><h2>Lead sources</h2></div><div class="scroll"><table class="tbl"><thead><tr><th>Source</th><th class="right">Leads</th><th class="right">Contacted</th><th class="right">Replied</th><th class="right">Reply rate</th><th class="right">Converted</th></tr></thead>
 {% for r in sources %}<tr><td>{{ r.src }}</td><td class="right">{{ r.n }}</td><td class="right">{{ r.contacted }}</td><td class="right">{{ r.replied }}</td><td class="right">{{ r.rate }}</td><td class="right">{{ r.converted }}</td></tr>{% endfor %}</table></div></div>
 <div class="card"><div class="card-h"><h2>Pipeline forecast</h2></div><div class="card-b"><table class="tbl"><thead><tr><th>Stage</th><th class="right">Deals</th><th class="right">Amount</th><th class="right">Weighted</th></tr></thead>
 {% for r in pipe %}<tr><td>{{ r.stage }} <span class="muted">({{ r.prob }}%)</span></td><td class="right">{{ r.n }}</td><td class="right">{{ money(r.amount) }}</td><td class="right">{{ money(r.weighted) }}</td></tr>{% endfor %}
 <tr><td><b>Open total</b></td><td></td><td class="right"><b>{{ money(pipe_open) }}</b></td><td class="right"><b>{{ money(pipe_weighted) }}</b></td></tr></table>
 <p class="muted" style="margin-bottom:0">Win rate: <b>{{ win_rate }}</b> of closed deals.</p></div></div>
</div>
{% endblock %}"""

REVIEW = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Review queue{% endblock %}{% block body %}
{% call m.header('t-rev', '✉', 'Review', drafts|length ~ ' draft(s) waiting · ' ~ approved ~ ' approved') %}
 {% if drafts and ai_on %}<form method="post" action="{{ url_for('rewrite_openers') }}" onsubmit="this.querySelector('button').disabled=true;this.querySelector('button').textContent='Rewriting… (about 5 seconds per email)'"><button class="btn">↻ Rewrite all AI openers</button></form>{% endif %}
 {% if drafts and not held %}<form method="post" action="{{ url_for('approve_all') }}" onsubmit="return confirm('Approve all {{ drafts|length }} drafts as they are?')"><button class="btn">Approve all</button></form>{% endif %}
 {% if approved %}<form method="post" action="{{ url_for('job_start') }}"><input type="hidden" name="cmd" value="run"><button class="btn brand">▶ Send approved now</button></form>{% endif %}
{% endcall %}
{% if held %}<div class="help" style="margin-bottom:12px"><b>Your manager approves your emails.</b> Drafts are sent to your manager automatically (at the next check-in, within a couple of minutes). Once approved they send on the next bot run. You can still read them here.</div>{% endif %}
{% call m.help('review', 'Nothing is sent until you approve it') %}
<ol><li>Read the draft. The first line is written by AI from the lead's website and profile.</li>
<li>Edit anything you like, or click <b>New AI opener</b> for a different first line.</li>
<li><b>Approve</b> it, or <b>Reject lead</b> to stop all emails to that person.</li>
<li>Approved emails go out on the next bot run (or click <b>Send approved now</b>). The footer and unsubscribe line are added automatically.</li></ol>{% endcall %}
{% for d in drafts %}
<div class="card" id="d{{ d.id }}"><div class="card-h">{{ m.fit(d.x.fit) }}<h2><a href="{{ url_for('lead', lead_id=d.lead_id) }}">{{ d.name }}</a> <span class="muted" style="font-weight:400">· {{ d.x.title or '' }}{{ ' · ' if d.x.title }}{{ d.company or '' }}</span></h2>
 {% if d.variant %}<span class="badge" title="Template">{{ d.variant }}</span>{% endif %}<span class="badge b-info">{{ 'First email' if d.step == 0 else 'Follow-up ' ~ d.step }}</span></div>
 <div class="card-b">
 <div class="muted">To {{ d.email }} · from {{ d.sender }}{% if d.website %} · <a href="https://{{ d.website }}" target="_blank" rel="noopener">{{ d.website }} ↗</a>{% endif %}{% if d.x.linkedin %} · <a href="{{ d.x.linkedin }}" target="_blank" rel="noopener">LinkedIn ↗</a>{% endif %}</div>
 {% if d.avoid %}<div class="help" style="margin:8px 0 0;border-color:#c23934">⚠️ This email contains words on your avoid list: <b>{{ d.avoid|join(', ') }}</b>. Edit them out before approving, or it may be filtered as spam.</div>{% endif %}
 {% if d.x.fit == 'C' %}<div class="help" style="margin:8px 0 0">⚠️ This lead's title is outside your target roles (fit C). Consider rejecting.</div>{% endif %}
 <form method="post" action="{{ url_for('review_action', draft_id=d.id) }}">
  <label>Subject</label><input name="subject" value="{{ d.subject }}" {{ 'readonly title="Follow-ups keep the same subject so they thread"' if d.step > 0 }}>
  <label>Body</label><textarea name="body" style="min-height:230px;font-family:ui-monospace,Menlo,Consolas,monospace">{{ d.body }}</textarea>
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">
   {% if held %}<span class="badge b-info">Awaiting your manager</span>{% else %}<button class="btn brand" name="action" value="approve">✓ Approve</button>
   <button class="btn" name="action" value="save">Save edits</button>{% endif %}
   {% if d.step == 0 %}<button class="btn" name="action" value="regenerate" {{ 'disabled title="Add ANTHROPIC_API_KEY to .env to use AI openers"' if not ai_on }}>↻ New AI opener</button>{% endif %}
   <span style="flex:1"></span>
   <button class="btn danger" name="action" value="reject" onclick="return confirm('Reject this lead? No emails will be sent to them.')">Reject lead</button>
  </div></form></div></div>
{% else %}<div class="card"><div class="empty"><b>Nothing waiting for review</b>{% if lead_count %}Click <i>Run bot now</i> to draft emails for leads that are due.<br><br>
<form method="post" action="{{ url_for('job_start') }}"><input type="hidden" name="cmd" value="run"><button class="btn brand">▶ Run bot now</button></form>{% else %}Add some leads first.<br><br><a class="btn brand" href="{{ url_for('import_page') }}">Import leads</a>{% endif %}</div></div>{% endfor %}
{% endblock %}"""

SETUP = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Getting started{% endblock %}{% block body %}
{% call m.header('t-setup', '⚙', 'Getting started', done ~ ' of ' ~ steps|length ~ ' steps complete') %}<button class="btn" onclick="startTour(true)">Take the tour</button>{% endcall %}
<div class="card"><div class="card-b"><div class="progress"><div style="width:{{ (done / steps|length * 100)|round }}%"></div></div>
<p class="muted" style="margin-bottom:0">This checklist checks your setup automatically. Work top to bottom; each step says exactly what to do. Steps tick themselves off.</p></div></div>
<div class="card">{% for s in steps %}
<div class="step {{ 'ok' if s.ok else ('next' if loop.index0 == next_idx) }}" id="{{ s.id }}"><div class="n">{{ '✓' if s.ok else loop.index }}</div><div class="bd">
 <h3>{{ s.title }}</h3><p>{{ s.text|safe }}</p>
 {% if s.todo and not s.ok %}<ul style="margin:0 0 8px;padding-left:18px">{% for t in s.todo %}<li>{{ t|safe }}</li>{% endfor %}</ul>{% endif %}
 {% if s.action %}<form method="post" action="{{ url_for('job_start') }}" style="display:inline"><input type="hidden" name="cmd" value="{{ s.action }}">{% if s.arg %}<input type="hidden" name="max" value="{{ s.arg }}">{% endif %}
  <button class="btn {{ 'brand' if not s.ok and loop.index0 == next_idx }}" {{ 'disabled' if s.blocked and not s.ok }}>{{ s.button }}</button></form>{% endif %}
 {% if s.link %}<a class="btn {{ 'brand' if not s.ok and loop.index0 == next_idx }}" href="{{ s.link }}">{{ s.link_text }}</a>{% endif %}
 {% if s.sample %}<form method="post" action="{{ url_for('sample_remove' if s.has_sample else 'sample_load') }}" style="display:inline"><button class="btn">{{ 'Remove sample leads' if s.has_sample else 'Add sample leads' }}</button></form>{% endif %}
 {% if s.blocked and not s.ok %}<span class="muted"> Finish step {{ s.blocked }} first.</span>{% endif %}
</div></div>{% endfor %}</div>
{% endblock %}"""

JOB = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Bot run{% endblock %}{% block body %}
{% call m.header('t-setup', '▶', 'Bot run #' ~ j.id, job_labels.get(j.cmd, j.cmd)) %}<a class="btn" href="{{ url_for('jobs') }}">All runs</a>{% endcall %}
<div class="card"><div class="card-h"><h2>{{ m.status_badge(j.status, {'running':'Running…','ok':'Finished','failed':'Failed'}) }}</h2><span class="muted">started {{ j.started_at[:19].replace('T',' ') }} UTC</span></div>
<div class="card-b">{% for p in problems %}<div class="toast err">⚠️ {{ p }}</div>{% endfor %}
{% if problems and 'Settings' in problems|join %}<p><a class="btn brand" href="{{ url_for('settings') }}">Open Settings</a></p>{% endif %}
{% if j.status == 'running' %}<p class="muted">This page refreshes itself until the run finishes. Sending can take a few minutes because emails are spaced out on purpose.</p>{% endif %}
<pre class="log">{{ j.output or 'Starting…' }}</pre>
{% if j.status != 'running' %}<div style="display:flex;gap:6px;flex-wrap:wrap">{% for ep, label in after_links %}<a class="btn {{ 'brand' if loop.first }}" href="{{ url_for(ep) }}">{{ label }}</a>{% endfor %}</div>{% endif %}
</div></div>
{% endblock %}{% block scripts %}{% if j.status == 'running' %}<script>setTimeout(function(){location.reload()},2500)</script>{% endif %}{% endblock %}"""

JOBS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Bot runs{% endblock %}{% block body %}
{% call m.header('t-setup', '▶', 'Bot', 'Run history') %}{% endcall %}
<div class="card">{% if rows %}<table class="tbl"><thead><tr><th>#</th><th>What</th><th>Status</th><th>Started (UTC)</th></tr></thead>
{% for j in rows %}<tr><td><a href="{{ url_for('job', job_id=j.id) }}">{{ j.id }}</a></td><td>{{ job_labels.get(j.cmd, j.cmd) }}</td><td>{{ m.status_badge(j.status, {'running':'Running…','ok':'Finished','failed':'Failed'}) }}</td><td>{{ j.started_at[:16].replace('T',' ') }}</td></tr>{% endfor %}</table>
{% else %}<div class="empty">No runs yet.</div>{% endif %}</div>
{% endblock %}"""

NEW_LEAD = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}New lead{% endblock %}{% block body %}
{% call m.header('t-lead', 'L', 'Leads', 'New lead') %}{% endcall %}
<div class="card" style="max-width:640px"><div class="card-b"><form method="post"><div class="grid2">
 <div><label>Email *</label><input name="email" type="email" required></div><div><label>First name</label><input name="first_name"></div>
 <div><label>Last name</label><input name="last_name"></div><div><label>Title</label><input name="title"></div>
 <div><label>Company</label><input name="company"></div><div><label>Website</label><input name="website" placeholder="company.com"></div>
 <div><label>LinkedIn</label><input name="linkedin"></div><div><label>Country</label><input name="country"></div>
</div>
<label style="display:flex;gap:6px;align-items:center;margin-top:14px;color:var(--ink)"><input type="checkbox" name="sequence" value="1" checked style="width:auto"> Add to the email sequence</label>
<button class="btn brand" style="margin-top:12px">Save</button></form></div></div>
{% endblock %}"""

IMPORT = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Import leads{% endblock %}{% block body %}
{% call m.header('t-lead', 'L', 'Leads', 'Import leads') %}{% endcall %}
{% call m.help('import', 'What file can I upload?') %} An <b>Excel (.xlsx)</b> or <b>CSV</b> file with one lead per row. Only an email column is required. Column names are matched automatically (for example "First Name", "Company", "Title", "Website", "Employee Linkedin"). Shared inboxes like info@ or hello@ are skipped because they hurt deliverability. Duplicates and do-not-contact addresses are skipped too.{% endcall %}
{% if result %}<div class="card"><div class="card-h"><h2>Import finished</h2></div><div class="card-b">
<p><b>{{ result.added }}</b> lead(s) added.</p>{% if result.skipped %}<table class="tbl"><thead><tr><th>Skipped because</th><th class="right">Count</th></tr></thead>{% for k, v in result.skipped.items() %}<tr><td>{{ k }}</td><td class="right">{{ v }}</td></tr>{% endfor %}</table>{% endif %}
<p><a class="btn brand" href="{{ url_for('leads') }}">View leads</a> <a class="btn" href="{{ url_for('setup') }}">Back to Getting started</a></p></div></div>{% endif %}
<div class="card" style="max-width:640px"><div class="card-b"><form method="post" enctype="multipart/form-data">
 <label>File</label><input type="file" name="file" accept=".csv,.xlsx" required>
 <label>Campaign</label><select name="campaign_id">{% for c in all_campaigns %}<option value="{{ c.id }}" {{ 'selected' if request.args.get('campaign')|string == c.id|string }}>{{ c.name }}{{ ' (paused)' if c.status != 'active' }}</option>{% endfor %}</select>
 <label style="display:flex;gap:6px;align-items:center;margin-top:14px;color:var(--ink)"><input type="checkbox" name="sequence" value="1" checked style="width:auto"> Add these leads to the email sequence</label>
 <button class="btn brand" style="margin-top:12px">Import</button></form></div></div>
{% endblock %}"""

SEARCH = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Search{% endblock %}{% block body %}
{% call m.header('t-search', '⌕', 'Search results', '"' ~ q ~ '"') %}{% endcall %}
{% for title, tile, letter, items, ep, key in groups %}<div class="card"><div class="card-h"><div class="tile {{ tile }}">{{ letter }}</div><h2>{{ title }} ({{ items|length }})</h2></div>
{% if items %}<table class="tbl">{% for i in items %}<tr><td><a href="{{ url_for(ep, **{key: i.id}) }}"><b>{{ i.label }}</b></a></td><td class="muted">{{ i.sub }}</td></tr>{% endfor %}</table>{% else %}<div class="card-b muted">No matches.</div>{% endif %}</div>{% endfor %}
{% endblock %}"""

SETTINGS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Settings{% endblock %}{% block body %}
{% call m.header('t-setup', '⚙', 'Settings', 'Set up your outreach') %}<a class="btn" href="{{ url_for('setup') }}">Getting Started checklist</a>{% endcall %}
{% if result %}<div class="toast {{ '' if ok else 'err' }}">{{ result }}</div>{% endif %}
<div class="settings">
<nav class="snav">{% for k, lbl, n in [('inbox','Email inboxes','1'),('safety','Safety & automation','2'),('keys','AI & Apollo keys','3'),('emails','Your offer & emails','4'),('targeting','Lead targeting','5'),('schedule','Sending schedule','6'),('handover','Handover','7'),('team','Team','8')] %}
 <a href="{{ url_for('settings', tab=k) }}" class="{{ 'on' if tab == k }}"><span class="sn">{{ n }}</span>{{ lbl }}</a>{% endfor %}</nav>
<div class="spanel">

{% if tab == 'inbox' %}
<div class="card"><div class="card-h"><h2>Email inboxes</h2></div><div class="card-b">
<p class="muted" style="margin-top:0">The addresses your emails are sent from. One is enough to start; add up to {{ max_inboxes }} (choose which ones each campaign uses on its page). Use your <b>outreach domains</b>, not your main company inbox. To add many at once, use <a href="#bulk">Add several inboxes</a> below.</p>
<form method="post" action="{{ url_for('save_inbox') }}">
{% for ib in inboxes %}{% set s = ib.s %}{% set n = ib.n %}
<fieldset class="ibox" {{ 'data-optional' if n > 1 }}>{% if s.email %}<details {{ 'open' if ib.paused }}><summary style="cursor:pointer"><b>Inbox {{ n }}: {{ s.email }}</b> <span class="muted">{{ s.name }} · {{ ib.today }}/day{% if s.ramp_from and ib.today < (s.daily_limit or 10) %} today (ramping up to {{ s.daily_limit }}){% endif %}{% if ib.campaigns %} · {{ ib.campaigns|join(', ') }}{% endif %}</span>
 {% if ib.paused %}<span class="badge" style="background:#fde1e1;color:#7b1018">paused</span>{% elif ib.pw %}<span class="badge b-ok">saved</span>{% endif %}</summary>
 {% if ib.paused %}<div class="help" style="margin:8px 0;border-color:#c23934">⛔ Paused by the deliverability guard: {{ ib.paused }} <button class="btn sm" formaction="{{ url_for('inbox_resume') }}" name="email" value="{{ s.email }}">Resume it</button></div>{% endif %}
 {% else %}<legend>Inbox {{ n }}{{ ' (optional)' if n > 1 }}</legend>{% endif %}
 <div class="grid2">
  <div><label>Email provider</label><select name="provider{{ n }}" data-n="{{ n }}" class="prov">{% for k, p in providers.items() %}<option value="{{ k }}" {{ 'selected' if ib.provider == k }}>{{ p.label }}</option>{% endfor %}</select></div>
  <div><label>Email address</label><input name="email{{ n }}" type="email" value="{{ s.email or '' }}" placeholder="you@your-outreach-domain.com"></div>
  <div><label>Your name (what prospects see)</label><input name="name{{ n }}" value="{{ s.name or '' }}" placeholder="e.g. Sara Khan"></div>
  <div><label>App password {% if ib.pw %}<span class="badge b-ok">saved {{ ib.pw }}</span>{% endif %}</label><input name="password{{ n }}" type="password" autocomplete="new-password" placeholder="{{ 'Leave blank to keep the saved one' if ib.pw else 'Paste the app password' }}"></div>
  <div><label>Emails per day from this inbox</label><input name="daily_limit{{ n }}" type="number" min="1" max="100" value="{{ s.daily_limit or 10 }}"></div>
 </div>
 <p class="hint" id="hint{{ n }}">{{ providers[ib.provider].help }}</p>
 <details class="adv" {{ 'open' if ib.provider == 'other' }}><summary>Server details</summary><div class="grid2">
  <div><label>Sending server (SMTP)</label><input name="smtp_host{{ n }}" id="smtp{{ n }}" value="{{ s.smtp_host or providers[ib.provider].smtp_host }}"></div>
  <div><label>SMTP port</label><input name="smtp_port{{ n }}" id="port{{ n }}" type="number" value="{{ s.smtp_port or 587 }}"></div>
  <div><label>Receiving server (IMAP)</label><input name="imap_host{{ n }}" id="imap{{ n }}" value="{{ s.imap_host or providers[ib.provider].imap_host }}"></div>
 </div></details>
 <label class="check"><input type="checkbox" name="ramp{{ n }}" {{ 'checked' if s.ramp_from }}> New inbox: ramp up gradually ({{ (c.ramp or {}).start or 5 }}/day, +{{ (c.ramp or {}).per_week or 5 }} each week, up to the limit above){% if s.ramp_from %} <span class="muted">· since {{ s.ramp_from }}</span>{% endif %}</label><input type="hidden" name="ramp_from{{ n }}" value="{{ s.ramp_from or '' }}">
 <button class="btn" name="test" value="{{ n }}" style="margin-top:10px">Save &amp; test connection</button>
{% if s.email %}</details>{% endif %}</fieldset>{% endfor %}
<button class="btn brand">Save inboxes</button></form></div></div>

<div class="card" id="health"><div class="card-h"><h2>Sending domain health</h2><span style="flex:1"></span><form method="post" action="{{ url_for('domains_check') }}" style="margin:0"><button class="btn sm">Check now</button></form></div><div class="card-b">
 <p class="muted" style="margin-top:0">SPF, DKIM and DMARC tell receiving servers your emails are genuine; without them more of your emails land in spam. MX lets replies reach you.</p>
 {% if dom_health %}<table class="tbl"><tr><th>Domain</th><th>SPF</th><th>DKIM</th><th>DMARC</th><th>MX</th><th>Checked</th></tr>{% for d, r in dom_health.items() %}<tr><td><b>{{ d }}</b></td>
 {% if r.error %}<td colspan="4" class="muted">{{ r.error }}</td>{% else %}{% for k in ['spf','dkim','dmarc','mx'] %}<td>{{ '✅' if r[k][0] else '❌' }} <small class="muted">{{ r[k][1] }}</small></td>{% endfor %}{% endif %}<td class="muted">{{ r.at[:10] }}</td></tr>
 {% if r.fix %}<tr><td colspan="6"><ul style="margin:0">{% for x in r.fix %}<li>{{ x }}</li>{% endfor %}</ul></td></tr>{% endif %}{% endfor %}</table>
 {% else %}<p class="muted">Not checked yet. Click <b>Check now</b>.</p>{% endif %}
 <p class="hint">The deliverability guard also pauses an inbox by itself if more than 5% of its last emails bounce (after at least 20) or its login fails 3 times in a row, and tells you. Keep warm-up running in Instantly for every inbox.</p></div></div>

<div class="card" id="bulk"><div class="card-h"><h2>Add several inboxes</h2></div><div class="card-b"><form method="post" action="{{ url_for('inbox_bulk') }}">
 <p class="muted" style="margin-top:0">For inboxes on the same mail server: one per line as <code>email, name, password</code>. Passwords are stored encrypted on this computer.</p>
 <textarea name="lines" style="min-height:120px;font-family:ui-monospace,Menlo,Consolas,monospace" placeholder="maya.hart@getneosofttech.com, Maya Hart, password1&#10;liam.cole@getneosofttech.com, Liam Cole, password2" required></textarea>
 <div class="grid2"><div><label>Sending server (SMTP)</label><input name="smtp_host" placeholder="mail.getneosofttech.com" required></div><div><label>SMTP port</label><input name="smtp_port" type="number" value="465"></div>
 <div><label>Receiving server (IMAP)</label><input name="imap_host" placeholder="mail.getneosofttech.com" required></div><div><label>Emails per day from each</label><input name="daily_limit" type="number" min="1" max="100" value="10"></div></div>
 <label class="check"><input type="checkbox" name="ramp" checked> New inboxes: ramp up gradually</label>
 <button class="btn brand" style="margin-top:10px">Add inboxes</button></form></div></div>

{% elif tab == 'safety' %}
<div class="card"><div class="card-h"><h2>Safety &amp; automation</h2></div><div class="card-b"><form method="post" action="{{ url_for('save_safety') }}">
 <label class="switch"><input type="checkbox" name="test_mode" {{ 'checked' if (c.test_mode or {}).enabled }}><span></span><b>Test mode</b> &mdash; send every email to me instead of the lead</label>
 <div style="max-width:420px"><label>My test address</label><input name="redirect_to" type="email" value="{{ (c.test_mode or {}).redirect_to or '' }}" placeholder="you@gmail.com"></div>
 <p class="hint">Keep this on while your inboxes warm up. Subjects show who each email was meant for. Turn it off to go live.</p>
 <label class="switch"><input type="checkbox" name="review" {{ 'checked' if (c.review or {}).enabled }}><span></span><b>Review before sending</b> &mdash; every email waits for my approval</label>
 <p class="hint">Recommended for at least the first few weeks.</p>
 <label class="switch"><input type="checkbox" name="autorun" {{ 'checked' if (c.autorun or {}).enabled }}><span></span><b>Run automatically</b> and keep running in the background</label>
 <div style="max-width:220px"><label>Every</label><select name="every_minutes">{% for mins, lbl in [(30,'30 minutes'),(60,'hour'),(120,'2 hours'),(240,'4 hours')] %}<option value="{{ mins }}" {{ 'selected' if (c.autorun or {}).every_minutes == mins or (not (c.autorun or {}).every_minutes and mins == 60) }}>{{ lbl }}</option>{% endfor %}</select></div>
 <p class="hint">Each run drafts emails for due leads, sends approved ones (only in your sending hours, unless test mode is on) and checks for replies. When this is on, closing the window leaves the app running with an icon next to the clock (right-click it to open or quit). When it's off, closing the window quits the app.</p>
 {% if startup_ok %}<label class="switch"><input type="checkbox" name="start_with_windows" {{ 'checked' if startup_on }}><span></span><b>Start BSO when Windows starts</b> &mdash; in the background, with the icon next to the clock</label>
 <p class="hint">For the computer that runs the campaigns. With <b>Run automatically</b> on, BSO also stops Windows from going to sleep. Closing a laptop's lid still sleeps it unless you set Windows → Settings → System → Power → <i>Lid close action: Do nothing</i> (when plugged in).</p>{% endif %}
 {% if is_bot %}<p class="hint">This BSO is signed in as the <b>BSO Bot</b>: it stays signed in after a restart, so campaigns carry on by themselves.</p>{% endif %}
 <button class="btn brand" style="margin-top:12px">Save</button></form></div></div>

{% elif tab == 'keys' %}
<div class="card"><div class="card-h"><h2>AI &amp; Apollo keys</h2></div><div class="card-b"><form method="post" action="{{ url_for('save_keys') }}">
 <p class="muted" style="margin-top:0">Keys are stored only on this computer. Leave a box blank to keep the saved key.</p>
 <label>Anthropic API key {% if keys.ANTHROPIC_API_KEY %}<span class="badge b-ok">saved {{ keys.ANTHROPIC_API_KEY }}</span>{% endif %}</label>
 <div class="inline"><input name="ANTHROPIC_API_KEY" type="password" autocomplete="off" placeholder="sk-ant-…"><button class="btn" name="test" value="ai" style="flex:0">Save &amp; test</button></div>
 <p class="hint">Writes each lead's personalised opening line and sorts replies. Get one at <a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a> → API keys.</p>
 <label class="switch"><input type="checkbox" name="ai_reply_sorting" {{ 'checked' if (c.ai_reply_sorting or {}).enabled }}><span></span><b>Use AI to sort replies</b> (optional)</label>
 <p class="hint">Off: replies are sorted on this computer with keyword rules, and their text isn't sent anywhere. On: reply text is sent to Anthropic for more accurate sorting. Openers never include the contact's name or email; the AI only sees their company's website, company name and job title.</p>
 <label>Apollo API key (optional) {% if keys.APOLLO_API_KEY %}<span class="badge b-ok">saved {{ keys.APOLLO_API_KEY }}</span>{% endif %}</label>
 <div class="inline"><input name="APOLLO_API_KEY" type="password" autocomplete="off" placeholder="Apollo key"><button class="btn" name="test" value="apollo" style="flex:0">Save &amp; test</button></div>
 <p class="hint">Finds new leads with verified emails. In Apollo: Settings → Integrations → API.</p>
 <label>Slack webhook (optional) {% if keys.SLACK_WEBHOOK_URL %}<span class="badge b-ok">saved</span>{% endif %}</label>
 <input name="SLACK_WEBHOOK_URL" type="password" autocomplete="off" placeholder="https://hooks.slack.com/services/…">
 <p class="hint">Get a Slack message whenever someone replies with interest.</p>
 <button class="btn brand" style="margin-top:12px">Save keys</button></form></div></div>

{% elif tab == 'emails' %}
<div class="card"><div class="card-h"><h2>Your offer &amp; emails</h2></div><div class="card-b">
 {% if avoid_hits %}<div class="help" style="border-color:#c23934">⚠️ Some templates use words on your avoid list, so the bot will not send them until you edit them out:<ul>{% for i, vs in avoid_hits.items() %}{% for v, hits in vs.items() %}<li>Email {{ i }} · {{ v }}: <b>{{ hits|join(', ') }}</b></li>{% endfor %}{% endfor %}</ul></div>
 {% elif avoid_n %}<p class="hint">✓ Checked against your avoid list ({{ avoid_n }} words and phrases). None are used.</p>{% endif %}
 <form method="post" action="{{ url_for('save_emails') }}">
 <label>What you offer (the AI uses this to write each lead's opening line)</label><textarea name="offer" rows="4">{{ c.offer or '' }}</textarea>
 <label>Company name and postal address (legally required in the footer)</label><input name="sender_address" value="{{ c.sender_address or '' }}">
 <h3 style="margin:18px 0 4px;font-size:14px">Company facts used in the templates</h3>
 <p class="hint" style="margin-top:0">⚠️ <b>Confirm these before going live.</b> The original templates promised a free proof of concept and a quick start; only keep offers NeoSOFT actually makes.</p>
 <div class="grid2">{% set fx = c.facts or {} %}
  <div><label>Team size <code>{team_size}</code></label><input name="fact_team_size" value="{{ fx.team_size or '' }}"></div>
  <div><label>Proof point <code>{proof_point}</code></label><input name="fact_proof_point" value="{{ fx.proof_point or '' }}"></div>
  <div><label>Trial offer <code>{trial_offer}</code></label><input name="fact_trial_offer" value="{{ fx.trial_offer or '' }}"></div>
  <div><label>Time to start <code>{lead_time}</code></label><input name="fact_lead_time" value="{{ fx.lead_time or '' }}"></div>
 </div>
 <label>Technologies <code>{stacks}</code></label><input name="fact_stacks" value="{{ fx.stacks or '' }}">
 <label class="switch" style="margin-top:14px"><input type="checkbox" name="ai_opener" {{ 'checked' if (c.personalization or {}).enabled }}><span></span><b>AI-written line</b> <code>{personal_line}</code> for each lead</label>
 <label>Fallback line (used when there's nothing specific to say)</label><input name="fallback_line" value="{{ (c.personalization or {}).fallback_line or '' }}">
 <p class="hint">Placeholders: <code>{first_name}</code> <code>{company}</code> <code>{title}</code> <code>{personal_line}</code> <code>{sender_name}</code> and the facts above.
 Each email can have several <b>templates</b>: leads are spread evenly across the switched-on ones, and <b>Reports → Template performance</b> shows which gets more replies.</p>
 {% for i in range(seq|length + 1) if i < 5 %}{% set st = seq[i] if i < seq|length else {'delay_days': 3, 'variants': []} %}
 <fieldset class="ibox step-box" {{ 'data-optional' if i >= seq|length }}><legend>{{ 'First email' if i == 0 else 'Follow-up ' ~ i }}{{ ' (optional: add a template to create it)' if i >= seq|length }}</legend>
  {% if i > 0 %}<div style="max-width:260px"><label>Send after (days without a reply)</label><input name="s{{ i }}_delay" type="number" min="1" max="30" value="{{ st.delay_days or 3 }}"></div>{% endif %}
  {% for j in range(st.variants|length + 1) if j < 10 %}{% set v = st.variants[j] if j < st.variants|length else {} %}
  <details class="variant" {{ 'open' if (j < st.variants|length and v.enabled and loop.first) }}>
   <summary>{% if j < st.variants|length %}<span class="badge {{ 'b-ok' if v.enabled else '' }}">{{ 'On' if v.enabled else 'Off' }}</span> {{ v.name }}{% else %}<span class="muted">+ Add a template</span>{% endif %}</summary>
   <div class="grid2"><div><label>Template name</label><input name="s{{ i }}v{{ j }}_name" value="{{ v.name or '' }}"></div>
   <div><label class="switch" style="margin-top:24px!important"><input type="checkbox" name="s{{ i }}v{{ j }}_on" {{ 'checked' if v.enabled or j >= st.variants|length }}><span></span>Use this template</label></div></div>
   {% if i == 0 %}<label>Subject</label><input name="s{{ i }}v{{ j }}_subject" value="{{ v.subject or '' }}">{% endif %}
   <label>Email (empty = delete this template)</label><textarea name="s{{ i }}v{{ j }}_body" rows="{{ 12 if v.body else 4 }}">{{ (v.body or '').rstrip() }}</textarea>
  </details>{% endfor %}
 </fieldset>{% endfor %}
 <p class="hint">Follow-ups are sent as replies in the same thread and stop as soon as someone replies. Footer and unsubscribe line are added automatically.</p>
 <button class="btn brand">Save emails</button></form></div></div>

{% elif tab == 'targeting' %}
<div class="card"><div class="card-h"><h2>Lead targeting (Apollo)</h2></div><div class="card-b"><form method="post" action="{{ url_for('save_targeting') }}">
 <label>Job titles to target (one per line)</label><textarea name="titles" rows="6">{{ (cf.person_titles or [])|join('\n') }}</textarea>
 <label>Seniority</label><div class="checks">{% for v, lbl in seniorities %}<label><input type="checkbox" name="seniorities" value="{{ v }}" {{ 'checked' if v in (cf.person_seniorities or []) }}> {{ lbl }}</label>{% endfor %}</div>
 <label>Company headquarters</label><div class="checks">{% for r in regions %}<label><input type="checkbox" name="regions" value="{{ r }}" {{ 'checked' if r in (cf.organization_locations or []) }}> {{ r }}{{ ' ⚠️' if r == 'Australia' }}</label>{% endfor %}</div>
 <label>Company size (employees)</label><div class="checks">{% for v, lbl in sizes %}<label><input type="checkbox" name="sizes" value="{{ v }}" {{ 'checked' if v in (cf.organization_num_employees_ranges or []) }}> {{ lbl }}</label>{% endfor %}</div>
 <label>Industries to search</label><div class="checks">{% for s in ac.searches or [] %}<label><input type="checkbox" name="searches" value="{{ s.name }}" {{ 'checked' if s.name not in (ac.disabled_searches or []) }}> {{ s.name }}</label>{% endfor %}</div>
 <div style="max-width:260px"><label>New leads per "Find leads" click</label><input name="max_per_pull" type="number" min="1" max="100" value="{{ ac.max_per_pull or 25 }}"></div>
 <p class="hint">Only Apollo-verified emails are kept, and each lead costs about 1 Apollo credit.</p>
 <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px"><button class="btn brand">Save targeting</button>
 <button class="btn" formaction="{{ url_for('job_start') }}" name="cmd" value="apollo-preview">Preview Apollo searches (free)</button></div></form></div></div>

{% if tab == 'targeting' %}
<div class="card" id="instantly"><div class="card-h"><h2>New leads from Instantly (automatic)</h2>{% if ins.auto and ins_key %}<span class="badge b-ok">automatic</span>{% endif %}</div><div class="card-b">
 <form method="post" action="{{ url_for('save_instantly') }}">
 <p class="muted" style="margin-top:0">The bot asks Instantly SuperSearch for people matching these filters; Instantly finds their work emails (using your SuperSearch credits) and the bot imports them, skipping anyone already in BSO. Needs an Instantly plan with API access.</p>
 <div class="grid2"><div><label>Instantly API key (v2) {% if ins_key %}<span class="badge b-ok">saved {{ ins_key }}</span>{% endif %}</label><input name="api_key" type="password" autocomplete="new-password" placeholder="{{ 'Leave blank to keep the saved one' if ins_key else 'Instantly → Settings → Integrations → API keys' }}"></div>
 <div><label>Job titles (one per line; matches titles containing these words)</label><textarea name="titles" style="min-height:80px" placeholder="CTO&#10;VP Engineering&#10;Head of Engineering">{{ (ins.titles or [])|join('\n') }}</textarea></div>
 <div><label>Countries (one per line)</label><textarea name="countries" style="min-height:60px" placeholder="United States&#10;United Kingdom">{{ (ins.countries or [])|join('\n') }}</textarea></div>
 <div><label>Keywords (optional)</label><input name="keywords" value="{{ ins.keywords or '' }}" placeholder="e.g. SaaS"></div></div>
 <label>Seniority</label><div class="checks">{% for v in ins_opts.LEVELS %}<label><input type="checkbox" name="levels" value="{{ v }}" {{ 'checked' if v in (ins.levels or []) }}> {{ v }}</label>{% endfor %}</div>
 <label>Department</label><div class="checks">{% for v in ins_opts.DEPARTMENTS %}<label><input type="checkbox" name="departments" value="{{ v }}" {{ 'checked' if v in (ins.departments or []) }}> {{ v }}</label>{% endfor %}</div>
 <label>Company size (employees)</label><div class="checks">{% for v in ins_opts.SIZES %}<label><input type="checkbox" name="sizes" value="{{ v }}" {{ 'checked' if v in (ins.sizes or []) }}> {{ v }}</label>{% endfor %}</div>
 <label>Industries</label><div class="checks">{% for v in ins_opts.INDUSTRIES %}<label><input type="checkbox" name="industries" value="{{ v }}" {{ 'checked' if v in (ins.industries or []) }}> {{ v }}</label>{% endfor %}</div>
 <div style="max-width:420px"><label>Put imported leads in this campaign</label><select name="campaign_id">{% for cp in all_campaigns %}<option value="{{ cp.id }}" {{ 'selected' if (ins.campaign_id or 1) == cp.id }}>{{ cp.name }}</option>{% endfor %}</select></div>
 <label class="check"><input type="checkbox" name="one_per_company" {{ 'checked' if ins.get('one_per_company', True) }}> One person per company</label>
 <div class="grid2"><div><label>Leads per import</label><input name="per_pull" type="number" min="1" max="1000" value="{{ ins.per_pull or 50 }}"></div>
 <div><label>Import automatically when fewer than this many days of new leads are left</label><input name="auto_below_days" type="number" min="0" step="0.5" value="{{ ins.auto_below_days or 3 }}"></div></div>
 <label class="check"><input type="checkbox" name="auto" {{ 'checked' if ins.auto }}> Import automatically (the bot checks on every run)</label>
 <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px"><button class="btn brand" name="action" value="save">Save</button><button class="btn" name="action" value="count">Count matching people (free)</button><button class="btn" name="action" value="pull" onclick="return confirm('Start an import now? It uses Instantly SuperSearch credits.')">Import now</button></div></form>
 {% if ins_pulls %}<table class="tbl" style="margin-top:12px"><tr><th>Started</th><th>Status</th><th class="r">Asked for</th><th class="r">Found</th><th class="r">Added</th></tr>{% for p in ins_pulls %}<tr><td>{{ (p.started_at or '')[:16].replace('T', ' ') }}</td><td>{{ 'Instantly is working on it' if p.status == 'running' else 'Done' }}</td><td class="r">{{ p.wanted }}</td><td class="r">{{ p.found if p.found is not none else '–' }}</td><td class="r">{{ p.imported if p.imported is not none else '–' }}</td></tr>{% endfor %}</table>{% endif %}
 <p class="hint">Don't also run these leads in an Instantly campaign, or they'd get two sequences.</p></div></div>

<div class="card" id="discover"><div class="card-h"><h2>Find more contacts on the web</h2>{% if dsc.enabled %}<span class="badge b-ok">on</span>{% endif %}</div><div class="card-b">
 <form method="post" action="{{ url_for('save_discover') }}">
 <p class="muted" style="margin-top:0">For companies already in BSO, the AI searches the public web for people in the roles below whose <b>work email is published</b> (company site, press releases, speaker pages…). BSO never guesses addresses: it opens the page the AI names and only adds an address it can see written there, at the company's own domain. With an Instantly key saved above, Instantly's verifier also checks each found address. Each company is searched once.</p>
 {% if not ai_key %}<div class="help" style="border-color:#dd7a01">Needs your Anthropic key (Settings → AI &amp; Apollo keys).</div>{% endif %}
 <label class="check"><input type="checkbox" name="enabled" {{ 'checked' if dsc.enabled }}> Search the web for more contacts on each bot run</label>
 <div class="grid2"><div><label>Roles to look for (one per line)</label><textarea name="roles" style="min-height:90px">{{ (dsc.roles or dsc_default)|join('\n') }}</textarea></div>
 <div><label>Companies to search per day</label><input name="per_day" type="number" min="1" max="200" value="{{ dsc.per_day or 10 }}"><p class="hint">About $0.03–0.05 per company in Anthropic usage (web search is $10 per 1,000 searches, up to 3 per company). 10 a day is about $10–15 a month.</p></div></div>
 <label class="check"><input type="checkbox" name="add_to_sequence" {{ 'checked' if dsc.get('add_to_sequence', True) }}> Start the email sequence for contacts found</label>
 <p class="hint">Contacts in the EU/UK: you need a legitimate interest to email them, and every email already carries an opt-out.</p>
 <button class="btn brand" style="margin-top:8px">Save</button></form>
 {% if dsc_recent %}<table class="tbl" style="margin-top:12px"><tr><th>Company</th><th>Searched</th><th class="r">Found</th><th class="r">Added</th><th>Not used</th></tr>{% for d in dsc_recent %}<tr><td>{{ d.domain }}</td><td>{{ d.at[:10] }}</td><td class="r">{{ d.found }}</td><td class="r">{{ d.added }}</td><td class="muted" style="font-size:12px">{{ d.note }}</td></tr>{% endfor %}</table>{% endif %}
 </div></div>
<div class="card" id="intake"><div class="card-h"><h2>Leads by email</h2>{% if li.enabled %}<span class="badge b-ok">on: {{ li.inbox }}</span>{% endif %}</div><div class="card-b">
 <form method="post" action="{{ url_for('save_intake') }}">
 <p class="muted" style="margin-top:0">Email a CSV or Excel (.xlsx) lead file, for example an Instantly export, to the inbox below. On its next run BSO imports it, skips duplicates and do-not-contact addresses, and replies with what it added. Nobody needs to import leads by hand.</p>
 <label class="check"><input type="checkbox" name="enabled" {{ 'checked' if li.enabled }}> Import lead files emailed to this inbox</label>
 <div class="grid2"><div><label>Intake inbox (one of your inboxes)</label><select name="inbox">{% for e in inbox_emails %}<option {{ 'selected' if li.inbox == e }}>{{ e }}</option>{% endfor %}</select></div>
 <div><label>Who may send lead files (one address per line)</label><textarea name="allowed_senders" style="min-height:70px" placeholder="omar@blacswanken.com">{{ (li.allowed_senders or [])|join('\n') }}</textarea></div></div>
 <div style="max-width:420px"><label>Put imported leads in this campaign <span class="muted">(or write "campaign: name" in the email's subject)</span></label><select name="campaign_id">{% for cp in all_campaigns %}<option value="{{ cp.id }}" {{ 'selected' if (li.campaign_id or 1) == cp.id }}>{{ cp.name }}</option>{% endfor %}</select></div>
 <label class="check"><input type="checkbox" name="add_to_sequence" {{ 'checked' if li.get('add_to_sequence', True) }}> Start the email sequence for imported leads</label>
 <label class="check"><input type="checkbox" name="verified_only" {{ 'checked' if li.get('verified_only', True) }}> Only accept files whose sender the mail server verified (recommended: stops someone faking your address)</label>
 <p class="hint">Emails from anyone else are treated as normal mail. The confirmation reply goes to whoever sent the file, even in test mode.</p>
 <button class="btn brand" style="margin-top:10px">Save</button></form></div></div>{% endif %}

{% elif tab == 'schedule' %}
<div class="card"><div class="card-h"><h2>Sending schedule</h2></div><div class="card-b"><form method="post" action="{{ url_for('save_schedule') }}">
 <p class="muted" style="margin-top:0">Emails go out during your <b>prospects'</b> working hours. Test mode ignores this so you can test any time.</p>
 <div class="grid2"><div><label>Prospects' time zone</label><select name="timezone">{% for tz in timezones %}<option {{ 'selected' if c.schedule.timezone == tz }}>{{ tz }}</option>{% endfor %}</select></div><div></div>
 <div><label>From (hour)</label><select name="start_hour">{% for h in range(5, 21) %}<option value="{{ h }}" {{ 'selected' if c.schedule.start_hour == h }}>{{ '%02d' % h }}:00</option>{% endfor %}</select></div>
 <div><label>Until (hour)</label><select name="end_hour">{% for h in range(6, 23) %}<option value="{{ h }}" {{ 'selected' if c.schedule.end_hour == h }}>{{ '%02d' % h }}:00</option>{% endfor %}</select></div></div>
 <label>Days</label><div class="checks">{% for d, lbl in [(1,'Mon'),(2,'Tue'),(3,'Wed'),(4,'Thu'),(5,'Fri'),(6,'Sat'),(7,'Sun')] %}<label><input type="checkbox" name="days" value="{{ d }}" {{ 'checked' if d in c.schedule.days }}> {{ lbl }}</label>{% endfor %}</div>
 {% set gap = c.delay_between_sends_seconds or [60, 180] %}
 <div class="grid2"><div><label>Gap between emails, at least (seconds)</label><input name="gap_min" type="number" min="0" value="{{ gap[0] }}"></div><div><label>at most (seconds)</label><input name="gap_max" type="number" min="0" value="{{ gap[1] }}"></div></div>
 <button class="btn brand" style="margin-top:12px">Save schedule</button></form></div></div>

{% elif tab == 'handover' %}
<div class="card"><div class="card-h"><h2>Handover to a lead gen supervisor</h2></div><div class="card-b"><form method="post" action="{{ url_for('save_handover') }}">
 <p class="muted" style="margin-top:0">When a prospect replies <b>interested</b>, asks for a <b>meeting</b> or asks a <b>question</b>, BSO hands them to a supervisor (taking turns): the inbox that received the reply sends a short introduction with the supervisor in CC, the supervisor gets a briefing email with the whole conversation, and the prospect stops getting automatic emails.</p>
 <label class="check"><input type="checkbox" name="enabled" {{ 'checked' if ho.get('enabled', True) }}> Hand interested prospects over to a supervisor</label>
 <label class="check"><input type="checkbox" name="intro_enabled" {{ 'checked' if ho.get('intro_enabled', True) }}> Send the prospect a short introduction (supervisor in CC)</label>
 <label>Introduction email. <span class="muted">{first_name}, {company}, {sender_name}, {supervisor_name} and {supervisor_first_name} are filled in.</span></label>
 <textarea name="intro_template" style="min-height:190px;font-family:ui-monospace,Menlo,Consolas,monospace">{{ ho.get('intro_template') or ho_default }}</textarea>
 {% if team.joined %}<label>Supervisors (set by your manager in the Master Console)</label>
 {% if team_sups %}<ul>{% for sv in team_sups %}<li>{{ sv.name }} &lt;{{ sv.email }}&gt;</li>{% endfor %}</ul>{% else %}<p class="muted">None yet: until your manager adds a Lead Gen Supervisor, interested replies stay with you as tasks.</p>{% endif %}
 {% else %}<label>Supervisors, one per line: Name &lt;email&gt; <span class="muted">(prospects go to them in turn)</span></label>
 <textarea name="supervisors" style="min-height:80px" placeholder="Ali Khan <ali@blacswanken.com>">{% for sv in ho.get('supervisors') or [] %}{{ sv.name }} <{{ sv.email }}>
{% endfor %}</textarea>{% endif %}
 <p class="hint">Test mode: the introduction and the briefing both go to your test address instead.</p>
 <button class="btn brand" style="margin-top:12px">Save handover settings</button></form></div></div>
{% if team.joined %}<div class="card" id="away"><div class="card-h"><h2>Away</h2>{% if team.away_until %}<span class="badge b-warn">away until {{ team.away_until }}</span>{% endif %}</div><div class="card-b"><form method="post" action="{{ url_for('save_away') }}" style="display:flex;gap:10px;align-items:end;flex-wrap:wrap">
 <label class="check" style="margin:0"><input type="checkbox" name="away" {{ 'checked' if team.away_until }}> I'm away (on leave): don't give me new prospects</label>
 <div><label>Until (including)</label><input type="date" name="until" value="{{ team.away_until or '' }}"></div><button class="btn brand">Save</button></form>
 <p class="hint">Your current prospects stay with you; ask your manager to move any that need attention while you're away.</p></div></div>{% endif %}
<div class="card" id="mailbox"><div class="card-h"><h2>Your mailbox (supervisors)</h2>{% if pm.email and pm_pw %}<span class="badge b-ok">connected: {{ pm.email }}</span>{% endif %}</div><div class="card-b">
 <p class="muted" style="margin-top:0">If you receive handed-over prospects, connect the mailbox they're introduced to (the one in CC). BSO then shows every email with each prospect on their page in <b>My prospects</b>, including replies you send from your normal email app, lets you reply from BSO in the same thread, and flags new replies. It checks every few minutes while BSO is open.</p>
 <form method="post" action="{{ url_for('save_mailbox') }}"><div class="grid2">
  <div><label>Your email address</label><input name="email" type="email" value="{{ pm.email or '' }}" placeholder="ali@blacswanken.com" required></div>
  <div><label>Your name (what prospects see)</label><input name="name" value="{{ pm.name or '' }}" placeholder="Ali Khan"></div>
  <div><label>Password {% if pm_pw %}<span class="badge b-ok">saved {{ pm_pw }}</span>{% endif %}</label><input name="password" type="password" autocomplete="new-password" placeholder="{{ 'Leave blank to keep the saved one' if pm_pw else 'Mailbox password or app password' }}"></div>
  <div></div>
  <div><label>Sending server (SMTP)</label><input name="smtp_host" value="{{ pm.smtp_host or '' }}" placeholder="smtp.gmail.com" required></div>
  <div><label>SMTP port</label><input name="smtp_port" type="number" value="{{ pm.smtp_port or 465 }}"></div>
  <div><label>Receiving server (IMAP)</label><input name="imap_host" value="{{ pm.imap_host or '' }}" placeholder="imap.gmail.com" required></div>
  <div><label>IMAP port</label><input name="imap_port" type="number" value="{{ pm.imap_port or 993 }}"></div>
 </div>
 <p class="hint">Google Workspace / Gmail: smtp.gmail.com port 465, imap.gmail.com, and an <b>App password</b> (Google Account → Security → 2-Step Verification → App passwords). SiteGround: mail.yourdomain.com, ports 465 and 993, your mailbox password.</p>
 <button class="btn brand" style="margin-top:8px">Save &amp; test mailbox</button></form></div></div>

{% elif tab == 'team' %}
{% if team.joined %}
<div class="card"><div class="card-h"><h2>Your team account</h2></div><div class="card-b">
 <p style="margin-top:0"><b>{{ team.policy.org or 'Your team' }}</b> · {{ team.policy.name }} ({{ team.policy.role }}) · ID <b>{{ team.username }}</b><br>
 <span class="muted">Hub: {{ team.hub }} · last connected {{ team.last_ok or 'never' }}</span></p>
 {% if team.revoked %}<div class="help" style="border-color:#c23934">Your manager signed this laptop out. Sign in again with your ID and password.</div>{% endif %}
 <table class="tbl"><tr><td>Account</td><td>{{ 'Active' if team.policy.enabled else 'Paused by your manager' }}</td></tr>
 <tr><td>Daily send cap</td><td>{{ team.policy.daily_cap or 'No cap set' }}</td></tr>
 <tr><td>Weekly target</td><td>{{ team.policy.weekly_target or 'None set' }}</td></tr>
 <tr><td>Who approves your emails</td><td>{{ 'Your manager' if team.policy.approval == 'master' else 'You' }}</td></tr></table>
 <h3 style="margin-bottom:4px">What your manager can see</h3><ul>{% for line in team.disclosure %}<li>{{ line }}</li>{% endfor %}</ul>
 <div style="display:flex;gap:6px;flex-wrap:wrap"><form method="post" action="{{ url_for('team_sync') }}"><button class="btn">Check in now</button></form>
 <form method="post" action="{{ url_for('signout') }}"><button class="btn">Sign out</button></form>
 {% if not team.locked %}<form method="post" action="{{ url_for('team_leave') }}" onsubmit="return confirm('Leave this team? Your manager stops seeing new activity.')"><button class="btn danger">Leave the team</button></form>{% endif %}</div>
</div></div>
{% else %}
<div class="card"><div class="card-h"><h2>Team sign-in</h2></div><div class="card-b">
 <p style="margin-top:0">This copy of BSO is not part of a team. If your manager gave you an ID and password, sign in:</p>
 <a class="btn brand" href="{{ url_for('signin') }}">Sign in to your team</a></div></div>
{% endif %}
{% endif %}
</div></div>
{% endblock %}{% block scripts %}<script>
var PROV = {{ providers|tojson }};
document.querySelectorAll('select.prov').forEach(function(sel){ sel.onchange=function(){
  var n=sel.dataset.n, p=PROV[sel.value];
  if(p.smtp_host){ document.getElementById('smtp'+n).value=p.smtp_host; document.getElementById('imap'+n).value=p.imap_host; document.getElementById('port'+n).value=p.smtp_port; }
  document.getElementById('hint'+n).textContent=p.help;
  if(sel.value==='other') sel.closest('fieldset').querySelector('details').open=true;
}});
</script>{% endblock %}"""

WELCOME = r"""{% extends 'base.html' %}{% block title %}Welcome to BSO{% endblock %}{% block body %}
<div class="card" style="max-width:760px;margin:24px auto"><div class="card-b" style="padding:28px 32px">
 <div style="display:flex;gap:14px;align-items:center"><span style="flex:none;display:flex">{{ logo(64, tile=True, uid="wl", animated=True, label="BSO")|safe }}</span>
 <div><h1 style="font-size:22px">Welcome to BSO</h1><div class="muted">by {{ publisher }} · version {{ app_version }}</div></div></div>
 <p style="margin-top:18px">Before you start, please read and accept the terms. Short version:</p>
 <ul style="line-height:1.8">
  <li><b>Your data stays on this computer.</b> {{ publisher }} never receives it. No accounts, no tracking.</li>
  <li><b>You are the sender.</b> Emails go out from your own inbox, and you're responsible for following anti-spam and privacy laws.</li>
  <li><b>Nothing is sent until you approve it.</b> Test mode and review mode are on from the start.</li>
  <li>Data is shared only with services you choose to connect (your email provider, and optionally Anthropic, Apollo and Slack).</li>
 </ul>
 <p><a href="{{ url_for('legal_doc', doc='terms') }}" target="_blank">Terms of Use</a> · <a href="{{ url_for('legal_doc', doc='privacy') }}" target="_blank">Privacy Policy</a> · <a href="{{ url_for('legal_doc', doc='acceptable-use') }}" target="_blank">Acceptable Use Policy</a> · <a href="{{ url_for('legal_doc', doc='third-party') }}" target="_blank">Third-Party Notices</a></p>
 {% if accepted %}<div class="toast">You've accepted these terms. <a href="{{ url_for('home') }}" style="color:#fff;text-decoration:underline">Go to the app</a></div>{% else %}
 <form method="post" action="{{ url_for('accept_terms') }}">
  <label style="display:flex;gap:10px;align-items:flex-start;color:var(--ink);font-size:14px;margin-top:16px"><input type="checkbox" name="agree" value="1" style="width:auto;margin-top:3px">
  <span>I have read and accept the Terms of Use and the Acceptable Use Policy, and I have read the Privacy Policy.</span></label>
  <button class="btn brand" style="margin-top:16px;height:38px;padding:0 22px">Accept and continue</button>
  <span class="muted" style="margin-left:10px">Don't agree? Close the app and uninstall it.</span>
 </form>{% endif %}
</div></div>
{% endblock %}"""

LEGAL = r"""{% extends 'base.html' %}{% block title %}{{ title }}{% endblock %}{% block body %}
<div class="card legal" style="max-width:900px;margin:0 auto"><div class="card-b" style="padding:24px 32px">
<p class="muted" style="margin-top:0"><a href="{{ url_for('about') }}">← About &amp; legal</a></p>
{% if body %}{{ body|safe }}{% else %}<h1>{{ title }}</h1><p class="muted">BSO includes the open-source software listed below. Each component is licensed under its own terms, reproduced here.</p><pre class="log" style="background:#f8f8f8;color:var(--ink);max-height:none">{{ text }}</pre>{% endif %}
</div></div>
{% endblock %}"""

ABOUT = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}About{% endblock %}{% block body %}
{% call m.header('t-setup', 'i', 'About', 'BSO') %}{% endcall %}
<div class="cols"><div>
 <div class="card"><div class="card-h"><h2>About this app</h2></div><div class="card-b"><table class="tbl">
  <tr><td class="muted" style="width:180px">Product</td><td>BSO – BlacSwanken Outreach</td></tr>
  <tr><td class="muted">Version</td><td>{{ version }}</td></tr>
  <tr><td class="muted">Publisher</td><td>{{ lg.company_name }}</td></tr>
  <tr><td class="muted">Contact</td><td>{{ lg.contact_email }}</td></tr>
  <tr><td class="muted">Terms accepted</td><td>{{ accepted_at[:16].replace('T', ' ') ~ ' UTC' if accepted_at else 'Not yet' }}</td></tr>
 </table></div></div>
 <div class="card"><div class="card-h"><h2>Your data</h2></div><div class="card-b">
  <p style="margin-top:0">Everything is stored on this computer, in:<br><code>{{ data_dir }}</code></p>
  <p>Passwords and API keys: {% if encrypted %}<span class="badge b-ok">encrypted with Windows Data Protection</span>{% else %}<span class="badge b-warn">stored with owner-only file permissions (development mode)</span>{% endif %}</p>
  <form method="post" action="{{ url_for('open_data_folder') }}"><button class="btn">Open data folder</button> <a class="btn" href="{{ url_for('export') }}">Export leads (CSV)</a></form>
  <p class="hint">Back up <code>outreach.db</code> from that folder to keep a copy of your CRM.</p></div></div>
</div><div>
 <div class="card"><div class="card-h"><h2>Legal</h2></div><div class="card-b" style="line-height:2">
  <a href="{{ url_for('legal_doc', doc='terms') }}">Terms of Use</a><br>
  <a href="{{ url_for('legal_doc', doc='privacy') }}">Privacy Policy</a><br>
  <a href="{{ url_for('legal_doc', doc='acceptable-use') }}">Acceptable Use Policy</a><br>
  <a href="{{ url_for('legal_doc', doc='third-party') }}">Third-Party Notices</a></div></div>
 <div class="card"><div class="card-b muted" style="font-size:12px">© 2026 {{ lg.company_name }}. All rights reserved. BSO is not affiliated with Salesforce, Microsoft, Google, Anthropic or Apollo.io.</div></div>
</div></div>
{% endblock %}"""

CAMPAIGNS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Campaigns{% endblock %}{% block body %}
{% call m.header('t-rev', '⚑', 'Campaigns', items|length ~ ' campaign(s)') %}{% endcall %}
<div class="help" style="margin-bottom:12px">Each campaign has its own emails, its own inboxes and its own leads. Use one per offer or audience (for example <i>CTOs, US fintech</i> and <i>Heads of Product, UK SaaS</i>). Imports, emailed lead files and Instantly imports each go into the campaign you choose.</div>
<div class="card"><table class="tbl"><thead><tr><th>Campaign</th><th>Status</th><th class="r">Leads</th><th class="r">Not yet emailed</th><th class="r">In sequence</th><th class="r">Sent</th><th class="r">Reply rate</th><th class="r">Positive</th><th class="r">Handed over</th><th class="r">Bounces</th></tr></thead>
{% for c in items %}<tr><td><a href="{{ url_for('campaign', cid=c.id) }}"><b>{{ c.name }}</b></a><br><small class="muted">{{ c.inbox_list|length ~ ' inbox(es)' if c.inbox_list else 'all inboxes' }}</small></td>
<td>{{ m.status_badge('active' if c.status == 'active' else 'paused', {'active':'Running','paused':'Paused'}) }}</td><td class="r"><a href="{{ url_for('leads', campaign=c.id) }}">{{ c.st.leads }}</a></td><td class="r">{{ c.st.waiting }}</td><td class="r">{{ c.st.in_sequence }}</td>
<td class="r">{{ c.st.sent }}</td><td class="r">{{ c.st.rate }}</td><td class="r">{{ c.st.positive }} <small class="muted">({{ c.st.pos_rate }})</small></td><td class="r">{{ c.st.handed }}</td><td class="r">{{ c.st.bounces }}</td></tr>{% endfor %}</table></div>
<div class="card" style="max-width:640px"><div class="card-h"><h2>New campaign</h2></div><div class="card-b"><form method="post" action="{{ url_for('campaign_new') }}"><div class="grid2">
<div><label>Name</label><input name="name" required placeholder="e.g. Heads of Product, UK SaaS"></div>
<div><label>Start from the emails of</label><select name="copy_from">{% for c in items %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></div></div>
<button class="btn brand" style="margin-top:12px">Create campaign</button></form></div></div>{% endblock %}"""

CAMPAIGN = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}{{ c.name }}{% endblock %}{% block body %}
{% call m.header('t-rev', '⚑', 'Campaign', c.name) %}
 <form method="post" style="display:inline">{% if c.status == 'active' %}<button class="btn" name="action" value="pause">Pause campaign</button>{% else %}<button class="btn brand" name="action" value="resume">▶ Start campaign</button>{% endif %}</form>
 <a class="btn" href="{{ url_for('leads', campaign=c.id) }}">Its leads ({{ st.leads }})</a>
{% endcall %}
<div class="stats" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px">{% for k, l in [('waiting','Not yet emailed'),('in_sequence','In sequence'),('sent','Emails sent'),('rate','Reply rate'),('positive','Positive replies'),('handed','Handed over'),('bounces','Bounces')] %}<div class="card" style="margin:0"><div class="card-b"><small class="muted">{{ l }}</small><div style="font-size:22px">{{ st[k] }}</div></div></div>{% endfor %}</div>
<form method="post"><input type="hidden" name="action" value="save">
<div class="card"><div class="card-h"><h2>Settings</h2>{{ m.status_badge('active' if c.status == 'active' else 'paused', {'active':'Running','paused':'Paused'}) }}</div><div class="card-b">
 <label>Name</label><input name="name" value="{{ c.name }}">
 <label>Send from these inboxes <span class="muted">(none ticked = all inboxes)</span></label><div class="checks">{% for e in inboxes %}<label><input type="checkbox" name="inboxes" value="{{ e }}" {{ 'checked' if e in picked }}> {{ e }}</label>{% endfor %}</div>
 {% if c.id != 1 %}<label>What we offer, for the AI-written first line <span class="muted">(empty = same as the Main campaign)</span></label><textarea name="offer" style="min-height:70px" placeholder="{{ main_offer }}">{{ c.offer or '' }}</textarea>{% endif %}
</div></div>
{% if c.id == 1 %}<div class="card"><div class="card-b">The Main campaign's emails (with A/B variants) are edited in <a href="{{ url_for('settings', tab='emails') }}">Settings → Your offer &amp; emails</a>.</div></div>
{% else %}<div class="card" id="emails"><div class="card-h"><h2>Emails</h2><span class="muted">{first_name}, {company}, {personal_line}, {sender_name} are filled in for each lead</span></div><div class="card-b">
 {% for st in steps %}{% set i = loop.index0 %}<fieldset class="ibox"><legend>{{ 'First email' if i == 0 else 'Follow-up ' ~ i }}</legend>
  {% if i > 0 %}<div style="max-width:240px"><label>Days after the previous email</label><input name="delay{{ i }}" type="number" min="1" value="{{ st.delay_days }}"></div>{% else %}<input type="hidden" name="delay0" value="0">{% endif %}
  <label>Subject {% if i > 0 %}<span class="muted">(empty = reply in the same thread)</span>{% endif %}</label><input name="subject{{ i }}" value="{{ st.subject }}">
  <label>Body</label><textarea name="body{{ i }}" style="min-height:170px;font-family:ui-monospace,Menlo,Consolas,monospace">{{ st.body }}</textarea>
  {% if steps|length > 1 %}<label class="check"><input type="checkbox" name="remove{{ i }}"> Remove this email</label>{% endif %}</fieldset>{% endfor %}
 <button class="btn" name="action" value="add_step">+ Add a follow-up</button></div></div>{% endif %}
<button class="btn brand">Save campaign</button></form>
{% if c.id != 1 %}<form method="post" style="margin-top:20px" onsubmit="return confirm('Delete this campaign? Its leads move to the Main campaign.')"><button class="btn danger" name="action" value="delete">Delete campaign</button></form>{% endif %}
{% endblock %}"""

PROSPECTS = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}My prospects{% endblock %}{% block body %}
{% call m.header('t-opp', '★', 'My prospects', items|length ~ ' handed over to you · ' ~ counts['new'] ~ ' not contacted yet') %}
 <a class="btn {{ 'brand' if view == 'board' }}" href="{{ url_for('prospects', view='board') }}">Board</a><a class="btn {{ 'brand' if view == 'list' }}" href="{{ url_for('prospects', view='list') }}">List</a>
{% endcall %}
{% if away %}<div class="help" style="margin-bottom:12px;border-color:#dd7a01">You're marked <b>away until {{ away }}</b>: no new prospects come to you. <a href="{{ url_for('settings', tab='handover') }}#away">Change</a></div>{% endif %}
{% if mentions %}<div class="card" style="border-left:4px solid #0176d3"><div class="card-h"><h2>You were mentioned</h2></div><div class="card-b">
{% for c in mentions %}<div style="margin-bottom:10px"><b>{{ c.author_name }}</b> on <b>{{ c.lead_name or c.lead_email }}</b>{% if c.company %} ({{ c.company }}){% endif %} <span class="muted">{{ c.at[:16].replace('T', ' ') }}</span>
<div style="white-space:pre-wrap">{{ c.text }}</div>
<form method="post" action="{{ url_for('comment_add') }}" style="display:flex;gap:6px;margin-top:4px"><input type="hidden" name="lead_email" value="{{ c.lead_email }}"><input type="hidden" name="lead_name" value="{{ c.lead_name }}"><input type="hidden" name="company" value="{{ c.company }}"><input type="hidden" name="back" value="{{ url_for('prospects') }}">
<input name="text" placeholder="Reply…" required style="flex:1"><button class="btn sm">Reply</button></form>
<form method="post" action="{{ url_for('comments_seen') }}" style="margin-top:2px"><input type="hidden" name="lead_email" value="{{ c.lead_email }}"><button class="btn sm">Mark as read</button></form></div>{% endfor %}</div></div>{% endif %}
{% if not mailbox_ok %}<div class="help" style="margin-bottom:12px;border-color:#dd7a01">⚠️ <b>Connect your mailbox</b> in <a href="{{ url_for('settings', tab='handover') }}#mailbox">Settings → Handover</a> so each prospect's emails appear here and you can reply from BSO.</div>
{% else %}<form method="post" action="{{ url_for('mailbox_check') }}" style="margin:0 0 10px"><button class="btn">↻ Check mailbox now</button> <span class="muted">{{ unseen_total }} new repl{{ 'y' if unseen_total == 1 else 'ies' }}</span></form>{% endif %}
{% if view == 'board' %}
<div id="board" style="display:grid;grid-template-columns:repeat(6,minmax(160px,1fr));gap:10px;overflow-x:auto">
{% for k, lbl in labels.items() %}<div class="col" data-stage="{{ k }}" style="background:#eef1f4;border-radius:8px;padding:8px;min-height:240px"><div style="font-weight:600;margin:2px 4px 8px">{{ lbl.split(':')[0] }} <span class="muted">{{ counts[k] }}</span></div>
{% for p in items if (p.prospect_stage or 'new') == k %}<div class="kcard" draggable="true" data-id="{{ p.id }}" style="background:#fff;border:1px solid var(--line);{{ 'border-left:4px solid #2e844a;' if p.unseen or p.new_comments else ('border-left:4px solid #ba0517;' if k == 'new' and p.age >= 2 else '') }}border-radius:6px;padding:8px 10px;margin-bottom:8px;cursor:grab">
<a href="{{ url_for('lead', lead_id=p.id) }}"><b>{{ p.name }}</b></a><br><small class="muted">{{ p.company or '' }}</small><br>
<small>{{ p.age }}d{% if p.unseen %} · <b style="color:#2e844a">● {{ p.unseen }} new</b>{% endif %}{% if p.ncomments %} · 💬 {{ p.ncomments }}{% if p.new_comments %} <b style="color:#2e844a">new</b>{% endif %}{% endif %}</small></div>{% endfor %}</div>{% endfor %}</div>
<form id="mv" method="post" style="display:none"><input type="hidden" name="stage"><input type="hidden" name="back" value="{{ url_for('prospects', view='board') }}"></form>
<p class="muted">Drag a card to another column to change its stage. Red edge: not contacted for 2+ days. Green edge: new reply or comment.</p>
<script>
var drag=null;document.querySelectorAll('.kcard').forEach(function(c){c.addEventListener('dragstart',function(){drag=c;c.style.opacity=.5});c.addEventListener('dragend',function(){c.style.opacity=1})});
document.querySelectorAll('#board .col').forEach(function(col){col.addEventListener('dragover',function(e){e.preventDefault();col.style.background='#dbe7f3'});
col.addEventListener('dragleave',function(){col.style.background='#eef1f4'});
col.addEventListener('drop',function(e){e.preventDefault();col.style.background='#eef1f4';if(!drag)return;var st=col.dataset.stage;
if((st==='won'||st==='lost')&&!confirm('Move this prospect to '+(st==='won'?'Won':'Lost')+'?'))return;
var f=document.getElementById('mv');f.action='/prospects/'+drag.dataset.id+'/stage';f.stage.value=st;f.submit()})});
</script>
{% else %}
<div class="card">{% if items %}<table class="tbl"><thead><tr><th>Prospect</th><th>Company</th><th>Conversation</th><th>Handed over by</th><th>Stage</th></tr></thead>
{% for p in items %}<tr><td><a href="{{ url_for('lead', lead_id=p.id) }}"><b>{{ p.name }}</b></a><br><small class="muted">{{ p.x.title or '' }}<br>{{ p.email }}</small></td>
<td>{{ p.company or '' }}{% if p.website %}<br><a href="https://{{ p.website }}" target="_blank" rel="noopener"><small>{{ p.website }} ↗</small></a>{% endif %}{% if p.x.linkedin %} <a href="{{ p.x.linkedin }}" target="_blank" rel="noopener"><small>LinkedIn ↗</small></a>{% endif %}</td>
<td>{% if p.unseen %}<span class="badge b-ok">● {{ p.unseen }} new repl{{ 'y' if p.unseen == 1 else 'ies' }}</span><br>{% endif %}{% if p.new_comments %}<span class="badge b-info">💬 new comment</span><br>{% endif %}<small class="muted">{{ (p.x.handover_label or '').replace('_', ' ') }}{% if p.last_msg %}<br>last email {{ p.last_msg[:10] }} ({{ 'from them' if p.last_dir == 'in' else 'from you' }}){% endif %}</small></td><td>{{ p.handed_from }}<br><small class="muted">{{ (p.handed_at_in or '')[:10] }}</small></td>
<td><form method="post" action="{{ url_for('prospect_stage', lead_id=p.id) }}"><input type="hidden" name="back" value="{{ url_for('prospects', view='list') }}"><select name="stage" onchange="if((this.value==='won'||this.value==='lost')&&!confirm('Move this prospect to '+this.options[this.selectedIndex].text+'?')){this.form.reset();return}this.form.submit()">{% for k, l in labels.items() %}<option value="{{ k }}" {{ 'selected' if (p.prospect_stage or 'new') == k }}>{{ l }}</option>{% endfor %}</select></form></td></tr>{% endfor %}</table>
{% else %}<div class="empty"><b>No prospects yet</b>When a prospect replies with interest, they're handed over to you here, and you also get a briefing email.</div>{% endif %}</div>{% endif %}
{% endblock %}"""

SIGNIN = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · BlacSwanken Outreach</title><link rel="icon" href="{{ url_for('favicon') }}"><link rel="manifest" href="{{ url_for('web_manifest') }}"><meta name="theme-color" content="#1B1F24">
<style>
*{box-sizing:border-box}html,body{height:100%}body{margin:0;font:15px/1.45 system-ui,'Segoe UI',sans-serif;color:#181818;background:#1B1F24;display:flex;align-items:center;justify-content:center;padding:16px}
.box{background:#fff;border-radius:12px;width:100%;max-width:420px;padding:32px 32px 24px;box-shadow:0 20px 60px rgba(0,0,0,.35)}
.hd{display:flex;flex-direction:column;align-items:center;text-align:center;margin-bottom:20px}.hd h1{font-size:22px;margin:14px 0 2px}.hd span{color:#5c5c5c;font-size:13px}
label{display:block;font-size:12px;color:#5c5c5c;margin:12px 0 4px}input[type=text],input[type=password],input[name=hub]{width:100%;padding:10px 12px;border:1px solid #c9c7c5;border-radius:6px;font:inherit}
input:focus{outline:2px solid #0176d3;outline-offset:-1px;border-color:#0176d3}
.btn{display:block;width:100%;margin-top:18px;padding:11px;border:0;border-radius:6px;background:#0176d3;color:#fff;font:600 15px system-ui,'Segoe UI',sans-serif;cursor:pointer}.btn:hover{background:#015ba7}
.err{background:#fde1e1;border:1px solid #e9a0a0;color:#7b1018;border-radius:6px;padding:10px 12px;margin-bottom:6px;font-size:14px}
details{margin-top:14px;font-size:13px;color:#3e3e3c}summary{cursor:pointer;color:#0176d3}details ul{padding-left:18px;margin:8px 0}
.chk{display:flex;gap:8px;align-items:flex-start;margin-top:12px;font-size:13px;color:#181818}.chk input{margin-top:3px}
.ft{text-align:center;color:#706e6b;font-size:12px;margin-top:18px}.ft form{display:inline}.ft button{background:none;border:0;color:#706e6b;text-decoration:underline;cursor:pointer;font:inherit;padding:0}
{{ logo_css|safe }}
</style></head><body>
<div class="box">
 <div class="hd">{{ logo(72, tile=True, uid="si", animated=True, label="BSO")|safe }}<h1>BlacSwanken Outreach</h1><span>Sign in with the ID and password from your admin</span></div>
 {% if err %}<div class="err">{{ err }}</div>{% endif %}
 <form method="post" action="{{ url_for('signin') }}"><input type="hidden" name="next" value="{{ nxt }}">
  <label for="u">ID</label><input id="u" type="text" name="username" value="{{ last_user }}" autocomplete="username" autocapitalize="none" spellcheck="false" required {{ '' if last_user else 'autofocus' }}>
  <label for="p">Password</label><input id="p" type="password" name="password" autocomplete="current-password" required {{ 'autofocus' if last_user else '' }}>
  {% if not hub_fixed %}{% if hub_known %}<details><summary>Team address: {{ hub }}</summary><label>Change it only if your admin tells you to</label><input name="hub" value="{{ hub }}"></details>
  {% else %}<label>Team address <span style="color:#706e6b">(from your admin; leave empty on the computer running the Master Console)</span></label><input name="hub" value="" placeholder="https://script.google.com/macros/s/…/exec">{% endif %}{% endif %}
  {% if first %}<details open><summary>What your manager can see</summary><ul>{% for line in disclosure %}<li>{{ line }}</li>{% endfor %}</ul></details>
  <label class="chk"><input type="checkbox" name="consent" value="1"> I've read this and I agree.</label>
  {% else %}<details><summary>What your manager can see</summary><ul>{% for line in disclosure %}<li>{{ line }}</li>{% endfor %}</ul></details>{% endif %}
  <button class="btn">Sign in</button></form>
 <div class="ft">Forgotten your password? Ask your admin to reset it.<br><br>
  <form method="post" action="{{ url_for('quit_app') }}"><button>Quit BSO</button></form> · <a href="{{ url_for('about') }}" style="color:#706e6b">About &amp; legal</a></div>
</div>
<script>
function beat(){ try{ fetch('/heartbeat',{method:'POST',keepalive:true}); }catch(e){} }
beat(); setInterval(beat, 10000);
document.addEventListener('visibilitychange', function(){ if(!document.hidden) beat(); });
window.addEventListener('focus', beat);
window.addEventListener('pagehide', function(){ try{ navigator.sendBeacon('/bye'); }catch(e){} });
</script></body></html>"""


TEMPLATES = {
    "base.html": BASE, "macros.html": MACROS, "home.html": HOME, "leads.html": LEADS, "lead.html": LEAD,
    "accounts.html": ACCOUNTS, "account.html": ACCOUNT, "opps.html": OPPS, "opp.html": OPP,
    "tasks.html": TASKS, "reports.html": REPORTS, "review.html": REVIEW, "setup.html": SETUP,
    "job.html": JOB, "settings.html": SETTINGS, "welcome.html": WELCOME, "signin.html": SIGNIN, "prospects.html": PROSPECTS, "campaigns.html": CAMPAIGNS, "campaign.html": CAMPAIGN, "legal.html": LEGAL, "about.html": ABOUT, "jobs.html": JOBS, "new.html": NEW_LEAD, "import.html": IMPORT, "search.html": SEARCH,
}
