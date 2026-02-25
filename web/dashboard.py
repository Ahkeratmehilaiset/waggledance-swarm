"""
WaggleDance Swarm AI — Dashboard v0.1.0
=========================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v0.1.0 • Built: 2026-02-24

v0.1.0 (Phase 3):
  - Round Table card (streaming discussion + synthesis)
  - Agent Level badges (L1-L5) in agent grid
  - Night mode moon indicator in topbar
  - /api/agent_levels endpoint
  - WebSocket handlers: round_table_*, night_learning

v0.0.3:
  - UTF-8 charset meta
  - Header: mallitiedot + dynaaminen GPU/CPU
  - Title: kirkas + (on-prem) himmeä
  - /api/system endpoint
  - Throttle stats
"""
import json
import asyncio
import time
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, JSONResponse


def _get_seasonal_focus():
    """Get current month's seasonal keywords from consciousness module."""
    try:
        from consciousness import SEASONAL_BOOST
        return SEASONAL_BOOST.get(datetime.now().month, [])
    except ImportError:
        return []


def create_app(hivemind):
    app = FastAPI(title="WaggleDance Swarm AI Dashboard")

    # ── UTF-8 kaikkialle (Windows-fix) ────────────────────
    from starlette.middleware.base import BaseHTTPMiddleware

    class UTF8Middleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            ct = response.headers.get("content-type", "")
            if "json" in ct and "charset" not in ct:
                response.headers["content-type"] = ct + "; charset=utf-8"
            return response

    app.add_middleware(UTF8Middleware)

    chat_model = hivemind.llm.model if hivemind.llm else "?"
    hb_model = (hivemind.llm_heartbeat.model
                if hivemind.llm_heartbeat else chat_model)

    @app.get("/")
    async def index():
        swarm_badge = ("ENABLED" if hivemind._swarm_enabled
                       else "DISABLED")
        swarm_color = ("#3fb950" if hivemind._swarm_enabled
                       else "#f85149")

        html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>WaggleDance Swarm AI (on-prem)</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:'Cascadia Mono','Fira Code',monospace;background:#0d1117;color:#e6edf3;margin:0;padding:0}}
  .topbar{{
    background:linear-gradient(135deg,#161b22 0%,#0d1117 100%);
    border-bottom:1px solid #30363d;
    padding:12px 24px;display:flex;align-items:center;justify-content:space-between;
  }}
  .topbar-left,.topbar-right{{display:flex;align-items:center;gap:10px;font-size:11px;color:#8b949e;min-width:260px}}
  .topbar-right{{justify-content:flex-end}}
  .topbar-center{{text-align:center;flex:1}}
  .topbar-center h1{{margin:0;font-size:22px;letter-spacing:0.5px}}
  .topbar-center h1 .t-main{{color:#f0b429;font-weight:700}}
  .topbar-center h1 .t-sub{{color:#6e7681;font-weight:400;font-size:13px;font-style:italic}}
  .topbar-center .sub2{{color:#484f58;font-size:10px;margin-top:2px}}
  .mbadge{{
    background:#21262d;border:1px solid #30363d;border-radius:6px;
    padding:5px 10px;font-size:11px;white-space:nowrap;
  }}
  .mbadge .lb{{color:#8b949e}}
  .mbadge .vl{{color:#79c0ff;font-weight:600}}
  .gbar{{display:inline-block;width:48px;height:7px;background:#21262d;border-radius:4px;margin:0 4px;overflow:hidden;vertical-align:middle}}
  .gfill{{height:100%;border-radius:4px;transition:width 1s ease}}
  .sbadge{{
    display:inline-block;background:{swarm_color}22;color:{swarm_color};
    border:1px solid {swarm_color}44;border-radius:4px;
    padding:2px 8px;font-size:10px;font-weight:600;
  }}
  .night-badge{{
    display:none;background:#d2992222;color:#d29922;
    border:1px solid #d2992244;border-radius:4px;
    padding:2px 8px;font-size:10px;font-weight:600;margin-left:6px;
  }}
  .container{{max-width:1500px;margin:0 auto;padding:14px 20px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}}
  h2{{color:#79c0ff;margin:0 0 10px;font-size:14px;font-weight:600}}
  .feed{{max-height:300px;overflow-y:auto;font-size:11.5px;line-height:1.8}}
  .feed div{{border-bottom:1px solid #21262d;padding:3px 0}}
  input{{background:#0d1117;border:1px solid #30363d;color:#e6edf3;border-radius:6px;padding:8px;width:100%}}
  button{{background:#238636;color:white;border:none;border-radius:6px;padding:8px 16px;cursor:pointer;margin:4px;font-size:12px}}
  button:hover{{background:#2ea043}}
  .stat{{display:inline-block;background:#21262d;border-radius:4px;padding:4px 8px;margin:2px;font-size:11px}}
  .role-scout{{border-left:3px solid #58a6ff}}
  .role-worker{{border-left:3px solid #3fb950}}
  .role-judge{{border-left:3px solid #d29922}}
  #livefeed div{{animation:fadeIn .3s}}
  @keyframes fadeIn{{from{{opacity:0}}to{{opacity:1}}}}
  .twarn{{color:#f85149;font-size:10px;padding:2px 6px;background:#f8514922;border-radius:3px}}
  .lvl-badge{{display:inline-block;border-radius:3px;padding:1px 5px;font-size:9px;font-weight:700;margin-left:4px}}
  .lvl-1{{background:#21262d;color:#8b949e}}
  .lvl-2{{background:#0d419d33;color:#58a6ff}}
  .lvl-3{{background:#23863633;color:#3fb950}}
  .lvl-4{{background:#d2992233;color:#d29922}}
  .lvl-5{{background:#f0b42933;color:#f0b429;border:1px solid #f0b42966}}
  .rt-card{{display:none;border:1px solid #d2992244;background:#161b22}}
  .rt-topic{{color:#d29922;font-size:12px;margin-bottom:8px}}
  .rt-feed{{max-height:250px;overflow-y:auto;font-size:11px;line-height:1.7}}
  .rt-feed div{{padding:4px 0;border-bottom:1px solid #21262d}}
  .rt-synthesis{{margin-top:8px;padding:8px;border:1px solid #f0b42966;border-radius:6px;background:#f0b42911;font-size:11px}}
</style></head>
<body>

<div class="topbar">
  <div class="topbar-left">
    <div class="mbadge">
      <span class="lb">Chat:</span>
      <span class="vl">{chat_model}</span>
      <span class="lb" style="margin-left:6px">GPU</span>
      <span class="gbar"><span class="gfill" id="gpu-chat" style="width:0%;background:#3fb950"></span></span>
      <span id="gpu-chat-pct" style="color:#3fb950;font-size:10px">—</span>
    </div>
  </div>
  <div class="topbar-center">
    <h1>🐝 <span class="t-main">WaggleDance Swarm AI</span> <span class="t-sub">(on-prem)</span></h1>
    <div class="sub2">Jani Korpi (Ahkerat Mehiläiset) • v0.1.0 • <span class="sbadge">SWARM {swarm_badge}</span><span id="night-badge" class="night-badge">🌙 NIGHT</span><span id="corrections-badge" style="display:none;background:#da368822;color:#da3688;border:1px solid #da368844;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:600;margin-left:6px">📝 0</span></div>
  </div>
  <div class="topbar-right">
    <div class="mbadge">
      <span class="gbar"><span class="gfill" id="gpu-hb" style="width:0%;background:#d29922"></span></span>
      <span id="gpu-hb-pct" style="color:#d29922;font-size:10px">—</span>
      <span class="lb" style="margin-left:6px">CPU</span>
      <span class="vl">{hb_model}</span>
      <span class="lb">:Heartbeat</span>
    </div>
  </div>
</div>

<div class="container">
<div class="grid">
  <div class="card">
    <h2>💬 Chat</h2>
    <div id="chatlog" class="feed" style="min-height:220px"></div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <input id="chatinput" placeholder="Kirjoita viesti..." onkeypress="if(event.key==='Enter')sendChat()">
      <button onclick="sendChat()">Lähetä</button>
    </div>
  </div>
  <div class="card">
    <h2>📡 Live Feed <span id="timeout-count" class="twarn" style="display:none"></span></h2>
    <div id="livefeed" class="feed"></div>
  </div>
  <div class="card">
    <h2>🤖 Agentit &amp; Roolit <span id="agent-count" style="color:#484f58;font-weight:400"></span></h2>
    <div id="agents"></div>
    <button onclick="loadStatus()" style="margin-top:8px">🔄 Päivitä</button>
    <h2 style="margin-top:14px">🏆 Token Economy</h2>
    <div id="leaderboard"></div>
  </div>
  <div class="card">
    <h2>📊 Swarm Stats</h2>
    <div id="swarm"></div>
    <h2 style="margin-top:14px">⚙️ Throttle</h2>
    <div id="throttle"></div>
    <h2 style="margin-top:14px">🤖 OpsAgent</h2>
    <div id="opsagent"></div>
    <h2 style="margin-top:14px">🧬 Oppiminen</h2>
    <div id="learning"></div>
  </div>
  <div class="card">
    <h2>🧠 Tietoisuus (Phase 4)</h2>
    <div id="consciousness-stats"></div>
  </div>
  <div class="card">
    <h2>📝 Korjaukset &amp; Oppiminen</h2>
    <div id="corrections-feed" class="feed" style="max-height:200px"></div>
  </div>
  <div class="card">
    <h2>📡 Data Feeds (Phase 8)</h2>
    <div id="feeds-status"></div>
    <button onclick="loadFeeds()" style="margin-top:8px">🔄 Päivitä</button>
  </div>
  <div id="rt-card" class="card rt-card" style="grid-column:1/3">
    <h2>🏛️ Round Table <span id="rt-status" style="color:#484f58;font-weight:400"></span></h2>
    <div id="rt-topic" class="rt-topic"></div>
    <div id="rt-feed" class="rt-feed"></div>
    <div id="rt-synthesis" class="rt-synthesis" style="display:none"></div>
  </div>
</div>
</div>

<script>
let toCount=0;
let agentLevels={{}};
const ws=new WebSocket(`ws://${{location.host}}/ws`);
ws.onmessage=e=>{{
  const msg=JSON.parse(e.data);
  const d=msg.data||msg;
  const tp=msg.type||d.type||'';

  // Round Table events
  if(tp==='round_table_start'){{
    const rtc=document.getElementById('rt-card');
    rtc.style.display='block';
    document.getElementById('rt-topic').textContent='Aihe: '+(d.topic||'');
    document.getElementById('rt-feed').innerHTML='';
    document.getElementById('rt-synthesis').style.display='none';
    document.getElementById('rt-status').textContent='(käynnissä...)';
    return;
  }}
  if(tp==='round_table_insight'){{
    const feed=document.getElementById('rt-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#d29922">[${{d.agent_type||d.agent}}]</span> ${{d.response}}`;
    feed.appendChild(div);
    feed.scrollTop=feed.scrollHeight;
    return;
  }}
  if(tp==='round_table_synthesis'){{
    const syn=document.getElementById('rt-synthesis');
    syn.style.display='block';
    syn.innerHTML=`<strong style="color:#f0b429">Synteesi:</strong> ${{d.synthesis}}`;
    document.getElementById('rt-status').textContent=`(${{d.agent_count}} agenttia)`;
    return;
  }}
  if(tp==='round_table_end'){{
    document.getElementById('rt-status').textContent=`(valmis, ${{d.agent_count}} agenttia)`;
    return;
  }}

  // Night mode
  if(tp==='night_learning'){{
    const nb=document.getElementById('night-badge');
    nb.style.display='inline';
    nb.textContent=`🌙 NIGHT (${{d.facts_learned||0}})`;
  }}

  // Phase 4: Correction stored
  if(tp==='correction_stored'){{
    const cf=document.getElementById('corrections-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#da3688">📝</span> Korjaus: ${{d.query||''}} → ${{d.good_answer||''}}`;
    cf.prepend(div);
    if(cf.children.length>20)cf.lastChild.remove();
    const cb=document.getElementById('corrections-badge');
    cb.style.display='inline';
    const n=parseInt(cb.textContent.match(/\\d+/)||[0])+1;
    cb.textContent=`📝 ${{n}}`;
  }}

  // Phase 8: Feed update
  if(tp==='feed_update'){{
    const cf=document.getElementById('corrections-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#58a6ff">📡</span> ${{d.feed||'feed'}}: ${{d.facts_stored||0}} facts`;
    cf.prepend(div);
    if(cf.children.length>20)cf.lastChild.remove();
  }}

  // Phase 4k: Enrichment
  if(tp==='enrichment'){{
    const cf=document.getElementById('corrections-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#f0b429">✨</span> Enrichment: ${{d.facts_stored||0}} facts (${{d.total_enriched||0}} total)`;
    cf.prepend(div);
    if(cf.children.length>20)cf.lastChild.remove();
  }}

  // Phase 9: Web learning
  if(tp==='web_learning'){{
    const cf=document.getElementById('corrections-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#58a6ff">🌐</span> Web: ${{d.facts_stored||0}} facts (${{d.total_web||0}} total, ${{d.searches_today||0}} searches)`;
    cf.prepend(div);
    if(cf.children.length>20)cf.lastChild.remove();
  }}

  // Phase 9: Distillation
  if(tp==='distillation'){{
    const cf=document.getElementById('corrections-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#a371f7">🧠</span> Distill: ${{d.facts_stored||0}} facts (${{d.total_distilled||0}} total, €${{d.cost_eur||0}})`;
    cf.prepend(div);
    if(cf.children.length>20)cf.lastChild.remove();
  }}

  // Phase 9: Meta-learning report
  if(tp==='meta_report'){{
    const cf=document.getElementById('corrections-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#d29922">📊</span> Meta: ${{d.suggestions||0}} suggestions, ${{d.optimizations_applied||0}} auto-applied`;
    cf.prepend(div);
    if(cf.children.length>20)cf.lastChild.remove();
  }}

  // Phase 9: Code suggestion
  if(tp==='code_suggestion'){{
    const cf=document.getElementById('corrections-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#f85149">🔍</span> Code review: ${{d.new_suggestions||0}} new (${{d.total_pending||0}} pending)`;
    cf.prepend(div);
    if(cf.children.length>20)cf.lastChild.remove();
  }}

  // Phase 4: User teaching
  if(tp==='user_teaching'){{
    const cf=document.getElementById('corrections-feed');
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:#3fb950">🎓</span> Opittu: ${{d.teaching||''}}`;
    cf.prepend(div);
    if(cf.children.length>20)cf.lastChild.remove();
  }}

  // Default live feed
  const feed=document.getElementById('livefeed');
  const div=document.createElement('div');
  const t=new Date().toLocaleTimeString();
  const txt=d.title||tp||'';
  if(txt.toLowerCase().includes('timeout')){{
    toCount++;
    const tc=document.getElementById('timeout-count');
    tc.style.display='inline';tc.textContent=`⚠️ ${{toCount}} timeoutia`;
  }}
  div.innerHTML=`<span style="color:#484f58">${{t}}</span> ${{txt}}`;
  feed.prepend(div);
  if(feed.children.length>60)feed.lastChild.remove();
}};

async function sendChat(){{
  const inp=document.getElementById('chatinput');
  const msg=inp.value.trim();if(!msg)return;
  const log=document.getElementById('chatlog');
  log.innerHTML+=`<div>🧑 ${{msg}}</div>`;
  inp.value='';inp.disabled=true;
  try{{
    const r=await fetch('/api/chat',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{message:msg}})}});
    const d=await r.json();
    log.innerHTML+=`<div style="color:#79c0ff">🐝 ${{d.response||d.error}}</div>`;
  }}catch(e){{log.innerHTML+=`<div style="color:#f85149">❌ ${{e}}</div>`;}}
  inp.disabled=false;log.scrollTop=log.scrollHeight;
  // User chatted → hide night badge
  document.getElementById('night-badge').style.display='none';
}}

function lvlBadge(agentId){{
  const s=agentLevels[agentId];
  if(!s) return '';
  const l=s.level||1;
  const n=s.level_name||'NOVICE';
  return `<span class="lvl-badge lvl-${{l}}">L${{l}} ${{n}}</span>`;
}}

async function loadStatus(){{
  try{{
    const r=await fetch('/api/status');const d=await r.json();

    // Cache agent levels
    agentLevels=d.agent_levels||{{}};

    // Night mode indicator
    const nm=d.night_mode||{{}};
    const nb=document.getElementById('night-badge');
    if(nm.active){{
      nb.style.display='inline';
      nb.textContent=`🌙 NIGHT (${{nm.facts_learned||0}})`;
    }}else{{
      nb.style.display='none';
    }}

    const a=document.getElementById('agents');
    const agents=d.agents?.list||[];
    document.getElementById('agent-count').textContent=`(${{agents.length}})`;
    a.innerHTML=agents.map(ag=>{{
      const role=ag.role||'worker';
      const sc=ag.status==='idle'?'#484f58':ag.status==='thinking'?'#d29922':'#3fb950';
      const badge=lvlBadge(ag.id||'');
      return `<div class="stat role-${{role}}"><span style="color:${{sc}}">●</span> ${{ag.name}} <span style="color:#484f58">[${{role}}]</span>${{badge}}</div>`;
    }}).join('')||'Ei agentteja';

    const lb=document.getElementById('leaderboard');
    lb.innerHTML=(d.token_economy?.leaderboard||[]).slice(0,10).map(e=>
      `<div class="stat">${{e.agent_id.slice(0,18)}} = ${{e.balance}}🪙</div>`
    ).join('')||'—';

    const sw=document.getElementById('swarm');const ss=d.swarm||{{}};
    sw.innerHTML=`
      <div class="stat">Agentit: ${{ss.total_agents||0}}</div>
      <div class="stat">Kalibr: ${{ss.calibrated||0}}</div>
      <div class="stat">Exploration: ${{((ss.exploration_rate||0)*100).toFixed(0)}}%</div>
      <div class="stat role-scout">Scouts: ${{ss.roles?.scout||0}}</div>
      <div class="stat role-worker">Workers: ${{ss.roles?.worker||0}}</div>
      <div class="stat role-judge">Judges: ${{ss.roles?.judge||0}}</div>
    `;

    const th=document.getElementById('throttle');const tt=d.throttle||{{}};
    const ep=((tt.error_rate||0)*100).toFixed(0);
    const ec=tt.error_rate>0.2?'#f85149':tt.error_rate>0.05?'#d29922':'#3fb950';
    th.innerHTML=`
      <div class="stat">${{tt.machine_class||'?'}}</div>
      <div class="stat">Viive: ${{tt.avg_latency_ms||0}}ms</div>
      <div class="stat" style="color:${{ec}}">Virhe: ${{ep}}%</div>
      <div class="stat">HB: ${{tt.heartbeat_interval_s||30}}s</div>
      <div class="stat">Conc: ${{tt.max_concurrent||1}}</div>
      <div class="stat">Reqs: ${{tt.total_requests||0}} (${{tt.total_errors||0}}❌)</div>
    `;

    // OpsAgent
    const oa=document.getElementById('opsagent');
    const ops=d.ops_agent||{{}};
    const models=ops.models||{{}};
    let modelHtml='';
    for(const[name,m] of Object.entries(models)){{
      const effColor=m.efficiency>0.6?'#3fb950':m.efficiency>0.3?'#d29922':'#f85149';
      modelHtml+=`<div class="stat" style="border-left:3px solid ${{effColor}}">
        ${{name}} eff=${{m.efficiency}} q=${{m.quality_score}} lat=${{m.avg_latency_ms}}ms err=${{(m.error_rate*100).toFixed(0)}}%
      </div>`;
    }}
    const decisions=(ops.decisions||[]).slice(-5).reverse();
    let decHtml=decisions.map(d=>`<div style="font-size:10px;color:#8b949e;padding:2px 0">
      ${{d.action}}: ${{d.reason}}</div>`).join('');
    const trendColor=ops.latency_trend>0.1?'#f85149':ops.latency_trend<-0.1?'#3fb950':'#8b949e';
    const idleSt=ops.idle_paused?'<span style="color:#f85149">PAUSED</span>':'<span style="color:#3fb950">OK</span>';
    oa.innerHTML=`
      <div class="stat">Sykli: ${{ops.cycle_count||0}}</div>
      <div class="stat" style="color:${{trendColor}}">Trendi: ${{((ops.latency_trend||0)*100).toFixed(0)}}%</div>
      <div class="stat">Idle: ${{idleSt}}</div>
      <div style="margin:6px 0">${{modelHtml}}</div>
      ${{decHtml?'<div style="margin-top:6px;border-top:1px solid #21262d;padding-top:4px"><span style="color:#484f58;font-size:10px">Päätökset:</span>'+decHtml+'</div>':''}}`;

    // Phase 4: Consciousness stats
    const cs=document.getElementById('consciousness-stats');
    const con=d.consciousness||{{}};
    const corrCount=d.corrections_count||0;
    const epCount=d.episodes_count||0;
    cs.innerHTML=`
      <div class="stat">Muisti: ${{con.memories||0}}</div>
      <div class="stat">Swarm: ${{con.swarm_facts||0}}</div>
      <div class="stat" style="color:#da3688">Korjaukset: ${{con.corrections||corrCount}}</div>
      <div class="stat">Episodit: ${{con.episodes||epCount}}</div>
      <div class="stat">Kyselyt: ${{con.total_queries||0}}</div>
      <div class="stat">Prefilter: ${{con.prefilter_hits||0}}</div>
      <div class="stat" style="color:#f85149">Hallus: ${{con.hallucinations_caught||0}}</div>
      <div class="stat">Cache: ${{con.cache_hit_rate||'0%'}}</div>
      <div class="stat" style="color:#3fb950">Active learning: ${{con.active_learning_count||0}}</div>
      <div class="stat">Synonyms: ${{con.domain_synonyms||0}}</div>
      <div class="stat">Jono: ${{con.learn_queue_size||0}}</div>
      <div class="stat" style="color:#f0b429">FI-index: ${{con.bilingual_fi_count||0}}</div>
      ${{con.hot_cache?`<div class="stat" style="color:#58a6ff">Cache: ${{con.hot_cache.size||0}}/${{con.hot_cache.max_size||500}} (${{(con.hot_cache.hit_rate*100||0).toFixed(0)}}% hit)</div>`:''}}
    `;
    // Update corrections badge
    if(corrCount>0){{
      const cb=document.getElementById('corrections-badge');
      cb.style.display='inline';
      cb.textContent=`📝 ${{corrCount}}`;
    }}

    // LearningEngine
    const le=document.getElementById('learning');
    const lr=d.learning||{{}};
    const ls=lr.stats||{{}};
    const ap=lr.agent_performance||{{}};
    const qc=ls.avg_quality||0;
    const qColor=qc>=7?'#3fb950':qc>=5?'#d29922':'#f85149';
    let agentRows='';
    for(const[aid,a] of Object.entries(ap)){{
      const tc=a.trend>0.3?'#3fb950':a.trend<-0.3?'#f85149':'#8b949e';
      const helpBadge=a.needs_help?'<span style="color:#f85149;font-size:9px"> ⚠️</span>':'';
      agentRows+=`<div style="font-size:10px;padding:2px 0;border-bottom:1px solid #161b22">
        ${{a.type}}${{helpBadge}} <span style="color:${{tc}}">${{a.avg_recent}}/10</span>
        (${{a.trend>=0?'+':''}}${{a.trend}}) ${{a.total_evaluated}} arvioitu, ${{(a.good_rate*100).toFixed(0)}}% hyvä
      </div>`;
    }}
    le.innerHTML=`
      <div class="stat" style="color:${{qColor}}">Keskilaatu: ${{qc.toFixed(1)}}/10</div>
      <div class="stat">Arvioitu: ${{ls.total_evaluated||0}} | Kuratoitu: ${{ls.total_curated||0}} | Hylätty: ${{ls.total_rejected||0}}</div>
      <div class="stat">Evoluutiot: ${{ls.total_evolutions||0}} | Tiivistykset: ${{ls.total_distillations||0}}</div>
      <div class="stat">Jono: ${{lr.queue_size||0}} | Auto-evolve: ${{lr.auto_evolve?'ON':'OFF'}}</div>
      ${{agentRows?'<div style="margin-top:6px;border-top:1px solid #21262d;padding-top:4px"><span style="color:#484f58;font-size:10px">Agentit:</span>'+agentRows+'</div>':''}}
    `;
  }}catch(e){{console.error(e);}}
}}

async function loadSys(){{
  try{{
    const r=await fetch('/api/system');const d=await r.json();
    const cp=d.gpu_percent??d.cpu_percent??0;
    const ce=document.getElementById('gpu-chat');
    const cl=document.getElementById('gpu-chat-pct');
    ce.style.width=cp+'%';
    ce.style.background=cp>80?'#f85149':cp>50?'#d29922':'#3fb950';
    cl.textContent=cp+'%';cl.style.color=ce.style.background;

    const hp=d.cpu_percent??0;
    const he=document.getElementById('gpu-hb');
    const hl=document.getElementById('gpu-hb-pct');
    he.style.width=hp+'%';
    he.style.background=hp>80?'#f85149':hp>50?'#d29922':'#3fb950';
    hl.textContent=hp+'%';hl.style.color=he.style.background;
  }}catch(e){{}}
}}

async function loadFeeds(){{
  try{{
    const r=await fetch('/api/feeds');const d=await r.json();
    const fs=document.getElementById('feeds-status');
    if(!d.enabled){{fs.innerHTML='<span style="color:#484f58">Disabled</span>';return;}}
    const feeds=d.feeds||{{}};
    let html='';
    for(const[name,f] of Object.entries(feeds)){{
      const ago=f.last_run_ago_s?Math.round(f.last_run_ago_s/60)+'min ago':'never';
      const ec=f.error_count>0?'#f85149':'#3fb950';
      html+=`<div class="stat" style="border-left:3px solid ${{ec}}">${{name}}: ${{f.run_count||0}} runs (${{ago}}) ${{f.error_count?'❌'+f.error_count:''}}</div>`;
    }}
    const alerts=d.critical_alerts||[];
    if(alerts.length>0){{
      html+='<div style="margin-top:6px;color:#f85149;font-size:10px">⚠️ '+alerts.length+' critical alerts</div>';
    }}
    fs.innerHTML=html||'No feeds';
  }}catch(e){{}}
}}

setTimeout(loadStatus,500);
setInterval(loadStatus,12000);
setInterval(loadSys,3000);
setInterval(loadFeeds,30000);
loadSys();
loadFeeds();
</script></body></html>"""

        # KRIITTINEN: charset=utf-8 HTTP-headerissa
        # Ilman tätä Windows-selain käyttää cp1252:ta → ääkköset rikki
        return Response(
            content=html,
            media_type="text/html; charset=utf-8"
        )

    @app.post("/api/chat")
    async def chat(data: dict):
        msg = data.get("message", "")
        lang = data.get("lang", "auto")
        if not msg:
            return {"error": "Tyhjä viesti"}
        try:
            response = await hivemind.chat(msg, language=lang)
            return {"response": response}
        except Exception as e:
            return {"error": str(e)}

    @app.post("/api/confusion")
    async def report_confusion(data: dict):
        """Record a routing mistake so confusion memory can learn from it."""
        try:
            from backend.routes.chat import record_confusion
            q = data.get("question", "")
            wrong = data.get("wrong_agent", "")
            correct = data.get("correct_agent", "")
            if q and wrong and correct:
                record_confusion(q, wrong, correct)
            return {"status": "ok"}
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/status")
    async def status():
        try:
            return await hivemind.get_status()
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/agent_levels")
    async def agent_levels():
        """Phase 3: Agent level stats."""
        if hivemind.agent_levels:
            return {"levels": hivemind.agent_levels.get_all_stats()}
        return {"levels": {}}

    @app.get("/api/consciousness")
    async def consciousness_stats():
        """Phase 4: Full consciousness stats for feedback dashboard."""
        if not hasattr(hivemind, 'consciousness') or not hivemind.consciousness:
            return JSONResponse({"error": "no consciousness"})
        c = hivemind.consciousness
        return JSONResponse({
            "memory_count": c.memory.count,
            "corrections_count": c.memory.corrections.count(),
            "episodes_count": c.memory.episodes.count(),
            "swarm_facts_count": c.memory.swarm_facts.count(),
            "cache_hit_rate": c.embed.cache_hit_rate,
            "hallucination_rate": (c._hallucination_count
                                   / max(c._total_queries, 1)),
            "prefilter_rate": (c._prefilter_hits
                               / max(c._total_queries, 1)),
            "total_queries": c._total_queries,
            "insights_stored": c._insight_counter,
            "active_learning_count": c._active_learning_count,
            "domain_synonyms": len(c._domain_synonyms),
            "agent_levels": (hivemind.agent_levels.get_all_stats()
                             if hivemind.agent_levels else {}),
            "seasonal_focus": _get_seasonal_focus(),
            "learn_queue_size": len(c._learn_queue),
            "hot_cache": (c.hot_cache.stats
                          if hasattr(c, 'hot_cache') and c.hot_cache
                          else {}),
            "bilingual_fi_count": (c.bilingual.fi_count
                                   if hasattr(c, 'bilingual')
                                   and c.bilingual else 0),
            "enrichment": (hivemind.enrichment.stats
                           if hasattr(hivemind, 'enrichment')
                           and hivemind.enrichment else {}),
            "web_learner": (hivemind.web_learner.stats
                            if hasattr(hivemind, 'web_learner')
                            and hivemind.web_learner else {}),
            "distiller": (hivemind.distiller.stats
                          if hasattr(hivemind, 'distiller')
                          and hivemind.distiller else {}),
            "meta_learning": (hivemind.meta_learning.stats
                              if hasattr(hivemind, 'meta_learning')
                              and hivemind.meta_learning else {}),
            "code_reviewer": (hivemind.code_reviewer.stats
                              if hasattr(hivemind, 'code_reviewer')
                              and hivemind.code_reviewer else {}),
            "micro_model": (hivemind.micro_model.stats
                            if hasattr(hivemind, 'micro_model')
                            and hivemind.micro_model else {}),
        })

    @app.get("/api/system")
    async def system_stats():
        """CPU/GPU/Memory dynaamisesti headeriin."""
        stats = {"cpu_percent": 0, "memory_percent": 0, "gpu_percent": None}
        try:
            import psutil
            stats["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            stats["memory_percent"] = psutil.virtual_memory().percent
        except ImportError:
            pass
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                vals = [int(x.strip()) for x in result.stdout.strip().split('\n') if x.strip()]
                if vals:
                    stats["gpu_percent"] = vals[0]
        except (FileNotFoundError, Exception):
            pass
        return stats

    @app.get("/api/swarm/scores")
    async def swarm_scores():
        if hivemind.scheduler:
            return {"scores": hivemind.scheduler.get_agent_scores()}
        return {"scores": []}

    @app.get("/api/ops")
    async def ops_status():
        """OpsAgent reaaliaikatiedot + mallisuositukset."""
        if hivemind.ops_agent:
            return {
                "status": hivemind.ops_agent.get_status(),
                "recommendation": hivemind.ops_agent.get_model_recommendation(),
            }
        return {"status": {}, "recommendation": {}}

    @app.get("/api/learning")
    async def learning_status():
        """LearningEngine tiedot + laatutaulukko."""
        if hivemind.learning:
            return {
                "status": hivemind.learning.get_status(),
                "leaderboard": hivemind.learning.get_leaderboard(),
            }
        return {"status": {}, "leaderboard": []}

    @app.get("/api/feeds")
    async def feeds_status():
        """Phase 8: Data feed status + critical RSS alerts."""
        if not hasattr(hivemind, 'data_feeds') or not hivemind.data_feeds:
            return JSONResponse({"enabled": False, "feeds": {}})
        status = hivemind.data_feeds.get_status()
        # Add critical alerts from RSS feed
        rss = hivemind.data_feeds._feeds.get("rss")
        if rss:
            status["critical_alerts"] = rss.critical_alerts
        return JSONResponse(status)

    @app.post("/api/feeds/{feed_name}/refresh")
    async def feeds_refresh(feed_name: str):
        """Phase 8: Force immediate feed update."""
        if not hasattr(hivemind, 'data_feeds') or not hivemind.data_feeds:
            return JSONResponse({"error": "feeds not enabled"}, status_code=400)
        stored = await hivemind.data_feeds.force_update(feed_name)
        return JSONResponse({"feed": feed_name, "facts_stored": stored})

    @app.get("/api/meta_report")
    async def meta_report():
        """Phase 9: Latest meta-learning weekly report."""
        if (not hasattr(hivemind, 'meta_learning')
                or not hivemind.meta_learning):
            return JSONResponse({"report": None, "available": False})
        ml = hivemind.meta_learning
        return JSONResponse({
            "available": True,
            "report": ml._last_report,
            "stats": ml.stats,
        })

    @app.get("/api/code_suggestions")
    async def code_suggestions():
        """Phase 9: Pending code review suggestions."""
        if (not hasattr(hivemind, 'code_reviewer')
                or not hivemind.code_reviewer):
            return JSONResponse({"suggestions": [], "available": False})
        cr = hivemind.code_reviewer
        return JSONResponse({
            "available": True,
            "pending": cr.get_pending_suggestions(),
            "stats": cr.stats,
        })

    @app.post("/api/code_suggestions/{index}/accept")
    async def code_suggestion_accept(index: int):
        """Phase 9: Accept a code review suggestion."""
        if (not hasattr(hivemind, 'code_reviewer')
                or not hivemind.code_reviewer):
            return JSONResponse({"error": "code reviewer not available"},
                                status_code=400)
        hivemind.code_reviewer.accept_suggestion(index)
        return JSONResponse({"status": "accepted", "index": index})

    @app.post("/api/code_suggestions/{index}/reject")
    async def code_suggestion_reject(index: int):
        """Phase 9: Reject a code review suggestion."""
        if (not hasattr(hivemind, 'code_reviewer')
                or not hivemind.code_reviewer):
            return JSONResponse({"error": "code reviewer not available"},
                                status_code=400)
        hivemind.code_reviewer.reject_suggestion(index)
        return JSONResponse({"status": "rejected", "index": index})

    @app.get("/api/micro_model")
    async def micro_model_stats():
        """Phase 10: Micro-model training stats and availability."""
        if (not hasattr(hivemind, 'micro_model')
                or not hivemind.micro_model):
            return JSONResponse({"available": False, "stats": {}})
        return JSONResponse({
            "available": True,
            "stats": hivemind.micro_model.stats,
        })

    @app.get("/api/monitor/history")
    async def monitor_history():
        if hivemind.monitor:
            return {"events": hivemind.monitor.get_history(50)}
        return {"events": []}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        async def ws_callback(data):
            try:
                await websocket.send_json(data)
            except Exception:
                pass
        hivemind.register_ws_callback(ws_callback)
        if hivemind.monitor:
            hivemind.monitor.register_callback(
                lambda e: websocket.send_json(e)
            )
        if hivemind.ops_agent:
            hivemind.ops_agent.register_decision_callback(ws_callback)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hivemind.unregister_ws_callback(ws_callback)

    return app
