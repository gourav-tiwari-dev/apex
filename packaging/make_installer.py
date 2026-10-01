"""dist/Apex -> ApexSetup-<version>.exe with Inno Setup (product, 30 Sep 2026).

python packaging/build.py --out BUILD      (first: the app folder)
python packaging/make_installer.py BUILD [--version 0.1.0]
"""

import argparse
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ISCC = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Inno Setup 6", "ISCC.exe")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("build")
    ap.add_argument("--version", default="0.1.0")
    args = ap.parse_args()
    source = os.path.join(os.path.abspath(args.build), "dist", "Apex")
    out = os.path.join(os.path.abspath(args.build), "installer")
    subprocess.run(
        [
            ISCC,
            f"/DSourceDir={source}",
            f"/DAppVersion={args.version}",
            f"/O{out}",
            os.path.join(HERE, "apex.iss"),
        ],
        check=True,
    )
    for name in os.listdir(out):
        size = os.path.getsize(os.path.join(out, name)) / 1e6
        print(f"{os.path.join(out, name)}: {size:.0f} MB")


if __name__ == "__main__":
    main()
