# Triton Neurotech — EMG BCI Workshop

Control a game with your muscles. A MindRove armband or an OpenBCI Ganglion reads the
electrical activity of your forearm, Python turns it into a trigger, and you play a
dinosaur-style jump game with your fist.

---

## Quick start (5 minutes)

You need **Python 3.10 or newer**. Check with `python3 --version` (macOS/Linux) or
`py --version` (Windows). If you don't have it, install it from
<https://www.python.org/downloads/> — on Windows tick **"Add python.exe to PATH"**.

**1. Get the code**

```bash
git clone https://github.com/uddhavp22/Triton-Neurotech-EMG-Workshop.git
cd Triton-Neurotech-EMG-Workshop
```

(or download the ZIP from GitHub and unzip it)

**2. Run the setup script once**

| macOS / Linux | Windows |
|---|---|
| `./setup.sh` | double-click `setup.bat` |

It creates a private Python environment in `.venv`, installs the packages, and prints a
checklist. Everything should say `ok`.

**3. Try it with no hardware**

| macOS / Linux | Windows |
|---|---|
| `./run.sh --keyboard` | `run.bat --keyboard` |

The app opens. Click **Jump game**, then **Start**, and hold **Space** to "flex". If this
works, your machine is ready — the only thing left is the signal source below.

**4. Connect a device and play**

| macOS / Linux | Windows |
|---|---|
| `./run.sh` | `run.bat` |

With no flags the app looks for a signal in this order: an LSL stream (MindRove Connect or
the OpenBCI GUI) → OpenBCI GUI UDP → MindRove armband direct → synthetic. The status in the
top-right corner of the menu tells you which one it found.

<details>
<summary>Prefer to do it by hand instead of the scripts?</summary>

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python check_setup.py
python main.py --keyboard
```
</details>

---

## Command-line options

| Flag | What it does |
|---|---|
| *(none)* | auto-detect: LSL → OpenBCI UDP → MindRove SDK → synthetic |
| `--keyboard` | test mode, no device: hold **Space** to flex |
| `--synthetic` | fake signal with a flex every few seconds |
| `--channels 1` | show only these channels (1-based, comma-separated) |
| `--backend lsl` / `udp` / `sdk` / `ganglion` | force one source instead of auto-detecting |
| `--ganglion` | OpenBCI Ganglion over Bluetooth, no GUI needed |
| `--help` | everything else |

Pass flags through the scripts the same way: `./run.sh --channels 1`, `run.bat --keyboard`.

---

## Using a MindRove armband

### Wearing it

1. Put the armband on your **forearm**, about 2–3 finger-widths below the elbow
2. The electrodes should sit snugly against the skin — not too tight, not loose
3. Make a fist and feel where the muscles bulge — that's where you want the sensors
4. If signal is weak, try rotating the band slightly or moistening the skin

### Connecting

The armband creates its own Wi-Fi hotspot.

1. Power on the armband (hold the button until the LED blinks)
2. On your laptop connect to the Wi-Fi network **`MindRove_XXXX`** (the ID is on the band), password `mindrove`
3. Internet won't work while you're on the armband's network — that's normal

### Streaming (recommended: MindRove Connect)

MindRove Connect is a small app that talks to the armband and publishes an LSL stream.

1. Download it from <https://github.com/MindRove/MindRoveSDK/releases> (look for `MindRoveConnect` for your OS)
2. Open it while connected to the armband's Wi-Fi and click **Start LSL Stream**
3. Leave it running and start the workshop app — it finds the stream on its own

No MindRove Connect? The app can also talk to the armband directly through the `mindrove`
package (`--backend sdk`).

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

Raw Ganglion samples carry a large DC offset and, on gel electrodes, a lot of motion
artefact. The input layer applies a 20 Hz high-pass and a 60 Hz notch to OpenBCI sources so
only muscle activity reaches the trigger. Leave the GUI's own filters off (Data Type
**TimeSeriesRaw**) — the app does the filtering.

**Getting a strong signal with plain electrodes**

- Both channel electrodes go over the fat part of the forearm flexors (palm-side, a hand's
  width below the elbow), 3–4 cm apart along the muscle. Reference on the bony elbow or wrist.
- Press them down and re-wet or re-gel if the rest trace looks fuzzy. A hard fist should push
  the activation meter well past 100 µV; if it only reaches 30, move the electrodes.
- Clench without moving your wrist or forearm — movement, not muscle, is what makes the
  trace jump around.

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
2. **Rest phase (10 s):** relax your arm completely — don't move
3. **Flex phase (10 s):** clench your fist firmly and hold it
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

Click the channel chip with the strongest signal (or press `1`–`8`). Channels marked `← try this` are good candidates.

---

## How it works

```
MindRove armband / OpenBCI Ganglion
            │  Wi-Fi / Bluetooth / LSL / UDP
            ▼
      emg_input.py          raw samples, display buffer, stream clock
            │
   signal_processing.py     rectify + 90 ms smoothing → activation (µV)
            │
      calibration.py        rest/flex recordings → threshold
            │
      controller.py         threshold + hysteresis + release envelope → one trigger per clench
            │
         game.py            jump physics, obstacles, eras
            │
         main.py            PyQt6 UI: menu, calibration, game
```

| File | What it does |
|------|-------------|
| `main.py` | Main menu, calibration and game UI, wires everything together |
| `emg_input.py` | Connects to LSL / UDP / MindRove / BrainFlow, buffers raw EMG samples |
| `signal_processing.py` | Rectifies + smooths signal into a single activation value (µV) |
| `calibration.py` | Records rest/flex windows, computes detection threshold |
| `controller.py` | Converts activation into clean triggers with debounce & hysteresis |
| `game.py` | Dinosaur-style jump game logic |
| `check_setup.py` | Verifies the installation |
| `setup.sh` / `setup.bat`, `run.sh` / `run.bat` | One-click install and launch |
| `fonts/` | Bundled Manrope and DM Mono (SIL Open Font License) |
| `workshop.ipynb` | Optional guided notebook |

---

## Workshop Notebook

Open `workshop.ipynb` for a guided, hands-on walkthrough where you'll:
- Read and plot live EMG data
- Implement signal processing from scratch
- Build your own threshold detector
- Connect it to the full pipeline

```bash
pip install jupyter
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

**`python` is not recognised / wrong version**
- Windows: use `py -3` instead of `python`, or re-run the Python installer and tick "Add python.exe to PATH"
- macOS: `python3` is the right name; the scripts handle this for you
- The app needs Python 3.10+: `python3 --version`

**Setup script fails installing a package**
- Make sure you're online; the first install downloads ~200 MB (Qt is most of it)
- Run `python check_setup.py` to see exactly what's missing, then `pip install <name>`
- Only `numpy`, `scipy`, `PyQt6` and `pyqtgraph` are essential — the app runs in `--keyboard` and `--synthetic` mode without the device packages

**Linux: window doesn't open / "could not load the Qt platform plugin xcb"**
- `sudo apt install libxcb-cursor0 libxcb-xinerama0 libegl1` (Debian/Ubuntu), then try again

**Windows: `--ganglion` can't find the board**
- BrainFlow needs the Ganglion to be unpaired in Windows Bluetooth settings (it connects on its own)
- Or use the OpenBCI GUI route (Networking → LSL) instead

**Fonts look different / plain**
- The app bundles its fonts in `fonts/`; make sure that folder came along if you copied the project

**App crashes on import**
- Run the setup script again (or `pip install -r requirements.txt`)
- Make sure the terminal is using the `.venv` Python: `./run.sh` / `run.bat` do this for you
