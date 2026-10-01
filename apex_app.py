"""The installed app's front door: Apex.exe (product, 30 Sep 2026).

Double-click Apex: the first time, the setup wizard; after that, race night straight away.
    Apex.exe            (setup if there's no profile.json yet, then race night)
    Apex.exe --setup    (the wizard again, to change the name, button or mode)
Every other flag goes to apex.py (--replay, --clips, ...).

Apex writes its files next to itself (apex.db, profile.json, ptt_button.json, Videos stay in
Videos/Apex), so the installer puts it in the user's own folder (%LOCALAPPDATA%\\Apex), where
it may write without asking for admin. The ffmpeg it ships (LGPL build) sits in ffmpeg/.
"""

import os
import sys


def app_folder():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main():
    folder = app_folder()
    os.chdir(folder)
    shipped_ffmpeg = os.path.join(folder, "ffmpeg")
    if os.path.isdir(shipped_ffmpeg):
        os.environ["PATH"] = shipped_ffmpeg + os.pathsep + os.environ.get("PATH", "")
    args = sys.argv[1:]
    if "--setup" in args or not os.path.exists("profile.json"):
        args = [a for a in args if a != "--setup"]
        import setup_wizard

        setup_wizard.main()
        if not os.path.exists("profile.json"):
            print("Setup was closed before Finish - start Apex again to set it up.")
            return
    import apex
    import beta

    beta.ensure_registered()  # a beta install gets its own AI token on the first start
    print(
        "Apex is listening. Start a session in Le Mans Ultimate. Ctrl+C here to stop."
    )
    apex.main(args)


if __name__ == "__main__":
    main()
