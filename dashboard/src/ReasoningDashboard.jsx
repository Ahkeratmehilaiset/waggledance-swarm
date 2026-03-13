/**
 * WaggleDance — ReasoningDashboard v1.0
 * Production dashboard: real API endpoints, real hardware, real reasoning.
 * Single-file component for v1 validation — refactor into hooks/components after.
 */

import { useState, useEffect, useRef, useCallback } from "react";

// ── Constants ──────────────────────────────────────────────────────────────

const COLORS = {
  bg: "#0A0A0F",
  card: "#111118",
  cardHover: "#1A1A25",
  border: "rgba(255,255,255,0.06)",
  text: "#E2E8F0",
  textMuted: "#64748B",
  textDim: "rgba(255,255,255,0.25)",
};

const LAYER_COLORS = {
  rule_constraints: "#F59E0B",
  model_based:      "#06B6D4",
  statistical:      "#8B5CF6",
  retrieval:        "#10B981",
  llm_reasoning:    "#6366F1",
};

const LAYER_ICONS = {
  rule_constraints: "🔶",
  model_based:      "⚡",
  statistical:      "📊",
  retrieval:        "💾",
  llm_reasoning:    "💬",
};

const BADGES = {
  model_based:       { icon: "⚡", color: "#06B6D4" },
  rule_constraints:  { icon: "🔶", color: "#F59E0B" },
  retrieval:         { icon: "💾", color: "#10B981" },
  hot_cache:         { icon: "🧠", color: "#22D3EE" },
  llm_reasoning:     { icon: "💬", color: "#6366F1" },
  model_based_stale: { icon: "⚠️", color: "#F59E0B" },
  hybrid:            { icon: "🔀", color: "#A78BFA" },
};

const TIERS = [
  { name: "MINIMAL",    vram: "<2 GB",    model: "no LLM",       vram_gb: 0 },
  { name: "LIGHT",      vram: "2–4 GB",   model: "qwen3:0.6b",   vram_gb: 2 },
  { name: "STANDARD",   vram: "4–16 GB",  model: "phi4-mini",    vram_gb: 4 },
  { name: "PROFESS.",   vram: "16–48 GB", model: "phi4:14b",     vram_gb: 16 },
  { name: "ENTERPRISE", vram: "48 GB+",   model: "llama3.3:70b", vram_gb: 48 },
];

const PROFILES = ["gadget", "cottage", "home", "factory"];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Translations ───────────────────────────────────────────────────────────

const T = {
  en: {
    runtimeOps: "Runtime Ops", modelsLoaded: "models loaded",
    purpose: {
      home: "Optimizes energy, comfort and safety. Learns your home's rhythm, detects anomalies, shows impact in euros.",
      cottage: "Monitors conditions, costs, frost risk and anomalies. Generates summaries and forecasts without cloud.",
      factory: "Explains production anomalies, monitors OEE/SPC signals, helps prioritize actions.",
      gadget: "Monitors battery, signal, sensors and device state. Detects drift, predicts issues.",
    },
    quickActions: {
      home:    ["What's the cheapest time to heat today?", "Is energy usage normal this week?", "Is anything unusual in the house?", "What did the system learn this week?"],
      cottage: ["How much has electricity cost so far?", "Is there frost risk tonight?", "What happened at the property in the last 24h?", "Should heating be shifted to cheaper hours?"],
      factory: ["Why did OEE drop today?", "Which signals show drift?", "What should the next shift know?", "What's the biggest recurring issue this week?"],
      gadget:  ["How long will battery last at this usage?", "Is there signal or sensor drift?", "When should this device be serviced?", "How does usage mode affect battery life?"],
    },
    knowsNow: "What it knows now", predicts: "What it predicts",
    learned: "What it learned", underTheHood: "Under the hood",
    learningProgress: "Learning progress", fastPathCoverage: "Fast-path coverage",
    factsPerDay: "facts/day", running: "Running", days: "days",
    recentlyLearned: "Recently learned",
    layers: { rule_constraints: "Rules", model_based: "Model-based", statistical: "Statistical", retrieval: "Retrieval", llm_reasoning: "LLM Reasoning" },
    layerState: { idle: "ready", checking: "checking...", active: "active", skip: "skip", disabled: "disabled" },
    badges: { model_based: "Calculated", rule_constraints: "Rule check", retrieval: "Retrieved", hot_cache: "Known", llm_reasoning: "LLM reasoning", model_based_stale: "Estimated", hybrid: "Hybrid" },
    chosenPath: "Chosen path", decisionTrace: "Decision Trace", send: "Send", askSomething: "Ask something...",
    correctThis: "Correct this", whatWasWrong: "What was wrong?",
    incorrectAnswer: "Answer was incorrect", wrongAssumptions: "Used wrong assumptions",
    missedContext: "Missed important context", other: "Other",
    correctAnswer: "Correct answer (optional)", submit: "Submit", cancel: "Cancel",
    feedbackThanks: "Thanks! This correction has been stored. The system will not repeat this mistake.",
    sourceStatus: { live: "LIVE", building: "BUILDING", notReady: "NOT READY", notConnected: "NOT CONNECTED", stale: "STALE" },
    dailyBrief: "Daily Brief", dismiss: "Dismiss", details: "Details",
    learnedOvernight: "Learned overnight",
    hardware: "Hardware", loadedModels: "Loaded Models", autothrottle: "AutoThrottle",
    performance: "Performance", dataSources: "Data Sources", domainCapsule: "Domain Capsule",
    memoryKnowledge: "Memory & Knowledge", magmaStack: "MAGMA Stack",
    flexhw: "FlexHW Detection", selectedTier: "Selected tier", youAreHere: "You are here",
    upgradePath: "Upgrade path", connecting: "Connecting...", offline: "Offline",
    errors: {
      api_offline:     { title: "System is starting up",       detail: "Loading models and connecting to data sources. Usually takes 30–60 seconds.",    action: "Retrying automatically..." },
      solver_failed:   { title: "Couldn't calculate this one", detail: "The solver needs more information. Try adding specific values.",                   action: "Falling back to AI reasoning..." },
      stale_data:      { title: "Using cached data",           detail: "Some data is outdated. The answer may not reflect current conditions.",            action: "Check your internet connection" },
      no_sensor:       { title: "Sensor not connected",        detail: "Using default values instead of live measurements.",                               action: "Connect sensors in Settings" },
      llm_timeout:     { title: "Taking longer than usual",    detail: "The AI model is processing a complex question.",                                   action: "Still working..." },
      empty_retrieval: { title: "No matching records found",   detail: "The knowledge base doesn't have this yet. It will learn from your question.",      action: "Falling back to AI reasoning..." },
    },
  },
  fi: {
    runtimeOps: "Ajonaikainen tila", modelsLoaded: "mallia ladattu",
    purpose: {
      home: "Optimoi energian, mukavuuden ja turvallisuuden. Oppii kodin rytmin, tunnistaa poikkeamat ja näyttää vaikutukset euroina.",
      cottage: "Valvoo olosuhteita, kustannuksia, jäätymisriskejä ja poikkeamia. Tekee yhteenvedot ja ennusteet ilman pilveä.",
      factory: "Selittää tuotannon poikkeamat, seuraa OEE/SPC-signaaleja ja auttaa priorisoimaan oikeat toimenpiteet.",
      gadget: "Valvoo akkua, signaalia, sensoreita ja laitteen tilaa. Havaitsee driftin ja ennustaa ongelmia.",
    },
    quickActions: {
      home:    ["Milloin lämmitys on halvinta tänään?", "Onko energiankulutus normaalia tällä viikolla?", "Onko talossa jotain poikkeavaa?", "Mitä järjestelmä oppi tällä viikolla?"],
      cottage: ["Paljonko sähkö on maksanut tähän mennessä?", "Onko jäätymisriskiä ensi yönä?", "Mitä mökillä tapahtui viimeisen 24h aikana?", "Kannattaako lämmitys siirtää halvoille tunneille?"],
      factory: ["Miksi OEE laski tänään?", "Mitkä signaalit näyttävät driftia?", "Mitä seuraavan vuoron pitää tietää?", "Mikä on viikon suurin toistuva häiriö?"],
      gadget:  ["Kuinka kauan akku kestää tällä käytöllä?", "Onko signaalissa tai sensorissa driftia?", "Milloin laite kannattaa huoltaa?", "Miten käyttötila vaikuttaa akun kestoon?"],
    },
    knowsNow: "Mitä tietää nyt", predicts: "Mitä ennustaa",
    learned: "Mitä oppinut", underTheHood: "Konepellin alla",
    learningProgress: "Oppimisen edistyminen", fastPathCoverage: "Pikapolun kattavuus",
    factsPerDay: "faktaa/päivä", running: "Käynnissä", days: "päivää",
    recentlyLearned: "Äskettäin opittu",
    layers: { rule_constraints: "Säännöt", model_based: "Mallipohjainen", statistical: "Tilastollinen", retrieval: "Muistihaku", llm_reasoning: "Kielimalli" },
    layerState: { idle: "valmis", checking: "tarkistetaan...", active: "aktiivinen", skip: "ohitettu", disabled: "pois käytöstä" },
    badges: { model_based: "Laskettu", rule_constraints: "Sääntötarkistus", retrieval: "Haettu muistista", hot_cache: "Tunnettu", llm_reasoning: "Kielimalli", model_based_stale: "Arvioitu", hybrid: "Yhdistetty" },
    chosenPath: "Valittu polku", decisionTrace: "Päätöspolku", send: "Lähetä", askSomething: "Kysy jotain...",
    correctThis: "Korjaa tämä", whatWasWrong: "Mikä oli väärin?",
    incorrectAnswer: "Vastaus oli väärä", wrongAssumptions: "Käytti vääriä oletuksia",
    missedContext: "Puuttui tärkeä konteksti", other: "Muu",
    correctAnswer: "Oikea vastaus (vapaaehtoinen)", submit: "Lähetä", cancel: "Peruuta",
    feedbackThanks: "Kiitos! Korjaus on tallennettu. Järjestelmä ei toista tätä virhettä.",
    sourceStatus: { live: "LIVE", building: "KEHITTYY", notReady: "EI VALMIS", notConnected: "EI YHDISTETTY", stale: "VANHENTUNUT" },
    dailyBrief: "Päivän yhteenveto", dismiss: "Sulje", details: "Lisätiedot",
    learnedOvernight: "Opittu yöllä",
    hardware: "Laitteisto", loadedModels: "Ladatut mallit", autothrottle: "Automaattisäätö",
    performance: "Suorituskyky", dataSources: "Datalähteet", domainCapsule: "Domain-kapseli",
    memoryKnowledge: "Muisti ja tieto", magmaStack: "MAGMA-pino",
    flexhw: "FlexHW-tunnistus", selectedTier: "Valittu taso", youAreHere: "Olet tässä",
    upgradePath: "Päivityspolku", connecting: "Yhdistetään...", offline: "Offline",
    errors: {
      api_offline:     { title: "Järjestelmä käynnistyy",        detail: "Ladataan malleja ja yhdistetään datalähteisiin. Kestää yleensä 30–60 sekuntia.", action: "Yritetään uudelleen automaattisesti..." },
      solver_failed:   { title: "Laskenta ei onnistunut",        detail: "Ratkaisija tarvitsee lisätietoja. Kokeile lisätä arvoja.",                        action: "Siirrytään kielimalliin..." },
      stale_data:      { title: "Käytetään välimuistin dataa",   detail: "Osa tiedoista on vanhentunutta. Vastaus ei välttämättä vastaa nykytilannetta.",    action: "Tarkista internetyhteys" },
      no_sensor:       { title: "Sensori ei yhdistetty",         detail: "Käytetään oletusarvoja mittausten sijaan.",                                         action: "Yhdistä sensorit asetuksissa" },
      llm_timeout:     { title: "Kestää tavallista pidempään",   detail: "Kielimalli käsittelee monimutkaista kysymystä.",                                    action: "Käsitellään edelleen..." },
      empty_retrieval: { title: "Vastaavia tietoja ei löytynyt", detail: "Tietokannassa ei ole vielä tietoa tästä. Järjestelmä oppii kysymyksestäsi.",        action: "Siirrytään kielimalliin..." },
    },
  },
};

// ── CSS Keyframes ───────────────────────────────────────────────────────────

const STYLES = `
@keyframes layerPulse {
  0%,100% { box-shadow: 0 0 0 0 rgba(255,255,255,0.08); }
  50%      { box-shadow: 0 0 14px 3px var(--lc30); }
}
@keyframes layerActivate {
  from { max-height: 56px; opacity: 0.7; }
  to   { max-height: 320px; opacity: 1; }
}
@keyframes fadeSlideIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes pulseDot {
  0%,100% { opacity: 1; } 50% { opacity: 0.3; }
}
@keyframes spinDot {
  0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); }
}
@keyframes briefSlide {
  from { transform: translateY(20px); opacity: 0; }
  to   { transform: translateY(0);    opacity: 1; }
}
`;

// ── Helpers ─────────────────────────────────────────────────────────────────

function getAuthHeaders() {
  const key = localStorage.getItem("WAGGLE_API_KEY");
  const h = { "Content-Type": "application/json" };
  if (key) h["Authorization"] = `Bearer ${key}`;
  return h;
}

async function apiFetch(url, opts = {}) {
  try {
    const res = await fetch(url, { headers: getAuthHeaders(), ...opts });
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

function fmt(v, unit = "", decimals = 1) {
  if (v == null || v === undefined || (typeof v === "number" && isNaN(v))) return "—";
  const n = typeof v === "number" ? v.toFixed(decimals) : v;
  return unit ? `${n} ${unit}` : String(n);
}

function timeAgo(ts) {
  if (!ts) return "—";
  const diff = Date.now() - new Date(ts).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1)  return "just now";
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24)   return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function sourceStatusColor(st) {
  return st === "live" ? "#10B981" : st === "building" ? "#F59E0B" : st === "stale" ? "#EF4444" : "#64748B";
}

// ── Sub-components ───────────────────────────────────────────────────────────

function LangToggle({ lang, setLang }) {
  const btn = (l) => ({
    padding: "2px 8px", fontSize: 11,
    fontWeight: lang === l ? 700 : 400,
    background: lang === l ? "rgba(255,255,255,0.12)" : "transparent",
    color: lang === l ? "#E2E8F0" : "#64748B",
    border: "none", borderRadius: 3, cursor: "pointer",
  });
  return (
    <div style={{ display: "flex", gap: 2, background: "rgba(255,255,255,0.06)", borderRadius: 4, padding: 2 }}>
      <button style={btn("en")} onClick={() => setLang("en")}>EN</button>
      <button style={btn("fi")} onClick={() => setLang("fi")}>FI</button>
    </div>
  );
}

function ProfileDropdown({ profile, setProfile, reloadCapsule }) {
  const [open, setOpen] = useState(false);
  const profileColors = { gadget: "#06B6D4", cottage: "#10B981", home: "#6366F1", factory: "#F59E0B" };
  const handleSelect = async (p) => {
    setOpen(false);
    await apiFetch("/api/profile", { method: "POST", body: JSON.stringify({ profile: p }) });
    setProfile(p);
    await reloadCapsule();
  };
  return (
    <div style={{ position: "relative" }}>
      <button onClick={() => setOpen(o => !o)} style={{
        display: "flex", alignItems: "center", gap: 6,
        background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 6, padding: "4px 10px", color: "#E2E8F0", cursor: "pointer", fontSize: 13, fontWeight: 600,
      }}>
        <span style={{ color: profileColors[profile] || "#E2E8F0" }}>●</span>
        {profile.toUpperCase()} ▾
      </button>
      {open && (
        <div style={{
          position: "absolute", top: 34, left: 0, zIndex: 100,
          background: "#1A1A25", border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 8, overflow: "hidden", minWidth: 120,
        }}>
          {PROFILES.map(p => (
            <div key={p} onClick={() => handleSelect(p)} style={{
              padding: "8px 14px", cursor: "pointer", fontSize: 13,
              color: p === profile ? "#E2E8F0" : "#94A3B8",
              background: p === profile ? "rgba(255,255,255,0.08)" : "transparent",
            }}
              onMouseEnter={e => e.currentTarget.style.background = "rgba(255,255,255,0.06)"}
              onMouseLeave={e => e.currentTarget.style.background = p === profile ? "rgba(255,255,255,0.08)" : "transparent"}
            >
              {p.toUpperCase()}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function LayerCard({ layer, queryFlow, t }) {
  const color = LAYER_COLORS[layer.name] || "#64748B";
  const icon = LAYER_ICONS[layer.name] || "◆";
  const isActive = queryFlow.activeLayer === layer.name;
  const isChecking = queryFlow.phase === "checking" && queryFlow.layer === layer.name;
  const isSkipped = queryFlow.skipped?.includes(layer.name);
  const isDone = queryFlow.phase === "done";

  let stateLabel = t.layerState.idle;
  let borderColor = "rgba(255,255,255,0.06)";
  let animation = "";

  if (!layer.enabled) { stateLabel = t.layerState.disabled; }
  else if (isActive && isDone) { stateLabel = t.layerState.active; borderColor = color; }
  else if (isActive) { stateLabel = t.layerState.active; borderColor = color; animation = "layerPulse 1.5s infinite"; }
  else if (isChecking) { stateLabel = t.layerState.checking; borderColor = color; animation = "layerPulse 0.8s infinite"; }
  else if (isSkipped) { stateLabel = t.layerState.skip; borderColor = "rgba(255,255,255,0.03)"; }

  return (
    <div style={{
      "--lc30": color + "50",
      border: `1px solid ${borderColor}`,
      borderRadius: 8, padding: "10px 12px", marginBottom: 6,
      background: isActive ? color + "10" : COLORS.card,
      animation,
      transition: "all 0.25s ease",
      opacity: (!layer.enabled || isSkipped) ? 0.4 : 1,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14 }}>{icon}</span>
          <span style={{ fontSize: 12, fontWeight: 600, color: isActive ? color : "#CBD5E1" }}>
            {t.layers[layer.name] || layer.name}
          </span>
          <span style={{ fontSize: 10, color: "#475569", background: "rgba(255,255,255,0.04)", borderRadius: 3, padding: "1px 5px" }}>
            P{layer.priority}
          </span>
        </div>
        <span style={{
          fontSize: 10, color: isActive ? color : isChecking ? color : "#475569",
          fontStyle: "italic",
        }}>
          {isChecking ? <span style={{ animation: "pulseDot 0.6s infinite" }}>{stateLabel}</span> : stateLabel}
        </span>
      </div>
    </div>
  );
}

function DecisionTrace({ msg, t }) {
  const [open, setOpen] = useState(false);
  const r = msg.routeResult;
  const mr = msg.modelResult;
  const explanation = msg.explanation;
  if (!r && !mr) return null;
  return (
    <div style={{ marginTop: 8 }}>
      <button onClick={() => setOpen(o => !o)} style={{
        background: "none", border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 5, padding: "3px 10px", color: "#94A3B8", fontSize: 11, cursor: "pointer",
      }}>
        📋 {t.decisionTrace} {open ? "▲" : "▼"}
      </button>
      {open && (
        <div style={{
          marginTop: 8, padding: 12, background: "rgba(0,0,0,0.3)",
          border: "1px solid rgba(255,255,255,0.06)", borderRadius: 8,
          fontSize: 11, color: "#94A3B8",
          animation: "fadeSlideIn 0.2s ease",
        }}>
          {r && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ color: "#CBD5E1", fontWeight: 600, marginBottom: 4 }}>🗺 Router</div>
              <div>Layer: <span style={{ color: LAYER_COLORS[r.layer] || "#E2E8F0" }}>{r.layer}</span></div>
              {r.decision_id && <div>Decision: {r.decision_id}</div>}
              {r.confidence != null && <div>Confidence: {(r.confidence * 100).toFixed(0)}%</div>}
              {r.reason && <div>Reason: {r.reason}</div>}
              {r.routing_time_ms != null && <div>Route time: {r.routing_time_ms.toFixed(1)} ms</div>}
            </div>
          )}
          {mr && mr.success && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ color: "#CBD5E1", fontWeight: 600, marginBottom: 4 }}>⚡ Solver</div>
              {mr.formula_used && <div>Formula: <code style={{ color: "#06B6D4" }}>{mr.formula_used}</code></div>}
              {mr.value != null && <div>Result: <strong>{fmt(mr.value, mr.unit)}</strong></div>}
              {mr.risk_level && <div>Risk: <span style={{ color: mr.risk_level === "critical" ? "#EF4444" : mr.risk_level === "warning" ? "#F59E0B" : "#10B981" }}>{mr.risk_level.toUpperCase()}</span></div>}
              {mr.derivation_steps?.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ color: "#CBD5E1", marginBottom: 2 }}>Steps:</div>
                  {mr.derivation_steps.map((s, i) => (
                    <div key={i} style={{ paddingLeft: 8, borderLeft: "2px solid rgba(6,182,212,0.3)", marginBottom: 2 }}>
                      <span style={{ color: "#06B6D4" }}>{s.name}</span>
                      {s.result != null && <span> = {typeof s.result === "number" ? s.result.toFixed(3) : String(s.result)} {s.unit}</span>}
                    </div>
                  ))}
                </div>
              )}
              {mr.inputs_used && Object.keys(mr.inputs_used).length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ color: "#CBD5E1", marginBottom: 2 }}>Inputs:</div>
                  {Object.entries(mr.inputs_used).map(([k, v]) => (
                    <div key={k} style={{ paddingLeft: 8 }}>{k}: <strong>{String(v)}</strong></div>
                  ))}
                </div>
              )}
              {mr.assumptions?.length > 0 && (
                <div style={{ marginTop: 6, color: "#64748B" }}>
                  Defaults used: {mr.assumptions.join(" · ")}
                </div>
              )}
              {mr.validation?.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  {mr.validation.map((v, i) => (
                    <div key={i} style={{ color: v.passed ? "#10B981" : "#EF4444" }}>
                      {v.passed ? "✓" : "✗"} {v.message}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {msg.timing != null && (
            <div style={{ marginTop: 6, color: "#475569" }}>Total: {msg.timing} ms</div>
          )}
        </div>
      )}
    </div>
  );
}

function FeedbackPanel({ msg, t, onFeedback }) {
  const [mode, setMode] = useState(null); // null | "thumbsdown_form" | "sent"
  const [reason, setReason] = useState("");
  const [correction, setCorrection] = useState("");

  const sendFeedback = async (rating) => {
    // rating: 2=up, 1=down
    const payload = {
      message_id: msg.message_id || msg.id || `msg_${Date.now()}`,
      rating,
      correction: correction || null,
    };
    await apiFetch("/api/feedback", { method: "POST", body: JSON.stringify(payload) });
    if (onFeedback) onFeedback(rating);
    setMode("sent");
  };

  if (mode === "sent") {
    return <div style={{ fontSize: 11, color: "#10B981", marginTop: 6 }}>✓ {t.feedbackThanks}</div>;
  }

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button onClick={() => sendFeedback(2)} title="Good answer" style={{
          background: "none", border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 13, color: "#94A3B8",
        }}>👍</button>
        <button onClick={() => setMode(mode === "thumbsdown_form" ? null : "thumbsdown_form")} style={{
          background: mode === "thumbsdown_form" ? "rgba(239,68,68,0.12)" : "none",
          border: "1px solid rgba(255,255,255,0.08)",
          borderRadius: 5, padding: "3px 10px", cursor: "pointer", fontSize: 11, color: "#94A3B8",
        }}>👎 {t.correctThis}</button>
      </div>
      {mode === "thumbsdown_form" && (
        <div style={{
          marginTop: 8, padding: 12, background: "rgba(239,68,68,0.06)",
          border: "1px solid rgba(239,68,68,0.2)", borderRadius: 8,
          animation: "fadeSlideIn 0.2s ease",
        }}>
          <div style={{ fontSize: 12, color: "#CBD5E1", marginBottom: 8, fontWeight: 600 }}>{t.whatWasWrong}</div>
          {[t.incorrectAnswer, t.wrongAssumptions, t.missedContext, t.other].map(opt => (
            <label key={opt} style={{ display: "block", fontSize: 11, color: "#94A3B8", marginBottom: 4, cursor: "pointer" }}>
              <input type="radio" name={`fb_${msg.id}`} value={opt} checked={reason === opt}
                onChange={() => setReason(opt)} style={{ marginRight: 6 }} />
              {opt}
            </label>
          ))}
          <div style={{ marginTop: 8, fontSize: 11, color: "#94A3B8", marginBottom: 4 }}>{t.correctAnswer}</div>
          <textarea value={correction} onChange={e => setCorrection(e.target.value)}
            rows={2} style={{
              width: "100%", background: "#0A0A0F", border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 5, color: "#E2E8F0", fontSize: 11, padding: 6, resize: "vertical",
            }} />
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <button onClick={() => sendFeedback(1)} style={{
              background: "#EF4444", border: "none", borderRadius: 5,
              padding: "4px 12px", color: "white", fontSize: 11, cursor: "pointer",
            }}>{t.submit}</button>
            <button onClick={() => setMode(null)} style={{
              background: "rgba(255,255,255,0.06)", border: "none", borderRadius: 5,
              padding: "4px 12px", color: "#94A3B8", fontSize: 11, cursor: "pointer",
            }}>{t.cancel}</button>
          </div>
        </div>
      )}
    </div>
  );
}

function ResponseBubble({ msg, t, lang }) {
  const badge = BADGES[msg.method] || BADGES.llm_reasoning;
  const badgeLabel = t.badges[msg.method] || msg.method || "LLM";
  const rr = msg.routeResult;
  const mr = msg.modelResult;
  const isError = msg.errorType;

  return (
    <div style={{
      background: COLORS.card, border: `1px solid ${COLORS.border}`,
      borderRadius: 10, padding: "12px 14px", marginBottom: 10,
      animation: "fadeSlideIn 0.25s ease",
    }}>
      {/* Badge + timing */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={{
          background: badge.color + "20", color: badge.color,
          border: `1px solid ${badge.color}40`, borderRadius: 4,
          padding: "2px 8px", fontSize: 11, fontWeight: 600,
        }}>
          {badge.icon} {badgeLabel}{msg.timing != null ? ` · ${msg.timing}ms` : ""}
        </span>
        {rr?.layer && (
          <span style={{ fontSize: 10, color: "#475569" }}>
            {t.chosenPath}: {t.layers[rr.layer] || rr.layer}
            {rr.confidence != null ? ` (${(rr.confidence * 100).toFixed(0)}%)` : ""}
          </span>
        )}
      </div>

      {/* Error box */}
      {isError && (
        <div style={{
          marginBottom: 8, padding: "8px 10px",
          background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)",
          borderRadius: 6,
        }}>
          <div style={{ color: "#FCA5A5", fontWeight: 600, fontSize: 12 }}>⚠️ {t.errors[isError]?.title}</div>
          <div style={{ color: "#94A3B8", fontSize: 11, marginTop: 2 }}>{t.errors[isError]?.detail}</div>
          {t.errors[isError]?.action && <div style={{ color: "#64748B", fontSize: 10, marginTop: 2 }}>🔄 {t.errors[isError].action}</div>}
        </div>
      )}

      {/* Content */}
      <div style={{ fontSize: 13, color: "#CBD5E1", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
        {msg.content}
      </div>

      {/* Solver result highlight */}
      {mr?.success && mr.value != null && (
        <div style={{
          marginTop: 8, padding: "6px 10px",
          background: "rgba(6,182,212,0.08)", border: "1px solid rgba(6,182,212,0.2)",
          borderRadius: 6, display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ color: "#06B6D4", fontWeight: 700, fontSize: 15 }}>
            {typeof mr.value === "number" ? mr.value.toFixed(2) : mr.value} {mr.unit}
          </span>
          {mr.risk_level && mr.risk_level !== "normal" && (
            <span style={{
              fontSize: 11, fontWeight: 600,
              color: mr.risk_level === "critical" ? "#EF4444" : "#F59E0B",
            }}>
              {mr.risk_level === "critical" ? "🔴" : "🟡"} {mr.risk_level.toUpperCase()}
            </span>
          )}
        </div>
      )}

      {/* Trace + feedback */}
      <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
        <DecisionTrace msg={msg} t={t} />
        <FeedbackPanel msg={msg} t={t} />
      </div>
    </div>
  );
}

function LiveDataPanel({ status, feeds, t, lang }) {
  const items = [];
  const raw = status?._raw || {};
  const weather = raw.weather || raw.external_weather;
  const spot = raw.spot_price || raw.electricity;
  const facts = status?.facts;

  // Weather
  if (weather?.temperature != null) {
    items.push({ icon: "🌡", label: lang === "fi" ? "Ulkolämpötila" : "Outdoor temp", value: `${weather.temperature}°C`, status: "live", updated: weather.updated });
  } else {
    items.push({ icon: "🌡", label: lang === "fi" ? "Sää" : "Weather", value: "—", status: "notConnected" });
  }
  // Spot price
  if (spot?.price != null || spot?.current != null) {
    const p = spot.price ?? spot.current;
    items.push({ icon: "⚡", label: lang === "fi" ? "Sähkön hinta" : "Spot price", value: `${p} c/kWh`, status: "live", updated: spot.updated });
  } else {
    items.push({ icon: "⚡", label: lang === "fi" ? "Sähkön hinta" : "Spot price", value: "—", status: "notConnected" });
  }
  // Facts
  if (facts) {
    const st = facts > 1000 ? "live" : facts > 100 ? "building" : "notReady";
    items.push({ icon: "📚", label: lang === "fi" ? "Opitut faktat" : "Learned facts", value: facts.toLocaleString(), status: st });
  }
  // Feeds
  if (feeds?.feeds) {
    Object.entries(feeds.feeds).slice(0, 2).forEach(([k, v]) => {
      items.push({ icon: "📡", label: k, value: v.status || "—", status: v.enabled ? "live" : "notConnected" });
    });
  }

  const stLabel = (s) => t.sourceStatus[s] || s;
  const stColor = (s) => sourceStatusColor(s);
  const stIcon = (s) => s === "live" ? "●" : s === "building" ? "◐" : s === "stale" ? "⚠" : "○";

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#64748B", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
        {t.knowsNow}
      </div>
      {items.map((item, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
          <span style={{ fontSize: 12, color: "#94A3B8" }}>{item.icon} {item.label}</span>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "#CBD5E1", fontWeight: 500 }}>{item.value}</span>
            <span style={{ fontSize: 10, color: stColor(item.status), fontWeight: 600 }}>
              {stIcon(item.status)} {stLabel(item.status)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function PredictionsPanel({ status, t, lang, profile }) {
  const [solveResult, setSolveResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const prevInputKey = useRef("");

  const modelMap = { home: "heat_pump_cop", cottage: "pipe_freezing", gadget: "battery_discharge", factory: "oee_decomposition" };
  const modelId = modelMap[profile] || "heat_pump_cop";

  useEffect(() => {
    const raw = status?._raw || {};
    const temp = raw.weather?.temperature ?? raw.external_weather?.temperature;
    const spot = raw.spot_price?.price ?? raw.electricity?.current;
    const inputKey = `${modelId}:${temp}:${spot}`;
    if (inputKey === prevInputKey.current) return;
    prevInputKey.current = inputKey;

    const inputs = {};
    if (temp != null) inputs.T_outdoor = temp;
    if (spot != null) inputs.spot_price_ckwh = spot;
    if (!Object.keys(inputs).length && !solveResult) return;

    setLoading(true);
    apiFetch("/api/solve", {
      method: "POST", body: JSON.stringify({ model_id: modelId, inputs }),
    }).then(r => {
      setSolveResult(r);
      setLoading(false);
    }).catch(() => { setSolveResult(null); setLoading(false); });
  }, [status, modelId]);

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#64748B", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
        {t.predicts}
      </div>
      {loading && <div style={{ fontSize: 11, color: "#475569" }}>⚡ calculating...</div>}
      {!loading && solveResult?.success && (
        <div style={{ padding: "8px 10px", background: "rgba(6,182,212,0.06)", border: "1px solid rgba(6,182,212,0.15)", borderRadius: 7 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "#94A3B8" }}>{modelId.replace(/_/g, " ")}</span>
            <span style={{
              fontSize: 14, fontWeight: 700,
              color: solveResult.risk_level === "critical" ? "#EF4444" : solveResult.risk_level === "warning" ? "#F59E0B" : "#06B6D4",
            }}>
              {typeof solveResult.value === "number" ? solveResult.value.toFixed(2) : solveResult.value} {solveResult.unit}
            </span>
          </div>
          {solveResult.risk_level && solveResult.risk_level !== "normal" && (
            <div style={{ fontSize: 11, color: "#64748B", marginTop: 3 }}>
              Risk: {solveResult.risk_level.toUpperCase()}
            </div>
          )}
        </div>
      )}
      {!loading && !solveResult?.success && (
        <div style={{ fontSize: 11, color: "#475569", fontStyle: "italic" }}>
          {solveResult?.error || (lang === "fi" ? "Ei dataa" : "No live data available")}
        </div>
      )}
    </div>
  );
}

function LearningPanel({ status, microModel, learning, t, lang }) {
  const facts = status?.facts || 0;
  const v1 = microModel?.stats || {};
  const lookups = v1.lookup_count || 0;
  const coverage = facts > 0 ? Math.min(100, Math.round((lookups / facts) * 100)) : 0;
  const leaderboard = learning?.leaderboard?.slice(0, 4) || [];

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#64748B", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
        {t.learned}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontSize: 12, color: "#94A3B8" }}>{t.learningProgress}</span>
        <span style={{ fontSize: 12, color: "#CBD5E1", fontWeight: 600 }}>{facts.toLocaleString()} facts</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: "#94A3B8" }}>{t.fastPathCoverage}</span>
        <span style={{ fontSize: 12, color: coverage > 50 ? "#10B981" : "#F59E0B", fontWeight: 600 }}>{coverage}%</span>
      </div>
      {/* Progress bar */}
      <div style={{ height: 4, background: "rgba(255,255,255,0.06)", borderRadius: 2, marginBottom: 8, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${coverage}%`, background: coverage > 50 ? "#10B981" : "#F59E0B", borderRadius: 2, transition: "width 0.8s ease" }} />
      </div>
      {leaderboard.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: "#475569", marginBottom: 4 }}>{t.recentlyLearned}:</div>
          {leaderboard.map((item, i) => (
            <div key={i} style={{ fontSize: 10, color: "#64748B", paddingLeft: 8, borderLeft: "2px solid rgba(99,102,241,0.4)", marginBottom: 2 }}>
              {item.agent_id || item.source || "?"}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UnderTheHoodPanel({ capsule, magma, status, models, t, lang }) {
  const items = [];
  if (capsule?.domain) items.push({ label: "Capsule", value: `${capsule.domain} v${capsule.version || "?"}` });
  if (capsule?.layers) items.push({ label: lang === "fi" ? "Kerrokset" : "Layers", value: `${capsule.layers.length} · ${capsule.models_count || 0} solvers` });
  const memCount = status?._raw?.consciousness?.memory_count || status?.facts || 0;
  if (memCount) items.push({ label: lang === "fi" ? "Muisti" : "Memory", value: memCount.toLocaleString() });
  if (magma?.cognitive_graph?.nodes != null) items.push({ label: "Cog. Graph", value: `${magma.cognitive_graph.nodes} nodes` });
  if (magma?.trust_engine?.total_signals != null) items.push({ label: "Trust", value: `${magma.trust_engine.total_signals} signals` });
  if (magma?.audit_entries != null) items.push({ label: "Audit", value: `${magma.audit_entries} entries` });

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#64748B", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
        {t.underTheHood}
      </div>
      {items.map((item, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid rgba(255,255,255,0.02)" }}>
          <span style={{ fontSize: 11, color: "#475569" }}>{item.label}</span>
          <span style={{ fontSize: 11, color: "#94A3B8", fontWeight: 500 }}>{item.value}</span>
        </div>
      ))}
    </div>
  );
}

function RuntimeOpsPanel({ hw, status, models, capsule, magma, throttleHistory, t, lang, onClose }) {
  const vramGb = hw?.vram_total || 0;
  const tierIndex = TIERS.findIndex(tier => vramGb >= tier.vram_gb);
  const activeTier = tierIndex >= 0 ? Math.min(tierIndex, TIERS.length - 1) : 0;

  return (
    <div style={{
      position: "fixed", top: 0, right: 0, width: 360, height: "100vh",
      background: "#0D0D15", border: "1px solid rgba(255,255,255,0.08)",
      borderRadius: "12px 0 0 12px", overflowY: "auto", zIndex: 200, padding: 20,
      boxShadow: "-4px 0 40px rgba(0,0,0,0.6)",
      animation: "fadeSlideIn 0.2s ease",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "#E2E8F0" }}>⚙ {t.runtimeOps}</span>
        <button onClick={onClose} style={{ background: "none", border: "none", color: "#64748B", cursor: "pointer", fontSize: 18 }}>✕</button>
      </div>

      {/* FlexHW */}
      <Section title={`🖥 ${t.flexhw}`}>
        <div style={{ fontSize: 11, color: "#64748B", marginBottom: 8 }}>{t.selectedTier}: {TIERS[activeTier]?.name}</div>
        {TIERS.map((tier, i) => (
          <div key={tier.name} style={{
            display: "flex", alignItems: "center", gap: 8, padding: "4px 8px", borderRadius: 5, marginBottom: 2,
            background: i === activeTier ? "rgba(99,102,241,0.15)" : "transparent",
            border: i === activeTier ? "1px solid rgba(99,102,241,0.4)" : "1px solid transparent",
          }}>
            {i === activeTier && <span style={{ color: "#6366F1", fontSize: 10, fontWeight: 700 }}>►</span>}
            <span style={{ fontSize: 11, color: i === activeTier ? "#E2E8F0" : "#475569", fontWeight: i === activeTier ? 700 : 400 }}>{tier.name}</span>
            <span style={{ fontSize: 10, color: "#475569", marginLeft: "auto" }}>{tier.vram} · {tier.model}</span>
          </div>
        ))}
        {hw?.vram_total && <div style={{ fontSize: 10, color: "#64748B", marginTop: 6 }}>{t.youAreHere}: {hw.vram_total} GB VRAM</div>}
      </Section>

      {/* Hardware */}
      <Section title={`💻 ${t.hardware}`}>
        <Row label="GPU" value={hw?.gpu_name || "—"} />
        <Row label="VRAM" value={hw?.vram != null ? `${hw.vram} / ${hw.vram_total || "?"} GB` : "—"} />
        <Row label="CPU" value={hw?.cpu_model || "—"} />
        <Row label="RAM" value={hw?.ram_total_gb ? `${hw.ram_total_gb} GB` : "—"} />
        <Row label="CPU util" value={hw?.cpu != null ? `${hw.cpu}%` : "—"} />
        <Row label="GPU util" value={hw?.gpu != null ? `${hw.gpu}%` : "—"} />
      </Section>

      {/* Models */}
      <Section title={`🤖 ${t.loadedModels}`}>
        {Array.isArray(models) && models.length > 0 ? models.map((m, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", fontSize: 11 }}>
            <span style={{ color: "#94A3B8" }}>{m.name || m.model_id || "?"}</span>
            <span style={{ color: "#475569" }}>{m.role || m.type || "—"}</span>
          </div>
        )) : <div style={{ fontSize: 11, color: "#475569" }}>—</div>}
      </Section>

      {/* AutoThrottle */}
      <Section title={`⚖ ${t.autothrottle}`}>
        <Row label={lang === "fi" ? "Luokka" : "Class"} value={status?.machine_class || "—"} />
        <Row label={lang === "fi" ? "HB-väli" : "HB interval"} value={status?.heartbeat_interval ? `${status.heartbeat_interval}s` : "—"} />
        {throttleHistory?.slice(0, 3).map((ev, i) => (
          <div key={i} style={{ fontSize: 10, color: "#475569", padding: "2px 0" }}>
            {ev.type || ev.event}: {ev.reason || ""} {ev.time ? `@${ev.time}` : ""}
          </div>
        ))}
      </Section>

      {/* Performance */}
      <Section title={`📈 ${t.performance}`}>
        <Row label={lang === "fi" ? "Pyyn. yhteensä" : "Total requests"} value={status?.total_requests ?? "—"} />
        <Row label={lang === "fi" ? "Virheitä" : "Errors"} value={status?.total_errors ?? "—"} />
        <Row label="Cache hit" value={status?.cache_hit_rate || "—"} />
        <Row label="Latency" value={status?.avg_latency_ms != null ? `${status.avg_latency_ms}ms` : "—"} />
      </Section>

      {/* Domain Capsule */}
      {capsule && (
        <Section title={`📦 ${t.domainCapsule}`}>
          <Row label="Domain" value={capsule.domain || "—"} />
          <Row label="Version" value={capsule.version || "—"} />
          <Row label={lang === "fi" ? "Kerrokset" : "Layers"} value={capsule.layers?.length ?? "—"} />
          <Row label="Rules" value={capsule.rules_count ?? "—"} />
          <Row label="Models" value={capsule.models_count ?? "—"} />
          <Row label="Sources" value={capsule.data_sources_count ?? "—"} />
        </Section>
      )}

      {/* Memory */}
      <Section title={`🧠 ${t.memoryKnowledge}`}>
        <Row label="Facts" value={status?.facts?.toLocaleString() || "—"} />
        <Row label="Cache hit" value={status?.cache_hit_rate || "—"} />
        <Row label="Corrections" value={status?.corrections ?? "—"} />
        <Row label="Cog. Graph" value={magma?.cognitive_graph?.nodes != null ? `${magma.cognitive_graph.nodes} nodes` : "—"} />
      </Section>

      {/* MAGMA */}
      {magma && (
        <Section title={`🔮 ${t.magmaStack}`}>
          <Row label="Audit entries" value={magma.audit_entries ?? "—"} />
          <Row label="Trust agents" value={magma.trust_engine?.agents_tracked ?? "—"} />
          <Row label="Trust signals" value={magma.trust_engine?.total_signals ?? "—"} />
          <Row label="Overlays" value={magma.overlays ?? "—"} />
          <Row label="Replay wired" value={magma.replay_wired ? "✓" : "—"} />
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 16, paddingBottom: 16, borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#475569", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "2px 0", fontSize: 11 }}>
      <span style={{ color: "#475569" }}>{label}</span>
      <span style={{ color: "#94A3B8", fontWeight: 500 }}>{value ?? "—"}</span>
    </div>
  );
}

function MorningBrief({ status, learning, t, lang, onDismiss }) {
  const facts = status?.facts || 0;
  const nightFacts = status?._raw?.night_mode?.stored_last_night || status?._raw?.enrichment?.total_stored || 0;
  return (
    <div style={{
      position: "fixed", bottom: 20, left: "50%", transform: "translateX(-50%)",
      background: "#1A1A2E", border: "1px solid rgba(99,102,241,0.4)",
      borderRadius: 12, padding: "16px 24px", zIndex: 150, minWidth: 380, maxWidth: 520,
      animation: "briefSlide 0.3s ease", boxShadow: "0 8px 40px rgba(0,0,0,0.6)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "#E2E8F0" }}>🌅 {t.dailyBrief}</span>
        <button onClick={onDismiss} style={{ background: "none", border: "none", color: "#64748B", cursor: "pointer", fontSize: 13 }}>{t.dismiss}</button>
      </div>
      <div style={{ fontSize: 12, color: "#94A3B8", lineHeight: 1.6 }}>
        {nightFacts > 0 && <div>🧠 {t.learnedOvernight}: <strong style={{ color: "#E2E8F0" }}>{nightFacts.toLocaleString()}</strong> {lang === "fi" ? "faktaa" : "facts"}</div>}
        <div>📚 {lang === "fi" ? "Yhteensä" : "Total"}: <strong style={{ color: "#E2E8F0" }}>{facts.toLocaleString()}</strong> {lang === "fi" ? "faktaa" : "facts"}</div>
        {learning?.leaderboard?.[0] && <div>🏆 {lang === "fi" ? "Aktiivisin" : "Top agent"}: {learning.leaderboard[0].agent_id || "—"}</div>}
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function ReasoningDashboard({ onSwitchView } = {}) {
  const [hw, setHw] = useState(null);
  const [status, setStatus] = useState(null);
  const [capsule, setCapsule] = useState(null);
  const [magma, setMagma] = useState(null);
  const [learning, setLearning] = useState(null);
  const [microModel, setMicroModel] = useState(null);
  const [models, setModels] = useState([]);
  const [throttleHistory, setThrottleHistory] = useState([]);
  const [feeds, setFeeds] = useState(null);
  const [chatHistory, setChatHistory] = useState([]);
  const [queryFlow, setQueryFlow] = useState({ phase: "idle", layer: null, skipped: [], activeLayer: null, currentLayerIndex: 0 });
  const [runtimeOpsOpen, setRuntimeOpsOpen] = useState(false);
  const [briefDismissed, setBriefDismissed] = useState(false);
  const [profile, setProfile] = useState("home");
  const [lang, setLang] = useState("en");
  const [inputMsg, setInputMsg] = useState("");
  const [backendOnline, setBackendOnline] = useState(true);
  const chatEndRef = useRef(null);
  const sendingRef = useRef(false);

  // ── Data fetching ──────────────────────────────────────────────────────────

  const reloadCapsule = useCallback(async () => {
    const cap = await apiFetch("/api/capsule");
    if (cap) setCapsule(cap);
  }, []);

  useEffect(() => {
    const fetchAll = async () => {
      const [hwR, stR, capR, magR, learnR, microR, modR, thrR, feedsR] = await Promise.allSettled([
        apiFetch("/api/hardware"),
        apiFetch("/api/status"),
        apiFetch("/api/capsule"),
        apiFetch("/api/magma/stats"),
        apiFetch("/api/learning"),
        apiFetch("/api/micro_model"),
        apiFetch("/api/models"),
        apiFetch("/api/monitor/history"),
        apiFetch("/api/feeds"),
      ]);
      const get = (r) => r.status === "fulfilled" ? r.value : null;
      const hwD = get(hwR); const stD = get(stR);
      if (hwD) setHw(hwD);
      if (stD) { setStatus(stD); setBackendOnline(true); }
      else setBackendOnline(false);
      if (get(capR)) {
        const cap = get(capR);
        setCapsule(cap);
        if (cap.domain) setProfile(cap.domain);
      }
      if (get(magR)) setMagma(get(magR));
      if (get(learnR)) setLearning(get(learnR));
      if (get(microR)) setMicroModel(get(microR));
      const modD = get(modR);
      if (modD) setModels(Array.isArray(modD) ? modD : modD.models || []);
      const thrD = get(thrR);
      if (thrD) setThrottleHistory(Array.isArray(thrD) ? thrD : thrD.events || []);
      if (get(feedsR)) setFeeds(get(feedsR));
    };
    fetchAll();
    const iv = setInterval(fetchAll, 5000);
    return () => clearInterval(iv);
  }, []);

  // scroll to bottom on new messages
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory]);

  // ── Send query ─────────────────────────────────────────────────────────────

  const sendQuery = useCallback(async (message) => {
    if (!message.trim() || sendingRef.current) return;
    sendingRef.current = true;
    setInputMsg("");

    // Add user message
    const userMsg = { role: "user", content: message, id: `u_${Date.now()}` };
    setChatHistory(h => [...h, userMsg]);

    const startTime = Date.now();
    setQueryFlow({ phase: "routing", layer: null, skipped: [], activeLayer: null, currentLayerIndex: 0 });

    // Route query
    const routeData = await apiFetch(`/api/route?q=${encodeURIComponent(message)}`);
    const targetLayer = routeData?.result?.layer || "llm_reasoning";

    // Animate layers
    const layers = (capsule?.layers || []).slice().sort((a, b) => a.priority - b.priority);
    const skipped = [];
    for (let i = 0; i < layers.length; i++) {
      const layer = layers[i];
      setQueryFlow(f => ({ ...f, phase: "checking", layer: layer.name, currentLayerIndex: i }));
      await sleep(160);
      if (layer.name === targetLayer) {
        setQueryFlow(f => ({ ...f, phase: "solving", activeLayer: layer.name, currentLayerIndex: i }));
        break;
      }
      skipped.push(layer.name);
      setQueryFlow(f => ({ ...f, skipped: [...skipped] }));
      await sleep(90);
    }

    // Chat request
    const chatData = await apiFetch("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, language: lang }),
    });

    const elapsed = Date.now() - startTime;
    const errorType = (!chatData || chatData.error) ? "api_offline" : null;

    const assistantMsg = {
      role: "assistant",
      content: chatData?.response || chatData?.message || (errorType ? "" : "No response"),
      method: chatData?.method || targetLayer,
      modelResult: chatData?.model_result || null,
      explanation: chatData?.explanation || null,
      routeResult: routeData?.result || null,
      timing: elapsed,
      id: `a_${Date.now()}`,
      message_id: chatData?.message_id || null,
      errorType,
    };
    setChatHistory(h => [...h, assistantMsg]);
    setQueryFlow({ phase: "done", layer: targetLayer, skipped, activeLayer: targetLayer, currentLayerIndex: 0 });
    setTimeout(() => setQueryFlow({ phase: "idle", layer: null, skipped: [], activeLayer: null, currentLayerIndex: 0 }), 3000);
    sendingRef.current = false;
  }, [capsule, lang]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuery(inputMsg); }
  };

  const t = T[lang] || T.en;
  const layers = (capsule?.layers || []).slice().sort((a, b) => a.priority - b.priority);
  const quickActions = (capsule?.key_decisions?.map(d => d.question) || t.quickActions[profile] || []).slice(0, 4);
  const showBrief = !briefDismissed && status && (status.facts > 0);

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: COLORS.bg, color: COLORS.text, fontFamily: "system-ui, -apple-system, sans-serif", overflow: "hidden" }}>
      <style>{STYLES}</style>

      {/* ── Topbar ─────────────────────────────────────────────────────── */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 16px", height: 52, flexShrink: 0,
        background: "#0D0D15", borderBottom: "1px solid rgba(255,255,255,0.06)",
        position: "sticky", top: 0, zIndex: 50,
      }}>
        {/* Left: logo + profile */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 200 }}>
          <span style={{ fontSize: 18 }}>🐝</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: "#E2E8F0", letterSpacing: 0.5 }}>WaggleDance</span>
          <ProfileDropdown profile={profile} setProfile={setProfile} reloadCapsule={reloadCapsule} />
          <LangToggle lang={lang} setLang={setLang} />
        </div>

        {/* Center: hw stats */}
        <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 11, color: "#64748B" }}>
          {hw?.gpu_name && <span style={{ color: "#94A3B8" }}>🖥 {hw.gpu_name}</span>}
          {hw?.vram != null && <span>VRAM {hw.vram}/{hw.vram_total || "?"}GB</span>}
          {hw?.ram_total_gb != null && <span>RAM {hw.ram_total_gb}GB</span>}
          {models.length > 0 && <span>{models.length} {t.modelsLoaded}</span>}
          <span style={{ color: status?.machine_class ? "#06B6D4" : "#475569" }}>
            {status?.machine_class || t.connecting}
          </span>
          {!backendOnline && (
            <span style={{ color: "#EF4444", animation: "pulseDot 1s infinite" }}>● {t.offline}</span>
          )}
        </div>

        {/* Right: view toggle + runtime ops */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 120, justifyContent: "flex-end" }}>
          {onSwitchView && (
            <button onClick={onSwitchView} style={{
              background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 6, padding: "4px 10px", color: "#94A3B8", cursor: "pointer",
              fontSize: 11, fontWeight: 500, letterSpacing: 0.5,
            }}>
              ← Classic
            </button>
          )}
          <button onClick={() => setRuntimeOpsOpen(o => !o)} style={{
            background: runtimeOpsOpen ? "rgba(99,102,241,0.2)" : "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6,
            padding: "4px 12px", color: "#CBD5E1", cursor: "pointer", fontSize: 12, fontWeight: 500,
          }}>
            ⚙ {t.runtimeOps}
          </button>
        </div>
      </div>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>

        {/* ── Left panel ──────────────────────────────────────────────── */}
        <div style={{
          width: 300, flexShrink: 0, borderRight: "1px solid rgba(255,255,255,0.06)",
          overflowY: "auto", padding: "14px 12px", background: "#0D0D15",
        }}>
          {/* Purpose card */}
          <div style={{
            marginBottom: 16, padding: "10px 12px",
            background: COLORS.card, border: "1px solid rgba(255,255,255,0.06)",
            borderRadius: 8,
          }}>
            <div style={{ fontSize: 10, color: "#64748B", marginBottom: 4, textTransform: "uppercase", letterSpacing: 1 }}>
              {profile.toUpperCase()}
            </div>
            <div style={{ fontSize: 12, color: "#94A3B8", lineHeight: 1.5 }}>{t.purpose[profile]}</div>
          </div>

          {/* Reasoning layers */}
          {layers.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#64748B", textTransform: "uppercase", letterSpacing: 1, marginBottom: 8 }}>
                {lang === "fi" ? "Päättelykerrokset" : "Reasoning Layers"}
              </div>
              {layers.map(layer => (
                <LayerCard key={layer.name} layer={layer} queryFlow={queryFlow} t={t} />
              ))}
            </div>
          )}

          <LiveDataPanel status={status} feeds={feeds} t={t} lang={lang} />
          <PredictionsPanel status={status} t={t} lang={lang} profile={profile} />
          <LearningPanel status={status} microModel={microModel} learning={learning} t={t} lang={lang} />
          <UnderTheHoodPanel capsule={capsule} magma={magma} status={status} models={models} t={t} lang={lang} />
        </div>

        {/* ── Center: chat ─────────────────────────────────────────────── */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* Messages */}
          <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px" }}>
            {/* Quick actions — shown when chat is empty */}
            {chatHistory.length === 0 && (
              <div style={{ marginBottom: 24 }}>
                <div style={{ fontSize: 11, color: "#475569", marginBottom: 10, textAlign: "center" }}>
                  {lang === "fi" ? "Kysymysehdotuksia:" : "Quick questions:"}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
                  {quickActions.map((q, i) => (
                    <button key={i} onClick={() => sendQuery(q)} style={{
                      background: COLORS.card, border: "1px solid rgba(255,255,255,0.08)",
                      borderRadius: 8, padding: "8px 14px", color: "#94A3B8",
                      cursor: "pointer", fontSize: 12, textAlign: "left",
                      transition: "all 0.15s", maxWidth: 280,
                    }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = "rgba(99,102,241,0.4)"; e.currentTarget.style.color = "#CBD5E1"; }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)"; e.currentTarget.style.color = "#94A3B8"; }}
                    >{q}</button>
                  ))}
                </div>
              </div>
            )}

            {/* Chat messages */}
            {chatHistory.map((msg) => msg.role === "user" ? (
              <div key={msg.id} style={{ textAlign: "right", marginBottom: 10 }}>
                <span style={{
                  display: "inline-block", background: "rgba(99,102,241,0.2)",
                  border: "1px solid rgba(99,102,241,0.3)", borderRadius: 10,
                  padding: "8px 14px", fontSize: 13, color: "#CBD5E1", maxWidth: "70%", textAlign: "left",
                }}>{msg.content}</span>
              </div>
            ) : (
              <ResponseBubble key={msg.id} msg={msg} t={t} lang={lang} />
            ))}

            {/* Sending indicator */}
            {queryFlow.phase !== "idle" && queryFlow.phase !== "done" && (
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 14px", marginBottom: 10 }}>
                <div style={{ width: 8, height: 8, borderRadius: "50%", border: "2px solid #6366F1", borderTopColor: "transparent", animation: "spinDot 0.8s linear infinite" }} />
                <span style={{ fontSize: 12, color: "#64748B" }}>
                  {queryFlow.phase === "routing" ? (lang === "fi" ? "Reititetään..." : "Routing...") :
                   queryFlow.phase === "checking" ? `${lang === "fi" ? "Tarkistetaan" : "Checking"} ${t.layers[queryFlow.layer] || queryFlow.layer}...` :
                   queryFlow.phase === "solving" ? (lang === "fi" ? "Lasketaan..." : "Solving...") : ""}
                </span>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Input */}
          <div style={{
            padding: "12px 16px", borderTop: "1px solid rgba(255,255,255,0.06)",
            background: "#0D0D15", flexShrink: 0,
          }}>
            <div style={{ display: "flex", gap: 10 }}>
              <textarea
                value={inputMsg}
                onChange={e => setInputMsg(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={t.askSomething}
                rows={2}
                style={{
                  flex: 1, background: COLORS.card, border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 8, padding: "10px 14px", color: "#E2E8F0",
                  fontSize: 13, resize: "none", fontFamily: "inherit",
                  outline: "none",
                }}
                onFocus={e => e.target.style.borderColor = "rgba(99,102,241,0.5)"}
                onBlur={e => e.target.style.borderColor = "rgba(255,255,255,0.08)"}
              />
              <button
                onClick={() => sendQuery(inputMsg)}
                disabled={!inputMsg.trim() || sendingRef.current}
                style={{
                  background: inputMsg.trim() ? "#6366F1" : "rgba(99,102,241,0.3)",
                  border: "none", borderRadius: 8, padding: "0 18px",
                  color: "white", cursor: inputMsg.trim() ? "pointer" : "default",
                  fontSize: 13, fontWeight: 600, transition: "background 0.15s",
                }}
              >{t.send}</button>
            </div>
          </div>
        </div>
      </div>

      {/* ── Runtime Ops slide-out ────────────────────────────────────── */}
      {runtimeOpsOpen && (
        <RuntimeOpsPanel
          hw={hw} status={status} models={models} capsule={capsule}
          magma={magma} throttleHistory={throttleHistory}
          t={t} lang={lang}
          onClose={() => setRuntimeOpsOpen(false)}
        />
      )}

      {/* ── Morning Brief ─────────────────────────────────────────────── */}
      {showBrief && (
        <MorningBrief
          status={status} learning={learning} t={t} lang={lang}
          onDismiss={() => setBriefDismissed(true)}
        />
      )}
    </div>
  );
}
