import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL = 2000;
const RETRY_INTERVAL = 30000;
const MAX_FAILURES = 3;

export function useApi() {
  const [backendAvailable, setBackendAvailable] = useState(false);
  const [backendMode, setBackendMode] = useState("unknown"); // "stub" | "production" | "unknown"
  const [status, setStatus] = useState({ facts: 0, cpu: 0, gpu: 0, vram: 0, agents_active: 0, is_thinking: false });
  const [heartbeats, setHeartbeats] = useState([]);
  const [hardware, setHardware] = useState({ cpu: 0, gpu: 0, vram: 0 });
  const [sensors, setSensors] = useState({ available: false, status: {} });
  const [voiceStatus, setVoiceStatus] = useState({ available: false, stt_available: false, tts_available: false });
  const [audioStatus, setAudioStatus] = useState({ available: false, status: {} });
  const [analytics, setAnalytics] = useState(null);
  const [roundTable, setRoundTable] = useState(null);
  const [agentLevels, setAgentLevels] = useState(null);
  const [settings, setSettings] = useState(null);
  const [profile, setProfile] = useState(null);
  const [models, setModels] = useState(null);

  const failCount = useRef(0);
  const lastRetry = useRef(0);
  const pollingStarted = useRef(false);

  const getAuthHeaders = useCallback(() => {
    const token = localStorage.getItem("WAGGLE_API_KEY") || "";
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
  }, []);

  const fetchJson = useCallback(async (url) => {
    try {
      const res = await fetch(url, { headers: getAuthHeaders() });
      if (res.status === 401) {
        // Token missing or invalid — prompt once
        if (!localStorage.getItem("_waggle_auth_warned")) {
          localStorage.setItem("_waggle_auth_warned", "1");
          console.warn("WaggleDance: API returned 401. Set localStorage WAGGLE_API_KEY.");
        }
        return null;
      }
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }, [getAuthHeaders]);

  // Poll backend APIs
  const poll = useCallback(async () => {
    if (!backendAvailable && failCount.current >= MAX_FAILURES) {
      const now = Date.now();
      if (now - lastRetry.current < RETRY_INTERVAL) return;
      lastRetry.current = now;
    }

    const [statusData, hbData, hwData, sensorData, voiceData, audioData, analyticsData, rtData, agentsData, settingsData, profileData, modelsData] = await Promise.all([
      fetchJson("/api/status"),
      fetchJson("/api/heartbeat"),
      fetchJson("/api/hardware"),
      fetchJson("/api/sensors"),
      fetchJson("/api/voice/status"),
      fetchJson("/api/sensors/audio"),
      fetchJson("/api/analytics/trends"),
      fetchJson("/api/round-table/recent"),
      fetchJson("/api/agents/levels"),
      fetchJson("/api/settings"),
      fetchJson("/api/profile"),
      fetchJson("/api/models"),
    ]);

    if (statusData) {
      failCount.current = 0;
      setBackendAvailable(true);
      setBackendMode(statusData.mode || "production");

      // Parse HiveMind's /api/status into flat format for React components
      const throttle = statusData.throttle || {};
      const agents = statusData.agents || {};
      const consciousness = statusData.consciousness || {};
      setStatus({
        facts: consciousness.memories || consciousness.memory_count || statusData.facts || hwData?.facts || 0,
        cpu: hwData?.cpu || 0,
        gpu: hwData?.gpu || 0,
        vram: hwData?.vram || 0,
        agents_active: agents.active || 0,
        agents_total: agents.total || 0,
        is_thinking: (agents.active || 0) > 0,
        machine_class: throttle.machine_class || "unknown",
        heartbeat_interval: throttle.heartbeat_interval_s || 60,
        avg_latency_ms: throttle.avg_latency_ms || 0,
        total_requests: throttle.total_requests || 0,
        total_errors: throttle.total_errors || 0,
        uptime: statusData.uptime || "",
        hallucinations_caught: consciousness.hallucinations_caught || 0,
        total_queries: consciousness.total_queries || 0,
        cache_hit_rate: consciousness.cache_hit_rate || "0%",
        corrections: consciousness.corrections || 0,
        insights_stored: consciousness.insights_stored || 0,
        micro_model: consciousness.micro_model || {},
        _raw: statusData,
      });
    } else {
      failCount.current++;
      if (failCount.current >= MAX_FAILURES) {
        setBackendAvailable(false);
        lastRetry.current = Date.now();
      }
    }

    // Heartbeat feed — real agent insights
    if (hbData && Array.isArray(hbData) && hbData.length > 0) {
      const converted = hbData.map((h) => ({
        a: h.agent || h.a || "System",
        m: h.message || h.m || "",
        t: h.type || h.t || "status",
        role: h.role || null,
        ph: h.pheromone != null ? h.pheromone : null,
      }));
      setHeartbeats(converted.slice(0, 6));
    }

    // Sensor hub status
    if (sensorData) {
      setSensors(sensorData);
    }

    // Voice interface status
    if (voiceData) {
      setVoiceStatus(voiceData);
    }

    // Audio monitor status
    if (audioData) {
      setAudioStatus(audioData);
    }

    // Analytics, Round Table, Agent Levels, Settings
    if (analyticsData) setAnalytics(analyticsData);
    if (rtData) setRoundTable(rtData);
    if (agentsData) setAgentLevels(agentsData);
    if (settingsData) setSettings(settingsData);
    if (profileData) setProfile(profileData);
    if (modelsData) setModels(modelsData);

    // Hardware stats
    if (hwData) {
      setHardware({
        cpu: hwData.cpu || 0,
        gpu: hwData.gpu || 0,
        vram: hwData.vram || 0,
        vram_total: hwData.vram_total || 0,
        ram_gb: hwData.ram_gb || 0,
        machine_class: hwData.machine_class || "",
        facts: hwData.facts || 0,
        total_requests: hwData.total_requests || 0,
      });
    }
  }, [backendAvailable, fetchJson]);

  useEffect(() => {
    if (pollingStarted.current) return;
    pollingStarted.current = true;
    poll();
    const interval = setInterval(poll, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [poll]);

  const switchProfile = useCallback(async (profileName) => {
    try {
      await fetch("/api/profile", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ profile: profileName }),
      });
    } catch { /* ignore */ }
  }, [getAuthHeaders]);

  const sendChat = useCallback(async (message, lang = "auto") => {
    if (!message.trim()) return null;
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ message, lang }),
      });
      if (res.ok) {
        const data = await res.json();
        return {
          text: data.response || data.error || "...",
          message_id: data.message_id || null,
          conversation_id: data.conversation_id || null,
        };
      }
    } catch {
      // fallback
    }
    return { text: "HiveMind not active yet...", message_id: null };
  }, [getAuthHeaders]);

  const sendFeedback = useCallback(async (messageId, rating, correction = null) => {
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ message_id: messageId, rating, correction }),
      });
    } catch { /* ignore */ }
  }, [getAuthHeaders]);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch("/api/history/recent/messages", {
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        return data.messages || [];
      }
    } catch { /* ignore */ }
    return [];
  }, [getAuthHeaders]);

  return {
    backendAvailable,
    backendMode,
    status,
    heartbeats,
    hardware,
    sensors,
    voiceStatus,
    audioStatus,
    analytics,
    roundTable,
    agentLevels,
    settings,
    sendChat,
    sendFeedback,
    loadHistory,
    switchProfile,
    profile,
    models,
  };
}
