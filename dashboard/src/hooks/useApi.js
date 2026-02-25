import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL = 1500;
const RETRY_INTERVAL = 30000;
const MAX_FAILURES = 3;

export function useApi() {
  const [backendAvailable, setBackendAvailable] = useState(false);
  const [status, setStatus] = useState({ facts: 0, cpu: 0, gpu: 0, vram: 0, agents_active: 6, is_thinking: false });
  const [heartbeats, setHeartbeats] = useState([]);
  const [hardware, setHardware] = useState({ cpu: 0, gpu: 0, vram: 0 });

  const failCount = useRef(0);
  const lastRetry = useRef(0);
  const pollingStarted = useRef(false);

  // Try fetching a JSON endpoint, return null on failure
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
    // If backend previously failed too many times, retry less often
    if (!backendAvailable && failCount.current >= MAX_FAILURES) {
      const now = Date.now();
      if (now - lastRetry.current < RETRY_INTERVAL) return;
      lastRetry.current = now;
    }

    // Try all endpoints in parallel
    const [statusData, hbData, hwData] = await Promise.all([
      fetchJson("/api/status"),
      fetchJson("/api/heartbeat"),
      fetchJson("/api/hardware"),
    ]);

    if (statusData) {
      failCount.current = 0;
      setBackendAvailable(true);
      setStatus(statusData);
    } else {
      failCount.current++;
      if (failCount.current >= MAX_FAILURES) {
        setBackendAvailable(false);
        lastRetry.current = Date.now();
      }
      // Mock status fallback — only hardware gauges
      setStatus((prev) => ({
        ...prev,
        cpu: 8 + Math.floor(Math.random() * 15),
        gpu: 48 + Math.floor(Math.random() * 18),
        vram: +(4 + Math.random() * 0.6).toFixed(1),
        is_thinking: Math.random() > 0.6,
      }));
    }

    if (hbData && Array.isArray(hbData) && hbData.length > 0) {
      // Convert backend format {agent, message, type} to component format {a, m, t}
      const converted = hbData.map((h) => ({
        a: h.agent || h.a || "System",
        m: h.message || h.m || "",
        t: h.type || h.t || "status",
        role: h.role || null,
        ph: h.pheromone != null ? h.pheromone : null,
      }));
      setHeartbeats(converted.slice(0, 6));
    }

    if (hwData) {
      setHardware(hwData);
    }
  }, [backendAvailable, fetchJson]);

  // Start polling
  useEffect(() => {
    if (pollingStarted.current) return;
    pollingStarted.current = true;

    // Initial poll
    poll();

    const interval = setInterval(poll, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [poll]);

  // Send chat message
  const sendChat = useCallback(async (message) => {
    if (!message.trim()) return null;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      if (res.ok) {
        const data = await res.json();
        return data.response || data.error || "...";
      }
    } catch {
      // fallback
    }

    // Mock response if backend unavailable
    return "Järjestelmä käynnistyy... HiveMind ei vielä aktiivinen.";
  }, []);

  return {
    backendAvailable,
    status,
    heartbeats,
    hardware,
    sendChat,
  };
}
