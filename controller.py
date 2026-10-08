"""
controller.py
Converts activation scalar + threshold into clean trigger events.

Implements:
- fast-attack / slow-release envelope: the raw activation (90 ms window) is
  bursty, so a long clench briefly dips below the hysteresis level and used to
  re-arm the controller mid-clench, which then fired again on the next burst.
  The release envelope ignores dips shorter than ~150 ms.
- sustained threshold crossing (hold_ms) for the onset
- refractory period (debounce)
- hysteresis + minimum re-arm time: the envelope must stay below
  threshold × release_ratio for rearm_ms before the next trigger can fire
"""

import math
import time


class TriggerController:
    """
    Call update(activation) each frame.
    Returns True exactly once per muscle contraction.
    """

    def __init__(self, threshold: float = 50.0, refractory_ms: int = 300,
                 release_ratio: float = 0.7, hold_ms: int = 70,
                 release_tau_ms: int = 160, rearm_ms: int = 120):
        self.threshold = threshold
        self.refractory_ms = refractory_ms
        self.release_ratio = release_ratio
        self.hold_ms = hold_ms
        self.release_tau_ms = release_tau_ms
        self.rearm_ms = rearm_ms

        self.envelope: float = 0.0          # slow-release envelope (read by the game)
        self._last_t: float | None = None
        self._last_trigger_time: float = 0.0
        self._armed: bool = True
        self._triggered: bool = False
        self._above_since: float | None = None
        self._below_since: float | None = None

    def update(self, activation: float) -> bool:
        now = time.monotonic()
        self._triggered = False

        dt = 0.0 if self._last_t is None else max(0.0, now - self._last_t)
        self._last_t = now
        decay = math.exp(-dt / (self.release_tau_ms / 1000.0)) if dt > 0 else 1.0
        self.envelope = max(activation, self.envelope * decay)

        refractory_elapsed = (now - self._last_trigger_time) * 1000 >= self.refractory_ms

        # Re-arm only after the envelope has stayed below the release level
        if not self._armed:
            if self.envelope < self.threshold * self.release_ratio:
                if self._below_since is None:
                    self._below_since = now
                elif (now - self._below_since) * 1000 >= self.rearm_ms:
                    self._armed = True
                    self._above_since = None
                    self._below_since = None
            else:
                self._below_since = None

        # Onset uses the fast activation so triggers stay quick
        if activation >= self.threshold:
            if self._above_since is None:
                self._above_since = now
        else:
            self._above_since = None

        held_long_enough = (
            self._above_since is not None and
            (now - self._above_since) * 1000 >= self.hold_ms
        )
        if self._armed and refractory_elapsed and held_long_enough:
            self._triggered = True
            self._last_trigger_time = now
            self._armed = False
            self._above_since = None
            self._below_since = None

        return self._triggered

    def is_active(self, activation: float) -> bool:
        return activation >= self.threshold

    def is_held(self, ratio: float = 0.85) -> bool:
        """True while the clench is still on, judged by the slow-release envelope."""
        return self.envelope >= self.threshold * ratio

    def reset(self):
        self.envelope = 0.0
        self._last_t = None
        self._last_trigger_time = 0.0
        self._armed = True
        self._triggered = False
        self._above_since = None
        self._below_since = None

    def set_threshold(self, threshold: float):
        self.threshold = max(1.0, threshold)
