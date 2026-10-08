# Triton Neurotech — EMG BCI Workshop

Control a game with your muscles using a MindRove armband **or an OpenBCI Ganglion** and Python.

---

## What You'll Build

A real-time EMG (electromyography) pipeline that:
1. Reads muscle signals from a MindRove 4-channel WiFi armband
2. Processes the signal to detect muscle contractions
3. Uses contractions as input to control a jump game

---

## Hardware Setup

### Wearing the Armband

1. Put the MindRove armband on your **forearm**, about 2–3 finger-widths below the elbow
2. The electrodes should sit snugly against the skin — not too tight, not loose
3. Make a fist and feel where the muscles bulge — that's where you want the sensors
4. If signal is weak, try rotating the band slightly or moistening the skin

### Connecting to Wi-Fi

The MindRove armband creates its own Wi-Fi hotspot.

1. Power on the armband (hold the button until the LED blinks)
2. On your laptop, connect to the Wi-Fi network: **`MindRove_XXXX`** (the ID is on the band)
3. Password: `mindrove` (default)
4. Your laptop will now be connected to the armband's network — internet won't work while connected

---

## Software Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs: `mindrove`, `numpy`, `pygame`, `pylsl`, `scipy`

### 2. Install MindRove Connect (recommended — LSL stream)

MindRove Connect is a small app that connects to the armband and publishes an LSL stream your Python code can read.

**Download:** https://github.com/MindRove/MindRoveSDK/releases  
(look for `MindRoveConnect` under the latest release for your OS)

**Steps:**
1. Open MindRove Connect
2. Make sure you're connected to the armband's Wi-Fi
3. Click **"Start LSL Stream"**
4. You should see a green status indicator — the stream is now live
5. Leave MindRove Connect running in the background

> **Why LSL?** LSL (Lab Streaming Layer) is a standard protocol for real-time biosignal streaming. Using it means your Python code doesn't need to manage the hardware connection directly — MindRove Connect handles that.

### 3. Run the workshop

```bash
python main.py
```

With no flags the app auto-detects a source in this order: LSL stream → OpenBCI GUI UDP → MindRove SDK → synthetic.

The live view shows one trace per channel the source provides. Channels that carry nothing
(switched off in the OpenBCI GUI, or padding in an LSL stream) are hidden automatically.
To show only the electrodes you actually wired up, pass them explicitly:

```bash
python main.py --channels 1        # single Ganglion channel
python main.py --channels 1,3      # two channels
```

Or, if you don't have the hardware yet, run in synthetic mode (fake flexes every few seconds):

```bash
python main.py --synthetic
```

To work on the UI or game mechanics without any signal, use keyboard test mode. Holding
**Space** acts as a clench and goes through the exact same trigger and jump logic:

```bash
python main.py --keyboard
```

---

## Using an OpenBCI Ganglion instead

The same game works with a Ganglion (4 channels, 200 Hz) and plain gel/snap electrodes.
Put the two electrodes of one channel over the forearm flexor bulk a few cm apart, and the
reference (REF / bottom pin) on a bony spot like the elbow or wrist. There are two ways to
feed the Ganglion into the game:

**Option A — through the OpenBCI GUI (keep the GUI open)**

1. In the OpenBCI GUI connect to the Ganglion and press **Start Data Stream**
2. Open the **Networking** widget (any widget's dropdown → Networking)
3. Pick **Protocol: LSL** (name `obci_eeg1`, type `EEG`, Data Type **TimeSeries**) **or**
   **Protocol: UDP** (IP `127.0.0.1`, port `12345`, Data Type **TimeSeries**)
4. Press **Start** in the Networking widget
5. Run `python main.py` — it finds the LSL stream or the UDP packets automatically.
   To force one: `python main.py --backend udp` or `--backend lsl`

**Option B — direct Bluetooth with BrainFlow (no GUI)**

1. `pip install brainflow` (already in `requirements.txt`)
2. **Close or disconnect the OpenBCI GUI** — Bluetooth allows only one host connection
3. Run `python main.py --ganglion`. The first time macOS will ask to allow your terminal
   to use Bluetooth; accept it and run again. Connecting takes a few seconds.
   - Several Ganglions nearby? `python main.py --ganglion --serial Ganglion-1234`
   - Using the BLED112 dongle? `python main.py --ganglion --serial-port /dev/cu.usbmodemXXXX`

Ganglion raw samples carry a large DC offset. The input layer applies a 5 Hz high-pass for
OpenBCI sources so the plots, calibration and threshold all work in the same µV range as the
MindRove.

---

## Running the App

### Controls

Everything is driven by clicks: the two cards on the main menu open calibration and the
game, the channel chips pick the active electrode, and the game screens have Start,
Play again and Main menu buttons. Keyboard shortcuts still work if you want them:

| Key | Action |
|-----|--------|
| `C` / `G` / `M` | Calibrate / jump game / main menu |
| `1`–`8` | Switch active EMG channel |
| `SPACE` | Manual trigger (test without hardware) |
| `F11` / `ESC` | Fullscreen / quit |

### Jump game controls

The jump works like the Chrome dinosaur game:

- a **quick clench** gives a short hop (clears a single small cactus)
- a **harder clench** launches higher
- **holding the clench** keeps the jump going longer, up to a cap
- **releasing early** cuts the jump short
- **low birds** must be jumped; **high birds** fly over your head, so stay down

After a game over, flex to play again or click **Main menu**.

The background advances through eight eras of human history, one every 400 points:
stone age, first villages, ancient Egypt, classical Rome, middle ages, industrial age,
modern city, space age.

The live view shows the smoothed activation envelope (the value the trigger uses) over a
faint raw trace. The trigger itself uses a fast-attack, slow-release envelope so brief dips
during a long clench cannot re-arm it and fire a second jump.

Speed ramps up with distance, and taller obstacles only appear once you are going fast.

### Calibration (important!)

Calibration teaches the app the difference between your resting muscle signal and a flex. Always calibrate before playing.

1. Click **Calibrate** on the main menu
2. **Rest phase (5 s):** relax your arm completely — don't move
3. **Flex phase (5 s):** repeatedly clench your fist firmly
4. The app computes a threshold and shows you a summary
5. If the threshold looks too high or too low, calibrate again

### Selecting the Right Channel

After connecting, the terminal prints a channel signal report like:

```
[EMGInput] Channel signal levels (higher = more signal):
           CH1 (idx  1):   12.3 µV  ██  ← try this
           CH2 (idx  2):    3.1 µV
           CH3 (idx  3):   45.6 µV  █████████  ← try this
           CH4 (idx  4):    8.2 µV  █
```

Press `1`–`4` to switch to the channel with the highest signal. Channels marked `← try this` are good candidates.

---

## Architecture Overview

```
MindRove armband
      │
      ▼  (Wi-Fi)
MindRove Connect app  ──►  LSL stream  ──►  emg_input.py
                                                  │
                                            signal_processing.py
                                            (rectify + smooth → µV scalar)
                                                  │
                                            controller.py
                                            (threshold + debounce → trigger)
                                                  │
                                    ┌─────────────┴──────────────┐
```

| File | What it does |
|------|-------------|
| `emg_input.py` | Connects to LSL or SDK, buffers raw EMG samples |
| `signal_processing.py` | Rectifies + smooths signal into a single activation value (µV) |
| `calibration.py` | Records rest/flex windows, computes detection threshold |
| `controller.py` | Converts activation scalar → clean trigger with debounce & hysteresis |
| `game.py` | Endless runner jump game |
| `main.py` | Main menu, calibration and game UI, wires everything together |
| `fonts/` | Bundled Manrope and DM Mono (SIL Open Font License) |

---

## Workshop Notebook

Open `workshop.ipynb` for a guided, hands-on walkthrough where you'll:
- Read and plot live EMG data
- Implement signal processing from scratch
- Build your own threshold detector
- Connect it to the full pipeline

```bash
jupyter notebook workshop.ipynb
```

---

## Troubleshooting

**No LSL stream found**
- Make sure MindRove Connect is running and shows a green status
- Check you're connected to the armband's Wi-Fi, not your normal network
- Try restarting MindRove Connect and clicking "Start LSL Stream" again

**Ganglion: `--ganglion` can't find the board / times out**
- Quit the OpenBCI GUI (or press Stop + Disconnect) — only one app can hold the Bluetooth link
- Check System Settings → Privacy & Security → Bluetooth allows your terminal / VS Code
- Power-cycle the Ganglion (blue LED should blink slowly when it's advertising)

**Ganglion via GUI: app says "No LSL stream found" and "No OpenBCI UDP packets"**
- In the Networking widget make sure Data Type is **TimeSeries** and you pressed **Start**
- For UDP the IP must be `127.0.0.1` and the port `12345` (or pass `--udp-port`)

**Signal is flat / very noisy**
- The armband may be loose — tighten it slightly
- Try moistening the skin under the electrodes
- Switch to a different channel (keys `1`–`4`)

**Triggers firing on their own (too sensitive)**
- Re-calibrate and relax more during the rest phase
- Or drag the threshold slider on the last calibration step

**App crashes on import**
- Run `pip install -r requirements.txt` again
- Make sure you're using Python 3.10+: `python --version`
