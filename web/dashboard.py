"""
WaggleDance AI — Dashboard v2.0.0
=========================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v2.0.0 • Built: 2026-03-18

v1.0.0 (MAGMA Activation):
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
import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("waggledance.dashboard")
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, JSONResponse


def _get_seasonal_focus():
    """Get current month's seasonal keywords from consciousness module."""
    try:
        from core.memory_engine import SEASONAL_BOOST
        return SEASONAL_BOOST.get(datetime.now().month, [])
    except ImportError:
        return []


def _get_enrichment_stats_from(hm):
    """Get NightEnricher stats from orchestrator if available."""
    try:
        ne = getattr(hm, 'night_enricher', None)
        if not ne:
            return {}
        sm = getattr(ne, 'source_manager', None)
        conv = getattr(ne, 'convergence', None)
        return {
            "total_checked": getattr(ne, '_total_checked', 0),
            "total_stored": getattr(ne, '_total_stored', 0),
            "per_agent_stored": dict(getattr(ne, '_per_agent_stored', {})),
            "sources": {
                sid: sm.get_metrics(sid).to_dict()
                for sid in (sm.source_ids if sm else [])
                if sm.get_metrics(sid)
            } if sm else {},
            "convergence": {
                "total_convergences": conv._total_convergences if conv else 0,
            },
        }
    except Exception:
        return {}


# Profiles that have bee/hive-specific dashboard sections
_APIARY_PROFILES = {"apiary", "cottage", "mehilainen", "beekeeper"}


def _is_apiary_profile(hivemind) -> bool:
    """Check if current profile should show bee-specific sections."""
    profile = (hivemind.config.get("profile", "") or "").lower()
    return profile in _APIARY_PROFILES


def create_app(hivemind):
    app = FastAPI(title="WaggleDance AI Dashboard")

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

    # ── CORS middleware ────────────────────────────────────
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173", "http://localhost:3000",
            "http://localhost:8000", "http://127.0.0.1:5173",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # ── Auth middleware (Bearer token) ────────────────────
    from backend.auth import get_or_create_api_key, BearerAuthMiddleware
    _api_key = get_or_create_api_key()
    app.add_middleware(BearerAuthMiddleware, api_key=_api_key)

    chat_model = hivemind.llm.model if hivemind.llm else "?"
    hb_model = (hivemind.llm_heartbeat.model
                if hivemind.llm_heartbeat else chat_model)

    # ── Chat history storage ────────────────────────────
    from core.chat_history import ChatHistory
    _chat_history = ChatHistory()

    @app.get("/")
    async def index():
        swarm_badge = ("ENABLED" if hivemind._swarm_enabled
                       else "DISABLED")
        swarm_color = ("#3fb950" if hivemind._swarm_enabled
                       else "#f85149")

        html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<script>localStorage.setItem("WAGGLE_API_KEY","{_api_key}")</script>
<title>WaggleDance AI (on-prem)</title>
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
  .role-explorer{{border-left:3px solid #58a6ff}}
  .role-executor{{border-left:3px solid #3fb950}}
  .role-evaluator{{border-left:3px solid #d29922}}
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
    <h1>&#x2699;&#xFE0F; <span class="t-main">WaggleDance AI</span> <span class="t-sub">(on-prem)</span></h1>
    <div class="sub2">Jani Korpi • v2.0.0 • <span class="sbadge">MULTI-AGENT {swarm_badge}</span><span id="night-badge" class="night-badge">🌙 NIGHT</span><span id="corrections-badge" style="display:none;background:#da368822;color:#da3688;border:1px solid #da368844;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:600;margin-left:6px">📝 0</span></div>
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
    <h2>📊 Scheduler Stats</h2>
    <div id="swarm"></div>
    <h2 style="margin-top:14px">⚙️ Throttle</h2>
    <div id="throttle"></div>
    <h2 style="margin-top:14px">🤖 OpsAgent</h2>
    <div id="opsagent"></div>
    <h2 style="margin-top:14px">🧬 Oppiminen</h2>
    <div id="learning"></div>
  </div>
  <div class="card">
    <h2>🧠 Runtime State</h2>
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
    div.innerHTML=`<span style="color:#f0b429">✨</span> Enrichment: ${{d.facts_stored||0}} facts (${{d.total_enriched||d.total_web||d.total_distilled||0}} total)`;
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
    log.innerHTML+=`<div style="color:#79c0ff">&#x1F4AC; ${{d.response||d.error}}</div>`;
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
      <div class="stat role-explorer">Explorers: ${{ss.roles?.scout||0}}</div>
      <div class="stat role-executor">Executors: ${{ss.roles?.worker||0}}</div>
      <div class="stat role-evaluator">Evaluators: ${{ss.roles?.judge||0}}</div>
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
      <div class="stat">Shared: ${{con.swarm_facts||0}}</div>
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
    async def chat(request: Request):
        body = await request.body()
        if len(body) > 100_000:  # 100KB limit
            return JSONResponse({"error": "Message too large"}, status_code=413)
        try:
            # Try UTF-8 first, fall back to latin-1 (Windows CP1252 superset)
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                text = body.decode("latin-1")
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        msg = data.get("message", "")
        lang = data.get("lang", "auto")
        if not msg:
            return {"error": "Tyhjä viesti"}
        try:
            profile = hivemind.config.get("profile", "cottage")
            conv_id = _chat_history.get_or_create_conversation(profile)
            _chat_history.add_message(conv_id, "user", msg, language=lang)
            t0 = time.time()
            response = await hivemind.chat(msg, language=lang)
            elapsed_ms = int((time.time() - t0) * 1000)
            # Get agent name from ChatHandler (reliable) or parse from response (fallback)
            agent_name = None
            _ch = getattr(hivemind, '_chat_handler', None)
            if _ch:
                agent_name = getattr(_ch, '_last_chat_agent_id', None)
            if not agent_name and response and response.startswith("["):
                bracket_end = response.find("]")
                if bracket_end > 0:
                    agent_name = response[1:bracket_end]
            msg_id = _chat_history.add_message(
                conv_id, "assistant", response,
                agent_name=agent_name, language=lang,
                response_time_ms=elapsed_ms)
            # Attach structured results from ChatHandler if available
            _method = None
            _model_result = None
            _explanation = None
            if _ch:
                _method = getattr(_ch, '_last_chat_method', None)
                _model_result = getattr(_ch, '_last_model_result', None)
                _explanation = getattr(_ch, '_last_explanation', None)
            resp_body = {"response": response, "message_id": msg_id,
                         "conversation_id": conv_id, "agent": agent_name}
            if _method:
                resp_body["method"] = _method
            if _model_result:
                resp_body["model_result"] = _model_result
            if _explanation:
                resp_body["explanation"] = _explanation
            return resp_body
        except Exception as e:
            log.error("API chat error: %s", e, exc_info=True)
            return JSONResponse({"error": "Internal error"}, status_code=500)

    @app.get("/api/history")
    async def chat_history_list(request: Request):
        """List recent conversations."""
        try:
            limit = int(request.query_params.get("limit", "20"))
            offset = int(request.query_params.get("offset", "0"))
        except ValueError:
            return JSONResponse({"error": "limit and offset must be integers"}, status_code=400)
        profile = request.query_params.get("profile", None)
        convs = _chat_history.get_conversations(limit, offset, profile)
        return {"conversations": convs}

    @app.get("/api/history/recent/messages")
    async def chat_history_recent():
        """Get most recent messages across all conversations."""
        msgs = _chat_history.get_recent_messages(limit=50)
        return {"messages": msgs}

    @app.get("/api/history/{conversation_id}")
    async def chat_history_detail(conversation_id: int):
        """Get full conversation with messages."""
        conv = _chat_history.get_conversation(conversation_id)
        if not conv:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return conv

    @app.post("/api/feedback")
    async def submit_feedback(data: dict):
        """Submit feedback (thumbs up/down) for a message."""
        message_id = data.get("message_id")
        rating = data.get("rating")  # 1=down, 2=up
        correction = data.get("correction")
        if not message_id or rating not in (1, 2):
            return JSONResponse({"error": "Invalid feedback"}, status_code=400)
        fb_id = _chat_history.add_feedback(message_id, rating, correction)
        # If thumbs-down with correction, feed into confusion memory
        if rating == 1 and correction:
            try:
                from backend.routes.chat import record_confusion
                # Get the original message to find wrong agent
                conv = _chat_history.get_recent_messages(limit=100)
                for m in conv:
                    if m.get("id") == message_id and m.get("agent_name"):
                        record_confusion(
                            correction, m["agent_name"], correction)
                        break
            except Exception:
                pass
        return {"status": "recorded", "feedback_id": fb_id}

    @app.post("/api/language")
    async def set_language(data: dict):
        """Set UI language mode: 'auto', 'fi', 'en'."""
        mode = data.get("mode", "auto")
        hivemind.set_language(mode)
        return {"language": hivemind.language_mode}

    @app.get("/api/language")
    async def get_language():
        return {"language": hivemind.language_mode}

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
            log.error("API error: %s", e)
            return {"error": "Internal error"}

    @app.get("/api/status")
    async def status():
        try:
            return await hivemind.get_status()
        except Exception as e:
            log.error("API error: %s", e)
            return {"error": "Internal error"}

    @app.get("/api/agent_levels")
    @app.get("/api/agents/levels")
    async def agent_levels():
        """Phase 3: Agent level stats for dashboard AgentGridPanel."""
        if not hivemind.agent_levels:
            return {"levels": {}, "agents": [], "total": 0,
                    "level_distribution": {}}
        stats = hivemind.agent_levels.get_all_stats()
        agents = list(stats.values())
        from collections import Counter
        dist = Counter(a.get("level_name", "NOVICE") for a in agents)
        return {
            "levels": stats,
            "agents": agents,
            "total": len(agents),
            "level_distribution": dict(dist),
        }

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
            "fi_fast": (c.fi_fast.stats
                        if hasattr(c, 'fi_fast') and c.fi_fast
                        else {}),
            "enrichment": _get_enrichment_stats_from(hivemind),
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

    @app.get("/api/profile")
    async def get_profile():
        """Return active profile and agent counts per profile."""
        profile = hivemind.config.get("profile", "cottage")
        stats = {"active_profile": profile, "profiles": ["gadget", "cottage", "home", "factory"]}
        if hasattr(hivemind, 'spawner') and hasattr(hivemind.spawner, 'yaml_bridge'):
            stats.update(hivemind.spawner.yaml_bridge.get_profile_stats())
        return stats

    @app.post("/api/profile")
    async def set_profile(request: Request):
        """Switch active profile (requires restart to take effect)."""
        body = await request.json()
        new_profile = body.get("profile", "cottage")
        if new_profile not in ("gadget", "cottage", "home", "factory"):
            return JSONResponse({"error": f"Invalid profile: {new_profile}"}, status_code=400)
        # Update settings.yaml
        import yaml as _yaml
        settings_path = Path("configs/settings.yaml")
        with open(settings_path, encoding="utf-8") as f:
            content = f.read()
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("profile:"):
                lines[i] = f"profile: {new_profile}  # gadget | cottage | home | factory"
                break
        import tempfile, os
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(Path(settings_path).parent))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.replace(tmp_path, str(settings_path))
        except Exception:
            os.unlink(tmp_path)
            raise
        return {"profile": new_profile, "message": "Profile updated. Restart to apply."}

    # ── Model Status endpoint ──────────────────────────────
    @app.get("/api/models")
    async def models_status():
        """Ollama model status: loaded models, roles, VRAM usage."""
        import urllib.request
        import urllib.error

        # Role mapping from settings
        role_map = {
            chat_model: "chat",
            hb_model: "background_learning",
        }
        # Add embedding model
        embed_model = "nomic-embed-text"
        eval_model = "all-minilm"
        role_map[embed_model] = "embedding"
        role_map[eval_model] = "evaluation"

        result = {"models": [], "vram_total_mb": 0, "vram_used_mb": 0,
                  "vram_percent": 0.0, "ollama_available": False}

        # Query Ollama for loaded models (ps) and all models (tags)
        loaded_models = {}
        all_models = {}
        try:
            # Loaded models
            req = urllib.request.Request("http://localhost:11434/api/ps")
            with urllib.request.urlopen(req, timeout=3) as resp:
                ps_data = json.loads(resp.read().decode("utf-8"))
            result["ollama_available"] = True
            for m in ps_data.get("models", []):
                name = m.get("name", "").split(":")[0]
                loaded_models[name] = {
                    "size_bytes": m.get("size", 0),
                    "vram_mb": round(m.get("size_vram", m.get("size", 0)) / (1024 * 1024)),
                }

            # All available models
            req2 = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req2, timeout=3) as resp:
                tags_data = json.loads(resp.read().decode("utf-8"))
            for m in tags_data.get("models", []):
                name = m.get("name", "").split(":")[0]
                all_models[name] = {
                    "size_gb": round(m.get("size", 0) / (1024**3), 1),
                }
        except Exception:
            pass

        # Build model list — combine role_map with loaded/available info
        total_vram = 0
        seen = set()
        for model_name, role in role_map.items():
            base = model_name.split(":")[0]
            if base in seen:
                continue
            seen.add(base)
            is_loaded = base in loaded_models
            vram = loaded_models.get(base, {}).get("vram_mb", 0) if is_loaded else 0
            size_gb = all_models.get(base, {}).get("size_gb", 0.0)
            if not size_gb and is_loaded:
                size_gb = round(loaded_models[base]["size_bytes"] / (1024**3), 1)
            total_vram += vram
            result["models"].append({
                "name": model_name,
                "role": role,
                "loaded": is_loaded,
                "size_gb": size_gb,
                "vram_mb": vram,
            })

        # Add any other loaded models not in role_map
        for base, info in loaded_models.items():
            if base not in seen:
                seen.add(base)
                total_vram += info["vram_mb"]
                result["models"].append({
                    "name": base,
                    "role": "other",
                    "loaded": True,
                    "size_gb": all_models.get(base, {}).get("size_gb",
                               round(info["size_bytes"] / (1024**3), 1)),
                    "vram_mb": info["vram_mb"],
                })

        result["vram_used_mb"] = total_vram

        # Get total VRAM from nvidia-smi
        try:
            import subprocess as _sp
            _nv = _sp.run(
                ["nvidia-smi", "--query-gpu=memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2)
            if _nv.returncode == 0:
                result["vram_total_mb"] = int(_nv.stdout.strip())
                if result["vram_total_mb"] > 0:
                    result["vram_percent"] = round(
                        total_vram / result["vram_total_mb"] * 100, 1)
        except Exception:
            pass

        return result

    @app.get("/api/auth/token")
    async def auth_token(request: Request):
        """Return API key — localhost only."""
        client_host = request.client.host if request.client else ""
        if client_host not in ("127.0.0.1", "::1"):
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        return {"token": _api_key}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def readiness():
        status = await hivemind.get_status() if hivemind else {}
        return {"status": "ready", "running": status.get("running", False)}

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
            import asyncio as _aio
            proc = await _aio.create_subprocess_exec(
                "nvidia-smi", "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
                stdout=_aio.subprocess.PIPE,
                stderr=_aio.subprocess.PIPE,
            )
            stdout, _ = await _aio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                result_text = stdout.decode()
                vals = [int(x.strip()) for x in result_text.strip().split('\n') if x.strip()]
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
        result = {"status": {}, "leaderboard": []}
        if hivemind.learning:
            result["status"] = hivemind.learning.get_status()
            result["leaderboard"] = hivemind.learning.get_leaderboard()
        # v1.16.0: Night enricher source capabilities
        ne = getattr(hivemind, 'night_enricher', None)
        if ne and hasattr(ne, 'source_manager'):
            result["source_capabilities"] = ne.source_manager.get_capability_map()
        return result

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

    @app.get("/api/sensors")
    async def sensors_status():
        """Phase 5: Smart home sensor hub status."""
        if not hasattr(hivemind, 'sensor_hub') or not hivemind.sensor_hub:
            return JSONResponse({"available": False, "status": {}})
        return JSONResponse({
            "available": True,
            "status": hivemind.sensor_hub.get_status(),
        })

    @app.get("/api/sensors/home")
    async def sensors_home():
        """Phase 5: Home Assistant entity states."""
        if (not hasattr(hivemind, 'sensor_hub')
                or not hivemind.sensor_hub
                or not hivemind.sensor_hub.home_assistant):
            return JSONResponse({"available": False, "entities": {}})
        ha = hivemind.sensor_hub.home_assistant
        return JSONResponse({
            "available": ha.enabled,
            "entities": ha.get_entities(),
            "context": ha.get_home_context(),
        })

    @app.get("/api/sensors/camera/events")
    async def sensors_camera_events():
        """Phase 5: Recent Frigate camera events."""
        if (not hasattr(hivemind, 'sensor_hub')
                or not hivemind.sensor_hub
                or not hivemind.sensor_hub.frigate):
            return JSONResponse({"available": False, "events": []})
        frigate = hivemind.sensor_hub.frigate
        return JSONResponse({
            "available": frigate.enabled,
            "events": frigate.get_recent_events(limit=20),
        })

    # ── Phase 6: Audio Sensors ──────────────────────────────

    @app.get("/api/sensors/audio")
    async def sensors_audio():
        """Phase 6: Audio monitor status + recent events."""
        if (not hasattr(hivemind, 'sensor_hub')
                or not hivemind.sensor_hub
                or not hivemind.sensor_hub.audio_monitor):
            return JSONResponse({"available": False, "events": []})
        audio = hivemind.sensor_hub.audio_monitor
        return JSONResponse({
            "available": audio._enabled,
            "status": audio.get_status(),
            "events": audio.get_recent_events(limit=20),
        })

    @app.get("/api/sensors/audio/bee")
    async def sensors_audio_bee():
        """Phase 6: Audio analysis status per monitored unit.
        Only available for apiary-related profiles."""
        if not _is_apiary_profile(hivemind):
            return JSONResponse({"available": False, "units": {},
                                 "reason": "Not an apiary profile"})
        if (not hasattr(hivemind, 'sensor_hub')
                or not hivemind.sensor_hub
                or not hivemind.sensor_hub.audio_monitor):
            return JSONResponse({"available": False, "units": {}})
        audio = hivemind.sensor_hub.audio_monitor
        analyzer = audio._bee_analyzer
        if not analyzer:
            return JSONResponse({"available": False, "units": {}})
        units = {
            unit_id: analyzer.get_hive_status(unit_id)
            for unit_id in analyzer._hive_status
        }
        return JSONResponse({
            "available": True,
            "units": units,
            "stats": analyzer.stats,
        })

    # ── Phase 7: Voice Interface ─────────────────────────────

    @app.get("/api/voice/status")
    async def voice_status():
        """Phase 7: Voice interface component readiness."""
        if (not hasattr(hivemind, 'voice_interface')
                or not hivemind.voice_interface):
            return JSONResponse({"available": False, "enabled": False,
                                 "stt_available": False, "tts_available": False})
        return JSONResponse(hivemind.voice_interface.status())

    @app.post("/api/voice/text")
    async def voice_text(request: Request):
        """Phase 7: Text input with optional TTS audio response."""
        if (not hasattr(hivemind, 'voice_interface')
                or not hivemind.voice_interface):
            return JSONResponse({"error": "Voice interface not available"},
                                status_code=503)
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse({"error": "Empty text"}, status_code=400)
        import base64
        result = await hivemind.voice_interface.process_text(text)
        audio_b64 = (base64.b64encode(result.audio_bytes).decode("ascii")
                     if result.audio_bytes else "")
        return JSONResponse({
            "input_text": result.input_text,
            "output_text": result.output_text,
            "audio_base64": audio_b64,
            "latency_ms": round(result.total_latency_ms, 1),
        })

    @app.post("/api/voice/audio")
    async def voice_audio(request: Request):
        """Phase 7: Audio input (base64) -> STT -> chat -> TTS."""
        if (not hasattr(hivemind, 'voice_interface')
                or not hivemind.voice_interface):
            return JSONResponse({"error": "Voice interface not available"},
                                status_code=503)
        import base64
        body = await request.body()
        if len(body) > 10_000_000:  # 10 MB limit for audio
            return JSONResponse({"error": "Audio too large"}, status_code=413)
        data = json.loads(body)
        audio_b64 = data.get("audio_base64", "")
        if not audio_b64:
            return JSONResponse({"error": "No audio data"}, status_code=400)
        audio_bytes = base64.b64decode(audio_b64)
        sample_rate = data.get("sample_rate", 16000)
        result = await hivemind.voice_interface.process_audio(
            audio_bytes, sample_rate)
        resp_audio = (base64.b64encode(result.audio_bytes).decode("ascii")
                      if result.audio_bytes else "")
        return JSONResponse({
            "input_text": result.input_text,
            "output_text": result.output_text,
            "audio_base64": resp_audio,
            "stt_latency_ms": round(result.stt_latency_ms, 1),
            "chat_latency_ms": round(result.chat_latency_ms, 1),
            "tts_latency_ms": round(result.tts_latency_ms, 1),
            "total_latency_ms": round(result.total_latency_ms, 1),
            "wake_word_detected": result.wake_word_detected,
        })

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

    # ── Recent heartbeat messages buffer (for polling clients) ──────
    _recent_heartbeats = []  # list of {agent, message, type, ...}
    _MAX_HB_BUFFER = 20

    async def _capture_heartbeat(msg):
        """Capture WS events into polling buffer for React dashboard.
        msg format: {"type": event_type, "data": {payload}}
        """
        event_type = msg.get("type", "")
        d = msg.get("data", {})
        entry = None
        if event_type == "agent_insight":
            entry = {
                "agent": d.get("agent", "?"),
                "message": d.get("insight", ""),
                "type": "insight",
                "role": d.get("type", ""),
            }
        elif event_type == "heartbeat":
            actions = d.get("actions", [])
            if actions:
                entry = {
                    "agent": "Heartbeat",
                    "message": f"HB #{d.get('count', '?')}: {', '.join(str(a) for a in actions)}",
                    "type": "status",
                }
        elif event_type == "queen_insight":
            entry = {
                "agent": "Coordinator",
                "message": d.get("insight", d.get("content", "")),
                "type": "coordinator",
            }
        elif event_type == "round_table_insight":
            entry = {
                "agent": d.get("agent", "Round Table"),
                "message": d.get("insight", d.get("text", "")),
                "type": "round_table",
            }
        if entry and entry["message"]:
            _recent_heartbeats.insert(0, entry)
            while len(_recent_heartbeats) > _MAX_HB_BUFFER:
                _recent_heartbeats.pop()

    hivemind.register_ws_callback(_capture_heartbeat)

    @app.get("/api/heartbeat")
    async def heartbeat_feed():
        """Recent agent insights for React dashboard polling."""
        return _recent_heartbeats[:6]

    # ── GPU smoothing (moving average over last 5 samples) ──
    _gpu_samples = []
    _cpu_samples = []

    @app.get("/api/hardware")
    async def hardware_stats():
        nonlocal _gpu_samples, _cpu_samples
        """Hardware stats: CPU, GPU, VRAM, RAM for React dashboard + Boot screen."""
        result = {"cpu": 0, "gpu": 0, "vram": 0.0, "ram_gb": 0.0}
        try:
            import psutil
            _raw_cpu = psutil.cpu_percent(interval=0.1)
            _cpu_samples.append(_raw_cpu)
            if len(_cpu_samples) > 5:
                _cpu_samples[:] = _cpu_samples[-5:]
            result["cpu"] = round(sum(_cpu_samples) / len(_cpu_samples), 1)
            _mem = psutil.virtual_memory()
            result["ram_gb"] = round(_mem.total / (1024**3), 1)
            result["ram_total_gb"] = round(_mem.total / (1024**3), 1)
            result["cpu_count"] = psutil.cpu_count(logical=True)
            result["cpu_model"] = f"{psutil.cpu_count(logical=True)} threads"
        except ImportError:
            pass
        # CPU model name (platform-specific)
        try:
            import platform
            _proc = platform.processor()
            if _proc:
                result["cpu_model"] = _proc
        except Exception:
            pass
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,name",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
            if proc.returncode == 0:
                parts = [x.strip() for x in stdout.decode().strip().split(", ")]
                if len(parts) >= 4:
                    _raw_gpu = int(parts[0])
                    _gpu_samples.append(_raw_gpu)
                    if len(_gpu_samples) > 5:
                        _gpu_samples[:] = _gpu_samples[-5:]
                    result["gpu"] = round(sum(_gpu_samples) / len(_gpu_samples))
                    result["vram"] = round(int(parts[1]) / 1024, 1)
                    result["vram_total"] = round(int(parts[2]) / 1024, 1)
                    result["gpu_name"] = f"{parts[3]} - {round(int(parts[2])/1024)} GB"
        except Exception:
            pass
        # Throttle state
        if hivemind.throttle:
            ts = hivemind.throttle.state
            result["machine_class"] = ts.machine_class
            result["heartbeat_interval"] = ts.heartbeat_interval
            result["max_concurrent"] = ts.max_concurrent
            result["avg_latency_ms"] = round(ts.avg_latency_ms)
            result["total_requests"] = hivemind.throttle._total_requests
            result["total_errors"] = hivemind.throttle._total_errors
        # Facts count
        if hasattr(hivemind, 'consciousness') and hivemind.consciousness:
            result["facts"] = hivemind.consciousness.memory.count
        return result

    # ── Domain Capsule + SmartRouter v2 endpoints ──────────

    @app.get("/api/capsule")
    async def capsule_info():
        """Return the active domain capsule configuration."""
        if not getattr(hivemind, 'capsule', None):
            return {"error": "no capsule loaded", "domain": None}
        return hivemind.capsule.to_dict()

    @app.get("/api/route")
    async def route_query(q: str = ""):
        """Test routing a query through SmartRouter v2."""
        if not q:
            return {"error": "missing ?q= parameter"}
        router = getattr(hivemind, 'smart_router_v2', None)
        if not router:
            return {"error": "SmartRouter v2 not loaded"}
        result = router.route(q)
        return {
            "query": q,
            "result": result.to_dict(),
            "stats": router.stats(),
        }

    @app.post("/api/solve")
    async def solve_model(request: Request):
        """Directly call SymbolicSolver with model_id + inputs."""
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        model_id = data.get("model_id", "")
        inputs = data.get("inputs", {})
        if not model_id:
            return JSONResponse({"error": "model_id required"}, status_code=400)
        try:
            from core.symbolic_solver import SymbolicSolver
            solver = SymbolicSolver()
            result = solver.solve(model_id, inputs)
            return result.to_dict()
        except Exception as exc:
            return JSONResponse({"error": str(exc), "success": False}, status_code=500)

    @app.get("/api/faiss/stats")
    async def faiss_stats():
        """Return FAISS collection stats: name, vector count, dimension."""
        try:
            from core.faiss_store import FaissRegistry
            from pathlib import Path
            faiss_dir = Path(__file__).parent.parent / "data" / "faiss"
            if not faiss_dir.exists():
                return {"collections": [], "total_vectors": 0}
            reg = FaissRegistry(base_dir=str(faiss_dir))
            collections = []
            for col_dir in sorted(faiss_dir.iterdir()):
                if col_dir.is_dir():
                    col = reg.get_or_create(col_dir.name)
                    collections.append({"name": col_dir.name, "count": col.count, "dim": col.dim})
            return {"collections": collections, "total_vectors": sum(c["count"] for c in collections)}
        except Exception as exc:
            return JSONResponse({"error": str(exc), "collections": []}, status_code=500)

    @app.post("/api/faiss/search")
    async def faiss_search(request: Request):
        """Semantic search over a FAISS collection using Ollama embeddings."""
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        query = data.get("query", "").strip()
        collection = data.get("collection", "axioms")
        k = min(int(data.get("k", 5)), 20)
        if not query:
            return JSONResponse({"error": "query required"}, status_code=400)
        try:
            import httpx
            from core.faiss_store import FaissRegistry
            from pathlib import Path
            import numpy as np

            # Embed via Ollama
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "http://localhost:11434/api/embed",
                    json={"model": "nomic-embed-text", "input": f"search_query: {query}"},
                )
            emb_data = resp.json()
            embedding = emb_data.get("embeddings", [emb_data.get("embedding", [])])[0]
            if not embedding:
                return JSONResponse({"error": "embedding failed"}, status_code=502)

            vec = np.array(embedding, dtype=np.float32)
            faiss_dir = Path(__file__).parent.parent / "data" / "faiss"
            reg = FaissRegistry(base_dir=str(faiss_dir))
            col = reg.get_or_create(collection)
            results = col.search(vec, k=k)
            return {
                "query": query,
                "collection": collection,
                "results": [{"doc_id": r.doc_id, "text": r.text, "score": round(r.score, 4), "metadata": r.metadata} for r in results],
            }
        except Exception as exc:
            return JSONResponse({"error": str(exc), "results": []}, status_code=500)

    # ═══ v1.18.0: Explainability + Causal Replay + Experiments ═══

    @app.get("/api/route/explain")
    async def route_explain(query: str = ""):
        """Return structured route explanation for a query."""
        if not query:
            return {"error": "query parameter required"}
        try:
            from core.route_explainability import explain_route
            mm_hit, mm_conf = False, 0.0
            try:
                from core.shared_routing_helpers import probe_micromodel
                mm_hit, mm_conf = probe_micromodel(query)
            except Exception:
                pass
            explanation = explain_route(
                query=query,
                hot_cache_hit=False,
                memory_score=0.0,
                micromodel_enabled=True,
                micromodel_hit=mm_hit,
                micromodel_confidence=mm_conf,
                matched_keywords=[],
            )
            return explanation.to_dict()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/route/telemetry")
    async def route_telemetry():
        """Return per-route telemetry stats."""
        try:
            from core.shared_routing_helpers import get_route_telemetry
            return get_route_telemetry().summary()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/experiments")
    async def list_experiments():
        """Return prompt experiment status."""
        try:
            if hivemind and hasattr(hivemind, 'learning') and hivemind.learning:
                status = hivemind.learning.get_status()
                experiments = status.get("experiments", [])
                return {"experiments": experiments, "count": len(experiments)}
            return {"experiments": [], "count": 0}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/graph/replay/{node_id}")
    async def causal_replay(node_id: str):
        """Return causal chain for a node in the cognitive graph."""
        try:
            if hivemind and hasattr(hivemind, 'cognitive_graph') and hivemind.cognitive_graph:
                cg = hivemind.cognitive_graph
                ancestors = cg.find_ancestors(node_id, max_depth=5)
                dependents = cg.find_dependents(node_id, max_depth=5)
                return {
                    "node_id": node_id,
                    "ancestors": ancestors,
                    "dependents": dependents,
                    "chain_length": len(ancestors) + len(dependents),
                }
            return {"node_id": node_id, "error": "cognitive_graph not available"}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/graph/stats")
    async def graph_stats():
        """Return cognitive graph statistics."""
        try:
            if hivemind and hasattr(hivemind, 'cognitive_graph') and hivemind.cognitive_graph:
                return hivemind.cognitive_graph.stats()
            return {"error": "cognitive_graph not available"}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/learning/ledger")
    async def learning_ledger(event_type: str = "", limit: int = 50):
        """Return recent learning ledger entries."""
        try:
            from core.shared_routing_helpers import get_learning_ledger
            ledger = get_learning_ledger()
            entries = ledger.query(
                event_type=event_type or None,
                limit=min(limit, 200),
            )
            return {
                "entries": [e.to_dict() for e in entries],
                "count": len(entries),
                "total": ledger.count(),
            }
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/micromodel/status")
    async def micromodel_status():
        """Return honest V1/V2/V3 micromodel status."""
        from pathlib import Path as P
        status = {"v1": {}, "v2": {}, "v3": {}}
        # V1: PatternMatchEngine
        try:
            from core.shared_routing_helpers import probe_micromodel
            result = probe_micromodel("test")
            status["v1"] = {
                "name": "PatternMatchEngine",
                "status": "wired",
                "config_file": "configs/micro_v1_patterns.json",
                "config_exists": P("configs/micro_v1_patterns.json").exists(),
                "runtime": "legacy + hex (shared_routing_helpers)",
            }
        except Exception as e:
            status["v1"] = {"name": "PatternMatchEngine", "status": "error", "error": str(e)}
        # V2: ClassifierModel
        v2_path = P("data/micromodel_v2.pt")
        status["v2"] = {
            "name": "ClassifierModel",
            "status": "file_exists_unwired" if v2_path.exists() else "not_found",
            "model_file": str(v2_path),
            "file_exists": v2_path.exists(),
            "file_size_mb": round(v2_path.stat().st_size / 1e6, 1) if v2_path.exists() else 0,
            "runtime": "none — no loading code in production runtime",
        }
        # V3: LoRA
        try:
            from core.lora_readiness import LoRAReadinessChecker
            manifest = LoRAReadinessChecker().full_check()
            status["v3"] = {
                "name": "LoRA fine-tuned",
                "status": "ready" if manifest.ready else "not_ready",
                "checks": manifest.to_dict()["checks"],
                "runtime": "none — no trained adapter yet",
            }
        except Exception as e:
            status["v3"] = {"name": "LoRA", "status": "error", "error": str(e)}
        return JSONResponse(status)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        async def ws_callback(data):
            try:
                await websocket.send_json(data)
            except Exception:
                pass
        hivemind.register_ws_callback(ws_callback)
        monitor_ws_callback = None
        if hivemind.monitor:
            async def monitor_ws_callback(e):
                try:
                    await websocket.send_json(e)
                except Exception:
                    pass
            hivemind.monitor.register_callback(monitor_ws_callback)
        if hivemind.ops_agent:
            hivemind.ops_agent.register_decision_callback(ws_callback)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hivemind.unregister_ws_callback(ws_callback)
            if monitor_ws_callback and hivemind.monitor:
                hivemind.monitor.unregister_callback(monitor_ws_callback)
            if hivemind.ops_agent:
                hivemind.ops_agent.unregister_decision_callback(ws_callback)

    # MAGMA Layer 3 routes
    try:
        from backend.routes.magma import register_magma_routes
        register_magma_routes(app, hivemind)
    except Exception as e:
        log.warning(f"MAGMA route not loaded: {e}")

    # MAGMA Layer 4: Cross-agent routes
    try:
        from backend.routes.cross_agent import register_cross_agent_routes
        register_cross_agent_routes(app, hivemind)
    except Exception as e:
        log.warning(f"MAGMA route not loaded: {e}")

    # MAGMA Layer 5: Trust & Reputation routes
    try:
        from backend.routes.trust import register_trust_routes
        register_trust_routes(app, hivemind)
    except Exception as e:
        log.warning(f"MAGMA route not loaded: {e}")

    # MAGMA: Cognitive Graph routes
    try:
        from backend.routes.graph import register_graph_routes
        register_graph_routes(app, hivemind)
    except Exception as e:
        log.warning(f"MAGMA route not loaded: {e}")

    # Analytics routes (file-based, no hivemind dependency)
    try:
        from backend.routes.analytics import router as analytics_router
        app.include_router(analytics_router)
    except Exception as e:
        log.warning(f"Analytics routes not loaded: {e}")

    # Settings routes (reads configs/settings.yaml)
    try:
        from backend.routes.settings import router as settings_router
        app.include_router(settings_router)
    except Exception as e:
        log.warning(f"Settings routes not loaded: {e}")

    # Round Table recent discussions (production: from audit log)
    try:
        _register_round_table_routes(app, hivemind)
    except Exception as e:
        log.warning(f"Round Table routes not loaded: {e}")

    return app


def _register_round_table_routes(app, hivemind):
    """Register production Round Table endpoints using audit + consensus tables."""

    @app.get("/api/round-table/recent")
    async def round_table_recent(limit: int = 5):
        al = getattr(hivemind, '_audit_log', None)
        if not al:
            return {"count": 0, "discussions": []}
        try:
            import time as _t
            now = _t.time()
            # Query audit log for round_table entries (last 7 days)
            entries = al.query_by_time_range(
                now - 7 * 86400, now, agent_id="round_table")
            # Also read consensus table for richer data (match by timestamp)
            consensus_list = []
            try:
                rows = al._conn.execute(
                    "SELECT * FROM consensus ORDER BY timestamp DESC "
                    "LIMIT ?", (limit * 3,)).fetchall()
                consensus_list = [dict(r) for r in rows]
            except Exception:
                pass

            discussions = []
            for e in entries[-limit:]:
                ts = e.get("timestamp", 0)
                # Find nearest consensus entry (within 120s)
                cons = {}
                for c in consensus_list:
                    if abs(c.get("timestamp", 0) - ts) < 120:
                        cons = c
                        break
                agents_str = cons.get("participating_agents", "")
                agents = [a.split("_")[0] for a in agents_str.split(",")
                          ] if agents_str else []
                synthesis = cons.get("synthesis_text",
                                     e.get("details", ""))
                # Build discussion entries for dashboard compatibility
                discussion = []
                if agents:
                    # Show last agent as Coordinator with synthesis
                    for a in agents[:-1]:
                        discussion.append({
                            "agent": a.replace("_", " ").title(),
                            "msg": ""})
                    discussion.append({
                        "agent": "Coordinator",
                        "msg": synthesis[:200]})
                discussions.append({
                    "id": e.get("doc_id", ""),
                    "topic": e.get("details", "")[:120],
                    "consensus": synthesis[:400],
                    "agreement": 1.0 if cons else 0.9,
                    "agents": agents,
                    "agent_count": len(agents),
                    "discussion": discussion,
                    "timestamp": ts,
                })
            return {"count": len(discussions), "discussions": discussions}
        except Exception:
            return {"count": 0, "discussions": []}
