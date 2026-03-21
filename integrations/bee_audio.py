"""
WaggleDance — Phase 6: Bee Audio Analyzer
==========================================
Analyzes ESP32 microphone FFT spectrum data for bee colony health monitoring.
Detects stress, swarming, queen piping, and queenless states by comparing
frequency patterns against a rolling 7-day baseline per hive.

Frequency reference:
  Normal hum: 200-500 Hz (~250 Hz fundamental)
  Queen piping: 400-500 Hz pulsed
  Swarming: broad 200-600 Hz, increased amplitude
  Stress (varroa): fundamental shifts ~+50 Hz
"""

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("bee_audio")


@dataclass
class BeeAudioResult:
    """Result of a single spectrum analysis."""
    hive_id: str = ""
    stress_level: float = 0.0       # 0.0-1.0
    fundamental_hz: float = 250.0
    status: str = "normal"          # normal/stressed/swarming/queenless/queen_piping
    anomaly: bool = False
    confidence: float = 0.0
    description_fi: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# Finnish status descriptions
_STATUS_FI = {
    "normal": "Normaali humina",
    "stressed": "Stressaantunut — perustaajuus kohonnut",
    "swarming": "Parveilun merkkejä — laaja taajuuskaista",
    "queenless": "Mahdollisesti orvotettu — epäsäännöllinen humina",
    "queen_piping": "Kuningattaren piippaus havaittu",
}


class BeeAudioAnalyzer:
    """Analyzes bee colony audio spectrums for health monitoring."""

    # Frequency ranges (Hz)
    NORMAL_HZ = (200, 500)
    QUEEN_PIPING_HZ = (400, 500)
    SWARMING_HZ = (200, 600)
    STRESS_SHIFT_HZ = 50

    # Thresholds
    SWARMING_AMPLITUDE_THRESHOLD = 0.7
    QUEEN_PIPING_PULSED_THRESHOLD = 0.6

    def __init__(self, config: dict):
        """
        Args:
            config: audio.bee_audio section from settings.yaml
        """
        self._config = config
        self._baseline_days = config.get("baseline_days", 7)
        self._stress_threshold = config.get("stress_threshold_hz_shift", 30)
        self._swarming_threshold = config.get(
            "swarming_amplitude_threshold", self.SWARMING_AMPLITUDE_THRESHOLD
        )

        # Per-hive baselines: hive_id -> deque of (timestamp, fundamental_hz)
        self._baselines: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=1000)
        )
        # Per-hive latest status
        self._hive_status: dict[str, BeeAudioResult] = {}

        self._total_analyses = 0
        self._anomalies_detected = 0
        log.info("BeeAudioAnalyzer initialized (baseline_days=%d, stress_threshold=%d Hz)",
                 self._baseline_days, self._stress_threshold)

    def analyze_spectrum(self, spectrum: list[dict], hive_id: str) -> BeeAudioResult:
        """Analyze FFT spectrum data for a hive.

        Args:
            spectrum: List of {"hz": float, "amplitude": float} dicts
            hive_id: Identifier for the hive

        Returns:
            BeeAudioResult with status and metrics
        """
        self._total_analyses += 1

        if not spectrum:
            return BeeAudioResult(
                hive_id=hive_id,
                confidence=0.0,
                description_fi="Ei spektridataa",
            )

        # Find fundamental frequency (highest amplitude in normal range)
        fundamental = self._find_fundamental(spectrum)

        # Get baseline for comparison
        baseline = self._get_baseline_hz(hive_id)

        # Detect states
        is_swarming = self._detect_swarming(spectrum)
        is_queen_piping = self._detect_queen_piping(spectrum)
        stress_level, stress_status = self._detect_stress(fundamental, baseline)

        # Determine overall status (priority: queen_piping > swarming > stressed > normal)
        if is_queen_piping:
            status = "queen_piping"
            confidence = 0.85
        elif is_swarming:
            status = "swarming"
            confidence = 0.90
        elif stress_level > 0.5:
            status = "stressed"
            confidence = 0.80
        else:
            status = "normal"
            confidence = 0.95

        anomaly = status != "normal"
        if anomaly:
            self._anomalies_detected += 1

        result = BeeAudioResult(
            hive_id=hive_id,
            stress_level=round(stress_level, 3),
            fundamental_hz=round(fundamental, 1),
            status=status,
            anomaly=anomaly,
            confidence=confidence,
            description_fi=_STATUS_FI.get(status, status),
        )

        # Update hive status cache
        self._hive_status[hive_id] = result

        if anomaly:
            log.warning("Hive %s anomaly: %s (fundamental=%.1f Hz, stress=%.2f)",
                        hive_id, status, fundamental, stress_level)

        return result

    def update_baseline(self, hive_id: str, spectrum: list[dict]):
        """Update rolling baseline with current spectrum.

        Args:
            hive_id: Hive identifier
            spectrum: FFT spectrum data
        """
        if not spectrum:
            return

        fundamental = self._find_fundamental(spectrum)
        self._baselines[hive_id].append((time.time(), fundamental))

    def get_hive_status(self, hive_id: str) -> dict:
        """Get latest status for a specific hive.

        Returns:
            Dict with status fields or empty defaults
        """
        result = self._hive_status.get(hive_id)
        if not result:
            return {
                "hive_id": hive_id,
                "status": "unknown",
                "stress_level": 0.0,
                "fundamental_hz": 0.0,
                "anomaly": False,
                "description_fi": "Ei dataa",
            }
        return {
            "hive_id": result.hive_id,
            "status": result.status,
            "stress_level": result.stress_level,
            "fundamental_hz": result.fundamental_hz,
            "anomaly": result.anomaly,
            "confidence": result.confidence,
            "description_fi": result.description_fi,
            "timestamp": result.timestamp,
        }

    def _find_fundamental(self, spectrum: list[dict]) -> float:
        """Find fundamental frequency (highest amplitude in 100-800 Hz range)."""
        candidates = [
            s for s in spectrum
            if 100 <= s.get("hz", 0) <= 800
        ]
        if not candidates:
            return 250.0  # default
        best = max(candidates, key=lambda s: s.get("amplitude", 0))
        return best.get("hz", 250.0)

    def _get_baseline_hz(self, hive_id: str) -> Optional[float]:
        """Get baseline fundamental Hz from rolling window."""
        samples = self._baselines.get(hive_id)
        if not samples or len(samples) < 3:
            return None

        # Filter to baseline_days window
        cutoff = time.time() - (self._baseline_days * 86400)
        recent = [hz for ts, hz in samples if ts >= cutoff]

        if len(recent) < 3:
            return None

        return sum(recent) / len(recent)

    def _detect_stress(self, fundamental: float, baseline: Optional[float]) -> tuple:
        """Detect stress from frequency shift.

        Returns:
            (stress_level: 0.0-1.0, description: str)
        """
        if baseline is None:
            return (0.0, "no_baseline")

        shift = fundamental - baseline
        if shift <= 0:
            return (0.0, "normal")

        # Normalize shift to 0.0-1.0 based on threshold
        stress = min(1.0, shift / (self._stress_threshold * 2))
        if shift >= self._stress_threshold:
            return (stress, "stressed")

        return (stress, "mild")

    def _detect_swarming(self, spectrum: list[dict]) -> bool:
        """Detect swarming: broad energy across 200-600 Hz with high amplitude."""
        swarming_band = [
            s for s in spectrum
            if self.SWARMING_HZ[0] <= s.get("hz", 0) <= self.SWARMING_HZ[1]
        ]
        if len(swarming_band) < 3:
            return False

        amplitudes = [s.get("amplitude", 0) for s in swarming_band]
        avg_amp = sum(amplitudes) / len(amplitudes)
        max_amp = max(amplitudes) if amplitudes else 0

        # Swarming: high average amplitude AND broad distribution
        spread = max_amp - min(amplitudes) if amplitudes else 0
        broad = spread < (max_amp * 0.5) if max_amp > 0 else False

        return avg_amp >= self._swarming_threshold and broad

    def _detect_queen_piping(self, spectrum: list[dict]) -> bool:
        """Detect queen piping: sharp peaks in 400-500 Hz range."""
        piping_band = [
            s for s in spectrum
            if self.QUEEN_PIPING_HZ[0] <= s.get("hz", 0) <= self.QUEEN_PIPING_HZ[1]
        ]
        if not piping_band:
            return False

        amplitudes = [s.get("amplitude", 0) for s in piping_band]
        max_amp = max(amplitudes) if amplitudes else 0

        # Queen piping: sharp peak (high max, low average outside peak)
        outside = [
            s.get("amplitude", 0) for s in spectrum
            if s.get("hz", 0) < 400 or s.get("hz", 0) > 500
        ]
        outside_avg = sum(outside) / len(outside) if outside else 0

        # Peak in piping range should be significantly higher than outside
        return (max_amp >= self.QUEEN_PIPING_PULSED_THRESHOLD
                and max_amp > outside_avg * 2.5)

    @property
    def stats(self) -> dict:
        return {
            "total_analyses": self._total_analyses,
            "anomalies_detected": self._anomalies_detected,
            "hives_tracked": len(self._hive_status),
            "baseline_entries": sum(len(v) for v in self._baselines.values()),
        }
