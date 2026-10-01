"""Builds dist/Apex: Apex.exe and everything it needs, ready for the installer (product, 30 Sep).

    python packaging/build.py [--out C:/path/to/build]

1. PyInstaller packs the code into Apex.exe (one folder, console window for the radio log).
2. Next to Apex.exe (Apex finds these relative to where it runs):
     voice_bank/   the standard engineer and spotter voices. NEVER voice_bank/clone or
                   voice_bank/phrases/clone: the cloned voice of a real person stays on
                   Gourav's laptop (his rule, 24 Sep).
     track_maps/   corner names
     ffmpeg/       the LGPL build (a paid app can't carry the GPL one)
     licenses/     notices for the code Apex ships that others wrote
3. Nothing personal: no .env, apex.db, profile.json, ptt_button.json, server.json, tapes.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the SHARED LGPL build: ffmpeg and ffprobe share one set of DLLs (256 MB -> about 90 MB),
# and the LGPL libraries stay separate files a user could replace
LGPL_FFMPEG = r"C:\Users\gourav\Downloads\apex-launch\vendor\ffmpeg-n8.1-latest-win64-lgpl-shared-8.1"
DEFAULT_OUT = r"C:\Users\gourav\Downloads\apex-launch\build"
NEVER_SHIP = [
    "clone",
    ".env",
    "apex.db",
    "profile.json",
    "ptt_button.json",
    "testers.json",
]
# installed in his Python for other projects; Apex imports none of them
EXCLUDE = [
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "pandas",
    "matplotlib",
    "scipy",
    "sympy",
    "networkx",
    "IPython",
    "notebook",
    "jupyter",
    "cv2",
    "sklearn",
    "PIL",
    "transformers",
    "datasets",
    "numba",
    "llvmlite",
    # dev/lmu_import.py only; pulled in through optional imports (117 MB, 30 Sep)
    "duckdb",
    "pyarrow",
]
COLLECT = [
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "tokenizers",
    "pyaudiowpatch",
    "sounddevice",
    "_sounddevice_data",
    "pygame",
    "edge_tts",
    "pyttsx3",
    "comtypes",
]
HIDDEN = [
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
    "win32com",
    "pythoncom",
    "tkinter",
]


def pyinstaller(out):
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "Apex",
        "--console",
        "--distpath",
        os.path.join(out, "dist"),
        "--workpath",
        os.path.join(out, "work"),
        "--specpath",
        os.path.join(out, "spec"),
        "--add-data",
        f"{os.path.join(REPO, 'knowledge.md')}{os.pathsep}.",
    ]
    for name in COLLECT:
        args += ["--collect-all", name]
    for name in HIDDEN:
        args += ["--hidden-import", name]
    for name in EXCLUDE:
        args += ["--exclude-module", name]
    args.append(os.path.join(REPO, "apex_app.py"))
    subprocess.run(args, check=True, cwd=REPO)
    return os.path.join(out, "dist", "Apex")


def copy_voices(app):
    source = os.path.join(REPO, "voice_bank")
    target = os.path.join(app, "voice_bank")

    def skip(folder, names):
        return [n for n in names if n == "clone"]

    shutil.copytree(source, target, ignore=skip, dirs_exist_ok=True)


def copy_rest(app):
    shutil.copytree(
        os.path.join(REPO, "track_maps"),
        os.path.join(app, "track_maps"),
        dirs_exist_ok=True,
    )
    ffmpeg = os.path.join(app, "ffmpeg")
    os.makedirs(ffmpeg, exist_ok=True)
    for name in os.listdir(os.path.join(LGPL_FFMPEG, "bin")):
        if name in ("ffmpeg.exe", "ffprobe.exe") or name.endswith(".dll"):
            shutil.copy2(os.path.join(LGPL_FFMPEG, "bin", name), ffmpeg)
    licenses = os.path.join(app, "licenses")
    os.makedirs(licenses, exist_ok=True)
    shutil.copy2(
        os.path.join(LGPL_FFMPEG, "LICENSE.txt"),
        os.path.join(licenses, "ffmpeg-LGPL.txt"),
    )
    shutil.copy2(os.path.join(REPO, "packaging", "THIRD_PARTY_NOTICES.txt"), licenses)


def nothing_personal(app):
    """Fails the build if anything private or the cloned voice got in."""
    leaks = []
    for folder, dirs, files in os.walk(app):
        for name in dirs + files:
            if name in NEVER_SHIP:
                leaks.append(os.path.join(folder, name))
            # a beta build carries the door's address, never a token: each install registers its own
            if name == "server.json":
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    if folder != app or "token" in json.load(f):
                        leaks.append(os.path.join(folder, name))
    if leaks:
        raise SystemExit("REFUSING TO SHIP: " + ", ".join(leaks))


def size_mb(folder):
    total = 0
    for root, _, files in os.walk(folder):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total / 1e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument(
        "--door", help="the Apex door's address: makes this an open-beta build"
    )
    args = ap.parse_args()
    app = pyinstaller(args.out)
    copy_voices(app)
    copy_rest(app)
    if args.door:
        with open(os.path.join(app, "server.json"), "w", encoding="utf-8") as f:
            json.dump({"url": args.door}, f)
    nothing_personal(app)
    print(f"built {app}: {size_mb(app):.0f} MB")


if __name__ == "__main__":
    main()
