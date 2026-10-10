"""
check_setup.py — run this once after `pip install -r requirements.txt`.

Verifies the Python version, every package the app needs, the bundled fonts,
and reports which signal sources are available on this machine.
"""
import importlib
import os
import sys

OK, MISSING = "  ok   ", "  MISSING"


def check(label, fn):
    try:
        info = fn()
        print(f"{OK} {label}{('  ' + info) if info else ''}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"{MISSING} {label}  ({type(e).__name__}: {e})")
        return False


def version(mod):
    m = importlib.import_module(mod)
    return getattr(m, "__version__", "")


def main():
    print(f"Python {sys.version.split()[0]} at {sys.executable}")
    if sys.version_info < (3, 10):
        print("  !! Python 3.10 or newer is required.")
    print()
    print("Required packages")
    core = all([
        check("numpy",      lambda: version("numpy")),
        check("scipy",      lambda: version("scipy")),
        check("PyQt6",      lambda: importlib.import_module("PyQt6.QtCore").QT_VERSION_STR),
        check("pyqtgraph",  lambda: version("pyqtgraph")),
    ])
    print()
    print("Signal sources (optional — the app runs in synthetic/keyboard mode without them)")
    check("pylsl (LSL from MindRove Connect / OpenBCI GUI)",
          lambda: f"{version('pylsl')}, liblsl {importlib.import_module('pylsl').library_version()}")
    check("mindrove (armband direct)", lambda: "")
    check("brainflow (Ganglion direct)", lambda: "")
    print()
    print("Bundled fonts")
    here = os.path.dirname(os.path.abspath(__file__))
    for f in ("Manrope[wght].ttf", "DMMono-Regular.ttf", "DMMono-Medium.ttf"):
        path = os.path.join(here, "fonts", f)
        print(f"{OK if os.path.exists(path) else MISSING} fonts/{f}")
    print()
    if core:
        print("Ready. Try it without hardware:   python main.py --keyboard")
        print("With a device streaming:          python main.py")
    else:
        print("Install what's missing:  pip install -r requirements.txt")
        sys.exit(1)


if __name__ == "__main__":
    main()
