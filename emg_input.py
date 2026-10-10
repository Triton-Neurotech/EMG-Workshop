"""
emg_input.py — EMG input (MindRove / OpenBCI Ganglion) with threaded pull/peek buffering.

Backends (auto order):
1. "lsl"       LSL stream — MindRove Connect, or OpenBCI GUI → Networking → LSL
2. "udp"       OpenBCI GUI → Networking → UDP (JSON time-series on 127.0.0.1:12345)
3. "sdk"       Direct MindRove SDK (armband Wi-Fi)
4. "synthetic" Fallback

"ganglion" is explicit-only (python main.py --ganglion): connects to the Ganglion
directly over Bluetooth via BrainFlow. The OpenBCI GUI must NOT be connected to the
board at the same time — BLE allows one host connection.

pull()  → returns only NEW samples since last call (used for activation processing)
peek(n) → returns last n samples from display buffer (used for plotting, non-destructive)
peek_smooth(n, latency_s) → like peek, but the window end is placed by a wall-clock
        model of the sample stream instead of "whatever arrived last", so a display
        that calls it every frame scrolls at a constant speed even though hardware
        delivers samples in irregular bursts.

Threaded acquisition keeps the incoming stream fresh even when the UI renders
at 60 FPS, which reduces the "chunky" feel from polling hardware in the main loop.
"""

import os
import json
import socket
import time
import threading
import numpy as np
from scipy.signal import lfilter, lfilter_zi, butter, iirnotch

DISPLAY_SECS = 5      # seconds of history in the display ring buffer
MAX_CHANNELS  = 8     # hard cap — UI supports up to 8 channels

try:
    from pylsl import StreamInlet, resolve_byprop, resolve_streams, proc_clocksync, proc_dejitter
    _LSL_AVAILABLE = True
except ImportError:
    _LSL_AVAILABLE = False

try:
    from mindrove.board_shim import BoardShim, MindRoveInputParams, BoardIds
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False

try:
    from brainflow.board_shim import (
        BoardShim as BFBoardShim, BrainFlowInputParams, BoardIds as BFBoardIds,
    )
    _BRAINFLOW_AVAILABLE = True
except ImportError:
    _BRAINFLOW_AVAILABLE = False

UDP_DEFAULT_HOST = "127.0.0.1"
UDP_DEFAULT_PORT = 12345          # OpenBCI GUI Networking widget default
KNOWN_RATES      = (125, 200, 250, 500, 1000, 1600)
# Front-end filter for raw OpenBCI sources (MindRove Connect already filters).
# 20 Hz high-pass: removes electrode offset, drift AND motion artefacts, which on
# plain gel electrodes are several times larger than the EMG itself and were
# triggering jumps on hand movement rather than on muscle activity.
# 60 Hz notch: mains hum was ~15% of the signal power at rest.
EMG_HIGHPASS_HZ  = 20.0
MAINS_HZ         = 60.0


class EMGInput:
    def __init__(self, synthetic: bool = False, preferred_backend: str = "auto",
                 udp_host: str = UDP_DEFAULT_HOST, udp_port: int = UDP_DEFAULT_PORT,
                 ganglion_mac: str = "", ganglion_serial: str = "",
                 ganglion_serial_port: str = "", ganglion_timeout: int = 15,
                 quiet: bool = False):
        self.synthetic = synthetic
        self.quiet = quiet                     # synthetic: baseline noise only, no fake flexes
        self.preferred_backend = preferred_backend
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.ganglion_mac = ganglion_mac
        self.ganglion_serial = ganglion_serial
        self.ganglion_serial_port = ganglion_serial_port
        self.ganglion_timeout = ganglion_timeout

        self.board = None
        self.board_id = None
        self.emg_channels: list[int] = []
        self.channel_labels: list[str] = []
        self.sampling_rate: int = 500
        self.backend: str = "synthetic" if synthetic else "uninitialized"
        self.source_name: str = "synthetic"

        self._running = False
        self._acq_thread: threading.Thread | None = None

        self._disp_buf: np.ndarray | None = None
        self._disp_lock = threading.Lock()
        self._n_total: int = 0                 # samples ever appended
        self.last_sample_time: float = 0.0     # monotonic time of the last chunk
        self._clock_offset: float | None = None  # wall time of "sample 0", low-pass filtered

        self._pending_buf: np.ndarray | None = None
        self._pending_lock = threading.Lock()
        self._channel_indices: list[int] = []

        # LSL state
        self._lsl_inlet = None

        # UDP state (OpenBCI GUI JSON)
        self._udp_sock: socket.socket | None = None

        # Optional per-channel front-end filters for raw OpenBCI sources:
        # a cascade of (b, a) sections with per-channel state. None = disabled.
        self._hp_ba: list[tuple[np.ndarray, np.ndarray]] | None = None
        self._hp_zi: list[np.ndarray] | None = None

        # Synthetic state
        self._synth_t = 0.0

    # ── Public API ──────────────────────────────────────────────────────────────

    def start(self):
        if self.synthetic:
            self._start_synthetic()
            return

        started = False
        pref = self.preferred_backend
        if pref in ("auto", "lsl"):
            backends = ["lsl", "udp", "sdk"]
        elif pref == "sdk":
            backends = ["sdk", "lsl", "udp"]
        elif pref == "synthetic":
            backends = []
        else:
            backends = [pref]           # explicit single backend (udp / ganglion)

        for backend in backends:
            if backend == "lsl":
                if not _LSL_AVAILABLE:
                    print("[EMGInput] pylsl not installed — skipping LSL backend.")
                    continue
                started = self._start_lsl()
            elif backend == "udp":
                started = self._start_udp()
            elif backend == "sdk":
                if not _SDK_AVAILABLE:
                    print("[EMGInput] mindrove SDK not installed — skipping SDK backend.")
                    continue
                started = self._start_sdk()
            elif backend == "ganglion":
                if not _BRAINFLOW_AVAILABLE:
                    print("[EMGInput] brainflow not installed — run: pip install brainflow")
                    continue
                started = self._start_ganglion()
            else:
                print(f"[EMGInput] Unknown backend '{backend}'.")
            if started:
                break

        if not started:
            print("[EMGInput] Falling back to synthetic mode.")
            self._start_synthetic()

    def pull(self) -> np.ndarray:
        """
        Returns ONLY new samples since last call as (n_channels, n_new).
        May return a 0-column array if no new data arrived.
        """
        with self._pending_lock:
            if self._pending_buf is None:
                return np.zeros((self.n_channels, 0))
            pending = self._pending_buf.copy()
            self._pending_buf = np.zeros((self.n_channels, 0))
        return pending

    def peek(self, n: int | None = None) -> np.ndarray:
        """
        Non-destructive read of the display buffer.
        Returns last n samples (all channels) as (n_channels, n).
        """
        with self._disp_lock:
            if self._disp_buf is None:
                return np.zeros((self.n_channels, n or 1))
            if n is None:
                return self._disp_buf.copy()
            return self._disp_buf[:, -n:].copy()

    def peek_smooth(self, n: int, latency_s: float = 0.30) -> np.ndarray:
        """
        Last n samples ending at (now - latency_s) according to the stream clock model.
        Returns (n_channels, n); zero-padded on the left if the buffer is short.
        """
        with self._disp_lock:
            if self._disp_buf is None:
                return np.zeros((self.n_channels, n))
            buf   = self._disp_buf
            total = self._n_total
            off   = self._clock_offset
            sr    = max(1, self.sampling_rate)
            L     = buf.shape[1]

            if off is None or total == 0:
                return buf[:, -n:].copy()

            # Sample index that should be at the right edge of the display now.
            end = int((time.monotonic() - latency_s - off) * sr)
            end = max(0, min(end, total))
            behind = total - end                 # samples newer than the display edge
            if behind >= L:
                return np.zeros((self.n_channels, n))
            stop_i  = L - behind
            start_i = stop_i - n
            if start_i >= 0:
                return buf[:, start_i:stop_i].copy()
            out = np.zeros((self.n_channels, n))
            out[:, -stop_i:] = buf[:, :stop_i]
            return out

    def stop(self):
        self._running = False

        if self._acq_thread is not None and self._acq_thread.is_alive():
            self._acq_thread.join(timeout=0.5)

        if self.board is not None and self.backend in ("sdk", "ganglion"):
            try:
                self.board.stop_stream()
                self.board.release_session()
            except Exception:
                pass

        if self._udp_sock is not None:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None

        self._lsl_inlet = None
        print(f"[EMGInput] Stopped ({self.backend}).")

    @property
    def n_channels(self) -> int:
        return len(self.emg_channels)

    def is_alive(self, max_silence_s: float = 2.0) -> bool:
        """True if samples arrived recently (synthetic always counts as alive)."""
        if self.backend == "synthetic":
            return True
        return (time.monotonic() - self.last_sample_time) < max_silence_s

    def _wait_for_data(self, timeout_s: float) -> bool:
        """Block until the first chunk arrives, or give up."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            if self._n_total > 0:
                return True
            time.sleep(0.05)
        return False

    def select_channels(self, logical: list[int]):
        """
        Keep only the given channels (indices into the current channel list).
        Used to show just the wired-up electrodes, e.g. a single Ganglion channel.
        """
        logical = [i for i in dict.fromkeys(logical) if 0 <= i < self.n_channels]
        if not logical or logical == list(range(self.n_channels)):
            return
        with self._pending_lock, self._disp_lock:
            self._channel_indices = [self._channel_indices[i] for i in logical]
            self.channel_labels   = [self.channel_labels[i] for i in logical]
            self.emg_channels     = list(range(len(logical)))
            if self._disp_buf is not None:
                self._disp_buf = np.ascontiguousarray(self._disp_buf[logical, :])
            self._pending_buf = np.zeros((len(logical), 0))
            if self._hp_zi is not None:
                self._hp_zi = [None if z is None else np.ascontiguousarray(z[logical, :])
                               for z in self._hp_zi]
        print(f"[EMGInput] Showing channels: {', '.join(self.channel_labels)}")

    # ── Backend setup ───────────────────────────────────────────────────────────

    def _start_lsl(self) -> bool:
        try:
            stream = self._resolve_lsl_stream()
            if stream is None:
                print("[EMGInput] No LSL stream found.")
                return False

            self._lsl_inlet = StreamInlet(
                stream,
                max_buflen=DISPLAY_SECS + 1,
                processing_flags=proc_clocksync | proc_dejitter,
            )
            total_channels = int(stream.channel_count())
            # Expose ALL channels from the stream (up to MAX_CHANNELS).
            # MindRove Connect may include non-EMG channels at index 0 — we show
            # everything and let the user pick the right channel via the UI selector.
            n_ch = min(total_channels, MAX_CHANNELS)
            self._channel_indices = list(range(n_ch))
            self.emg_channels = list(range(n_ch))
            self.channel_labels = [f"CH{i + 1}" for i in range(n_ch)]
            self.sampling_rate = int(stream.nominal_srate()) if stream.nominal_srate() > 0 else 500
            self.synthetic = False
            self.backend = "lsl"
            self.source_name = f"{stream.name()} ({stream.type()})"

            ident = f"{stream.name()} {stream.source_id()}".lower()
            if (any(k in ident for k in ("obci", "openbci", "ganglion", "cyton"))
                    or stream.type().lower() == "eeg"):
                # OpenBCI GUI streams raw samples with a large electrode offset.
                self._enable_dc_highpass()

            self._init_buffers()
            self._running = True
            self._acq_thread = threading.Thread(target=self._lsl_loop, daemon=True)
            self._acq_thread.start()

            if not self._wait_for_data(3.0):
                print(f"[EMGInput] LSL stream '{stream.name()}' found but it is not sending "
                      "samples (is the data stream started in the source app?) — skipping.")
                self._running = False
                self._acq_thread.join(timeout=0.5)
                self._lsl_inlet = None
                return False
            # Let a short buffer fill, then report which channels have signal.
            time.sleep(0.3)
            self.pull()   # drain stale startup data
            self._print_channel_report()
            return True
        except Exception as e:
            print(f"[EMGInput] LSL connection failed: {e}")
            self._lsl_inlet = None
            return False

    def _start_sdk(self) -> bool:
        try:
            BoardShim.disable_board_logger()
            params = MindRoveInputParams()
            self.board_id = BoardIds.MINDROVE_WIFI_BOARD
            self.board = BoardShim(self.board_id, params)
            self.board.prepare_session()
            self.board.start_stream()
            sdk_emg = list(BoardShim.get_emg_channels(self.board_id))
            # Cap to MAX_CHANNELS but keep the SDK's correct channel indices
            self._channel_indices = sdk_emg[:MAX_CHANNELS]
            self.emg_channels = list(range(len(self._channel_indices)))
            self.channel_labels = [f"CH{i + 1}" for i in range(len(self._channel_indices))]
            self.sampling_rate = int(BoardShim.get_sampling_rate(self.board_id))
            self.synthetic = False
            self.backend = "sdk"
            self.source_name = "MindRove SDK"

            self._init_buffers()
            self._running = True
            self._acq_thread = threading.Thread(target=self._sdk_loop, daemon=True)
            self._acq_thread.start()

            if not self._wait_for_data(2.5):
                # prepare_session() succeeds with no armband on the network — treat
                # "no samples" as not connected so we fall through to the next backend.
                print("[EMGInput] MindRove SDK session opened but no data arrived "
                      "(armband not on this Wi-Fi?) — skipping.")
                self._running = False
                self._acq_thread.join(timeout=0.5)
                raise RuntimeError("no data from MindRove SDK")
            time.sleep(0.3)
            self.pull()
            self._print_channel_report()
            return True
        except Exception as e:
            print(f"[EMGInput] SDK connection failed: {e}")
            if self.board is not None:
                try:
                    self.board.release_session()
                except Exception:
                    pass
            self.board = None
            return False

    def _start_ganglion(self) -> bool:
        """Direct Bluetooth connection to an OpenBCI Ganglion via BrainFlow."""
        try:
            BFBoardShim.disable_board_logger()
            params = BrainFlowInputParams()
            params.timeout = int(self.ganglion_timeout)
            if self.ganglion_serial_port:
                # BLED112 dongle path
                board_id = BFBoardIds.GANGLION_BOARD
                params.serial_port = self.ganglion_serial_port
                params.mac_address = self.ganglion_mac
                how = f"dongle {self.ganglion_serial_port}"
            else:
                board_id = BFBoardIds.GANGLION_NATIVE_BOARD
                params.mac_address = self.ganglion_mac
                params.serial_number = self.ganglion_serial   # e.g. "Ganglion-1234"
                how = "native BLE"
            print(f"[EMGInput] Connecting to Ganglion ({how}) — "
                  f"up to {params.timeout}s. Make sure the OpenBCI GUI is disconnected.")
            self.board_id = board_id
            self.board = BFBoardShim(board_id, params)
            self.board.prepare_session()
            self.board.start_stream()

            self._channel_indices = list(BFBoardShim.get_exg_channels(board_id))[:MAX_CHANNELS]
            self.emg_channels = list(range(len(self._channel_indices)))
            self.channel_labels = [f"CH{i + 1}" for i in range(len(self._channel_indices))]
            self.sampling_rate = int(BFBoardShim.get_sampling_rate(board_id))
            self.synthetic = False
            self.backend = "ganglion"
            self.source_name = "OpenBCI Ganglion (BrainFlow)"
            self._enable_dc_highpass()

            self._init_buffers()
            self._running = True
            self._acq_thread = threading.Thread(target=self._sdk_loop, daemon=True)
            self._acq_thread.start()

            if not self._wait_for_data(4.0):
                print("[EMGInput] Ganglion connected but no samples arrived — skipping.")
                self._running = False
                self._acq_thread.join(timeout=0.5)
                raise RuntimeError("no data from Ganglion")
            time.sleep(0.5)
            self.pull()
            self._print_channel_report()
            return True
        except Exception as e:
            print(f"[EMGInput] Ganglion connection failed: {e}")
            if self.board is not None:
                try:
                    self.board.release_session()
                except Exception:
                    pass
            self.board = None
            return False

    def _start_udp(self) -> bool:
        """
        OpenBCI GUI → Networking widget → Protocol: UDP, Data Type: TimeSeries.
        Packets are JSON: {"type": "timeSeriesRaw", "data": [[ch0...], [ch1...], ...]}
        """
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.udp_host, self.udp_port))
            sock.settimeout(0.2)

            # Wait briefly for a first time-series packet.
            first = None
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                arr = self._parse_udp_packet(data)
                if arr is not None:
                    first = arr
                    break
            if first is None:
                print(f"[EMGInput] No OpenBCI UDP time-series packets on "
                      f"{self.udp_host}:{self.udp_port}.")
                sock.close()
                return False

            n_ch = min(first.shape[0], MAX_CHANNELS)
            self._channel_indices = list(range(n_ch))
            self.emg_channels = list(range(n_ch))
            self.channel_labels = [f"CH{i + 1}" for i in range(n_ch)]

            # Estimate sampling rate by counting samples for ~1 s, snap to a known rate.
            count = 0
            t0 = time.time()
            while time.time() - t0 < 1.0:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                arr = self._parse_udp_packet(data)
                if arr is not None:
                    count += arr.shape[1]
            est = count / max(1e-3, time.time() - t0)
            self.sampling_rate = int(min(KNOWN_RATES, key=lambda r: abs(r - est)))

            self._udp_sock = sock
            self.synthetic = False
            self.backend = "udp"
            self.source_name = f"OpenBCI GUI UDP :{self.udp_port}"
            self._enable_dc_highpass()

            self._init_buffers()
            self._running = True
            self._acq_thread = threading.Thread(target=self._udp_loop, daemon=True)
            self._acq_thread.start()

            time.sleep(0.5)
            self.pull()
            print(f"[EMGInput] UDP rate estimate {est:.0f} Hz → using {self.sampling_rate} Hz")
            self._print_channel_report()
            return True
        except Exception as e:
            print(f"[EMGInput] UDP backend failed: {e}")
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            self._udp_sock = None
            return False

    _udp_warned = False

    def _parse_udp_packet(self, data: bytes) -> np.ndarray | None:
        """Return (n_channels, n_samples) from an OpenBCI GUI JSON packet, else None."""
        try:
            msg = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception:
            return None
        if not isinstance(msg, dict):
            return None
        ptype = str(msg.get("type", "")).lower()
        if "timeseries" not in ptype:
            if not self._udp_warned:
                print(f"[EMGInput] UDP packet type '{msg.get('type')}' ignored — "
                      f"set the GUI Networking Data Type to TimeSeries.")
                self._udp_warned = True
            return None
        payload = msg.get("data")
        if payload is None:
            return None
        arr = np.asarray(payload, dtype=float)
        if arr.ndim == 1:                # one sample per channel
            arr = arr.reshape(-1, 1)
        elif arr.ndim != 2:
            return None
        return arr

    def _udp_loop(self):
        while self._running and self._udp_sock is not None:
            try:
                data, _ = self._udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            arr = self._parse_udp_packet(data)
            if arr is None:
                continue
            valid = [ch for ch in self._channel_indices if ch < arr.shape[0]]
            if valid:
                self._append_chunk(arr[valid, :])

    def _enable_dc_highpass(self):
        """20 Hz 2nd-order high-pass + 60 Hz notch, applied per channel in _append_chunk."""
        sr = max(1, self.sampling_rate)
        sections = []
        if EMG_HIGHPASS_HZ < sr / 2:
            b, a = butter(2, EMG_HIGHPASS_HZ / (sr / 2), btype="high")
            sections.append((b, a))
        if MAINS_HZ < sr / 2:
            b, a = iirnotch(MAINS_HZ, 30.0, sr)
            sections.append((b, a))
        self._hp_ba = sections or None
        self._hp_zi = None      # seeded from the first chunk

    def _print_channel_report(self):
        """Print per-channel RMS to help identify which channels have real signal."""
        disp = self.peek()
        print(f"[EMGInput] Connected via {self.backend} — "
              f"{self.n_channels} ch @ {self.sampling_rate} Hz  ({self.source_name})")
        print("[EMGInput] Channel signal levels (higher = more signal):")
        for i in range(self.n_channels):
            rms = float(np.sqrt(np.mean(disp[i] ** 2))) if disp.shape[1] > 0 else 0.0
            bar = "█" * min(20, int(rms / 5))
            flag = "  ← try this" if rms > 10 else ""
            print(f"           CH{i+1} (idx {self._channel_indices[i]:2d}): "
                  f"{rms:6.1f} µV  {bar}{flag}")
        print("[EMGInput] Use keys 1-8 or click CH buttons to select the active channel.")

    def _start_synthetic(self):
        self._channel_indices = list(range(4))
        self.emg_channels = list(range(4))
        self.channel_labels = [f"CH{i + 1}" for i in range(4)]
        self.sampling_rate = 500
        self.synthetic = True
        self.backend = "synthetic"
        self.source_name = "Synthetic"

        self._init_buffers()
        self._running = True
        self._acq_thread = threading.Thread(target=self._synth_loop, daemon=True)
        self._acq_thread.start()
        print(f"[EMGInput] Synthetic — {self.n_channels} ch @ {self.sampling_rate} Hz")

    def _init_buffers(self):
        n = DISPLAY_SECS * self.sampling_rate
        with self._disp_lock:
            self._disp_buf = np.zeros((self.n_channels, n))
            self._n_total = 0
            self._clock_offset = None
        with self._pending_lock:
            self._pending_buf = np.zeros((self.n_channels, 0))

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _append_chunk(self, new_data: np.ndarray):
        if new_data.size == 0:
            return

        if self._hp_ba is not None:
            if self._hp_zi is None or self._hp_zi[0].shape[0] != new_data.shape[0]:
                self._hp_zi = [None] * len(self._hp_ba)
            for k, (b, a) in enumerate(self._hp_ba):
                if self._hp_zi[k] is None:
                    # Seed each section at steady state for ITS OWN first input sample
                    # (after the preceding sections), so there is no settling transient.
                    self._hp_zi[k] = np.outer(new_data[:, 0], lfilter_zi(b, a))
                new_data, self._hp_zi[k] = lfilter(b, a, new_data, axis=1, zi=self._hp_zi[k])

        n = new_data.shape[1]
        with self._pending_lock:
            if self._pending_buf is not None and self._pending_buf.shape[0] == new_data.shape[0]:
                self._pending_buf = np.hstack([self._pending_buf, new_data])

        with self._disp_lock:
            if self._disp_buf is None or self._disp_buf.shape[0] != new_data.shape[0]:
                return
            if n >= self._disp_buf.shape[1]:
                self._disp_buf[:, :] = new_data[:, -self._disp_buf.shape[1]:]
            else:
                self._disp_buf[:, :-n] = self._disp_buf[:, n:]
                self._disp_buf[:, -n:] = new_data

            # Stream clock: wall time at which sample index 0 "happened".
            # Chunks arrive in bursts, so track the offset with a slow filter and
            # never let it drift later than any observed arrival (that would place
            # samples in the future and stall the display).
            self._n_total += n
            now = time.monotonic()
            self.last_sample_time = now
            est = now - self._n_total / max(1, self.sampling_rate)
            if self._clock_offset is None:
                self._clock_offset = est
            else:
                self._clock_offset += 0.02 * (est - self._clock_offset)
                if est < self._clock_offset:
                    self._clock_offset = est

    def _resolve_lsl_stream(self):
        preferred_type = os.getenv("MINDROVE_LSL_TYPE", "EMG")
        preferred_name = os.getenv("MINDROVE_LSL_NAME", "").strip().lower()
        preferred_source = os.getenv("MINDROVE_LSL_SOURCE_ID", "").strip().lower()

        candidates = []
        try:
            candidates.extend(resolve_byprop("type", preferred_type, timeout=1.5))
        except Exception:
            pass

        if not candidates:
            try:
                candidates.extend(resolve_streams(wait_time=1.5))
            except Exception:
                return None

        if not candidates:
            return None

        def score(stream):
            name = stream.name().lower()
            stype = stream.type().lower()
            source = stream.source_id().lower()
            score_val = 0
            if "mindrove" in name or "mindrove" in source:
                score_val += 8
            if any(k in name or k in source for k in ("obci", "openbci", "ganglion")):
                score_val += 8
            if stype == preferred_type.lower():
                score_val += 4
            if stream.channel_count() >= 4:
                score_val += 2
            if stream.nominal_srate() > 0:
                score_val += 1
            if preferred_name and preferred_name in name:
                score_val += 20
            if preferred_source and preferred_source in source:
                score_val += 20
            return score_val

        return max(candidates, key=score)

    def _lsl_loop(self):
        idle_sleep = 0.002
        while self._running and self._lsl_inlet is not None:
            try:
                chunk, _timestamps = self._lsl_inlet.pull_chunk(timeout=0.05, max_samples=64)
            except Exception:
                time.sleep(0.05)
                continue

            if not chunk:
                time.sleep(idle_sleep)
                continue

            arr = np.asarray(chunk, dtype=float)
            if arr.ndim == 1:
                arr = arr.reshape(1, -1)
            else:
                arr = arr.T

            if self._channel_indices and arr.shape[0] > max(self._channel_indices):
                self._append_chunk(arr[self._channel_indices, :])

    def _sdk_loop(self):
        idle_sleep = 0.005
        while self._running and self.board is not None:
            try:
                raw = self.board.get_board_data()
            except Exception:
                time.sleep(0.05)
                continue

            if raw.shape[1] == 0:
                time.sleep(idle_sleep)
                continue

            valid = [ch for ch in self._channel_indices if ch < raw.shape[0]]
            if valid:
                self._append_chunk(raw[valid, :])

    def _synth_loop(self):
        chunk = 10
        dt = chunk / self.sampling_rate
        while self._running:
            t = self._synth_t
            self._synth_t += dt

            # Baseline noise ~8µV; burst every 5s to simulate flex.
            burst = 0.0
            phase = t % 6.0
            if 2.5 < phase < 3.0 and not self.quiet:
                burst = 200.0

            data = np.random.randn(4, chunk) * 8.0
            data[0] += burst * np.clip(np.random.randn(chunk), 0, None)
            data[1] += burst * 0.3 * np.clip(np.random.randn(chunk), 0, None)

            idx = [i for i in self._channel_indices if i < 4]
            self._append_chunk(data[idx, :])
            time.sleep(dt)
