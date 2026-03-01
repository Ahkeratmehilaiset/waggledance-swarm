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

  const failCount = useRef(0);
  const lastRetry = useRef(0);
  const pollingStarted = useRef(false);

  const fetchJson = useCallback(async (url) => {
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }, []);

  // Poll backend APIs
  const poll = useCallback(async () => {
    if (!backendAvailable && failCount.current >= MAX_FAILURES) {
      const now = Date.now();
      if (now - lastRetry.current < RETRY_INTERVAL) return;
      lastRetry.current = now;
    }

    const [statusData, hbData, hwData] = await Promise.all([
      fetchJson("/api/status"),
      fetchJson("/api/heartbeat"),
      fetchJson("/api/hardware"),
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

  const sendChat = useCallback(async (message, lang = "auto") => {
    if (!message.trim()) return null;
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, lang }),
      });
      if (res.ok) {
        const data = await res.json();
        return data.response || data.error || "...";
      }
    } catch {
      // fallback
    }
    return "HiveMind not active yet...";
  }, []);

  return {
    backendAvailable,
    backendMode,
    status,
    heartbeats,
    hardware,
    sendChat,
  };
}
