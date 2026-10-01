"""First run: two minutes from install to a working engineer (product, Tier 0, 30 Sep 2026).

    python setup_wizard.py

One window, five steps, then profile.json (driver_profile.py) and ptt_button.json (talk/ptt.py):
  1. You          name, what you drive, wheel or controller
  2. Radio button press the button you'll hold to talk (read through SDL, like the race loop)
  3. Engineer     clean or spicy, with a voice test
  4. Auto-clips   record the game window + a short of every race (and the mic: said plainly)
  5. The game     Le Mans Ultimate found (Steam), and what to do next
The window is thin: what it decides lives in plain functions below, which the tests call.
"""

import os
import re
import threading
import tkinter as tk
from tkinter import ttk

from driver_profile import Profile, load_profile, save_profile

CARS = ["a Hypercar", "an LMP2", "an LMP3", "a GT3", "a GTE"]
INPUTS = ["a wheel", "a controller"]
STEAM_LIBRARIES = r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"
LMU_FOLDER = os.path.join("steamapps", "common", "Le Mans Ultimate")


# ---------- what the wizard decides ----------
def steam_libraries(vdf_path=STEAM_LIBRARIES):
    """Every Steam library folder listed in libraryfolders.vdf."""
    if not os.path.exists(vdf_path):
        return []
    with open(vdf_path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    return [
        path.replace("\\\\", "\\") for path in re.findall(r'"path"\s+"([^"]+)"', text)
    ]


def find_lmu(vdf_path=STEAM_LIBRARIES):
    """The Le Mans Ultimate install folder, or None."""
    for library in steam_libraries(vdf_path):
        folder = os.path.join(library, LMU_FOLDER)
        if os.path.exists(os.path.join(folder, "Le Mans Ultimate.exe")):
            return folder
    return None


def profile_from_answers(name, car, input_device, spicy, clips, old=None):
    """The profile the answers describe. A habit note from an older profile is kept."""
    old = old or Profile()
    name = name.strip() or "the driver"
    if car not in CARS:
        car = "a GT3"
    if input_device not in INPUTS:
        input_device = "a wheel"
    return Profile(
        name=name,
        car=car,
        input=input_device,
        habit=old.habit,
        spicy=bool(spicy),
        voice=old.voice,
        clips=bool(clips),
    )


# ---------- the window ----------
BG, CARD, INK, DIM, ORANGE = "#0b0e12", "#141920", "#eef1f4", "#8a94a0", "#ff5b1f"


class Wizard:
    def __init__(self, root):
        self.root = root
        self.old = load_profile()
        self.button = None  # (controller name, button number) once pressed
        self.pygame = None
        root.title("Apex setup")
        root.configure(bg=BG)
        # sized in inches, not pixels: at 200% scaling a 560 px window cut the steps off (30 Sep)
        scale = root.winfo_fpixels("1i") / 96
        self.scale = scale
        root.geometry(f"{round(600 * scale)}x{round(640 * scale)}")
        root.minsize(round(560 * scale), round(600 * scale))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure(
            "TLabel", background=BG, foreground=INK, font=("Bahnschrift", 12)
        )
        style.configure("Head.TLabel", font=("Bahnschrift", 22, "bold"))
        style.configure("Dim.TLabel", foreground=DIM, font=("Bahnschrift", 11))
        style.configure(
            "Step.TLabel", foreground=ORANGE, font=("Bahnschrift", 11, "bold")
        )
        style.configure(
            "TRadiobutton", background=BG, foreground=INK, font=("Bahnschrift", 12)
        )
        style.configure(
            "TCheckbutton", background=BG, foreground=INK, font=("Bahnschrift", 12)
        )
        style.configure("Go.TButton", font=("Bahnschrift", 12, "bold"), padding=8)
        # the chosen option is orange, so which one is picked can't be misread
        for kind in ("TRadiobutton", "TCheckbutton"):
            style.map(
                kind,
                indicatorcolor=[("selected", ORANGE), ("!selected", CARD)],
                background=[("active", BG)],
                foreground=[("active", INK)],
            )
        self.name = tk.StringVar(
            value="" if self.old.name == "the driver" else self.old.name
        )
        self.car = tk.StringVar(value=self.old.car)
        self.input = tk.StringVar(value=self.old.input)
        # text, not a BooleanVar: a ttk radio button compares its value as text, and True never matched
        self.talk = tk.StringVar(value="spicy" if self.old.spicy else "clean")
        self.clips = tk.BooleanVar(value=self.old.clips)
        self.button_text = tk.StringVar(value="Waiting for a button...")
        self.steps = [
            self.step_you,
            self.step_button,
            self.step_engineer,
            self.step_clips,
            self.step_game,
        ]
        self.at = 0
        self.page = None
        self.show()

    def show(self):
        if self.page is not None:
            self.page.destroy()
        self.page = ttk.Frame(self.root, padding=32)
        self.page.pack(fill="both", expand=True)
        ttk.Label(
            self.page,
            text=f"STEP {self.at + 1} OF {len(self.steps)}",
            style="Step.TLabel",
        ).pack(anchor="w")
        self.steps[self.at]()
        buttons = ttk.Frame(self.page)
        buttons.pack(side="bottom", fill="x")
        if self.at > 0:
            ttk.Button(buttons, text="Back", command=self.back).pack(side="left")
        last = self.at == len(self.steps) - 1
        ttk.Button(
            buttons,
            text="Finish" if last else "Next",
            style="Go.TButton",
            command=self.finish if last else self.next,
        ).pack(side="right")

    def next(self):
        self.at += 1
        self.show()

    def back(self):
        self.at -= 1
        self.show()

    def heading(self, text, under):
        ttk.Label(self.page, text=text, style="Head.TLabel").pack(
            anchor="w", pady=(8, 4)
        )
        ttk.Label(
            self.page,
            text=under,
            style="Dim.TLabel",
            wraplength=round(520 * self.scale),
            justify="left",
        ).pack(anchor="w", pady=(0, 18))

    def choice(self, text, value, variable):
        """A big toggle that turns orange when picked: the theme's radio dots were too small to
        read at 200% scaling (30 Sep, his profile's choice looked unpicked)."""
        tk.Radiobutton(
            self.page,
            text=text,
            value=value,
            variable=variable,
            indicatoron=0,
            font=("Bahnschrift", 12, "bold"),
            width=14,
            pady=6,
            bd=0,
            relief="flat",
            bg=CARD,
            fg=INK,
            activebackground=CARD,
            activeforeground=INK,
            selectcolor=ORANGE,
            cursor="hand2",
        ).pack(anchor="w", pady=3)

    def step_you(self):
        self.heading(
            "Your engineer", "Apex talks to you by name and knows what you drive."
        )
        ttk.Label(self.page, text="Your name").pack(anchor="w")
        ttk.Entry(self.page, textvariable=self.name, font=("Bahnschrift", 13)).pack(
            fill="x", pady=(2, 14)
        )
        ttk.Label(self.page, text="What you race most").pack(anchor="w")
        ttk.Combobox(
            self.page,
            textvariable=self.car,
            values=CARS,
            state="readonly",
            font=("Bahnschrift", 12),
        ).pack(fill="x", pady=(2, 14))
        ttk.Label(self.page, text="You drive with").pack(anchor="w")
        for choice in INPUTS:
            self.choice(choice.split(" ", 1)[1].capitalize(), choice, self.input)

    def step_button(self):
        self.heading(
            "Your radio button",
            "Press the button on your wheel or controller that you'll HOLD to talk to Apex. "
            "Apex keeps hearing it while the game has focus.",
        )
        ttk.Label(
            self.page, textvariable=self.button_text, font=("Bahnschrift", 16, "bold")
        ).pack(anchor="w", pady=20)
        self.listen_for_button()

    def listen_for_button(self):
        """Polls SDL from Tk's own loop: no thread, no window of SDL's own."""
        if self.pygame is None:
            try:
                from talk.ptt import start_sdl

                self.pygame = start_sdl()
                self.pads = {}
            except Exception as error:
                self.button_text.set(
                    f"Can't read controllers here ({error.__class__.__name__})."
                )
                return
        pygame = self.pygame
        for event in pygame.event.get():
            if event.type == pygame.JOYDEVICEADDED:
                pad = pygame.joystick.Joystick(event.device_index)
                self.pads[pad.get_instance_id()] = pad
            elif event.type == pygame.JOYBUTTONDOWN:
                pad = self.pads.get(event.instance_id)
                self.button = (pad.get_name() if pad else None, event.button)
                self.button_text.set(
                    f"Got it: button {event.button} on {self.button[0] or 'your device'}."
                )
        if self.at == 1:
            self.root.after(20, self.listen_for_button)

    def step_engineer(self):
        self.heading(
            "How it talks",
            "Clean is straight talk. Spicy swears at the situation and the other cars, never at you. "
            "Neither mode ever uses a slur.",
        )
        self.choice("Clean", "clean", self.talk)
        self.choice("Spicy", "spicy", self.talk)
        ttk.Button(
            self.page, text="Hear it", style="Go.TButton", command=self.voice_test
        ).pack(anchor="w", pady=18)

    def voice_test(self):
        line = (
            "Pass into the Esses. Not before. Oh, get in there! Fucking lovely."
            if self.talk.get() == "spicy"
            else "Pass into the Esses. Not before. Oh, get in there! Lovely."
        )

        def speak():
            try:
                from radio.voice import Voice

                Voice(out_loud=True, azure=False).say(line)
            except Exception as error:
                print(f"[voice test: {error}]")

        threading.Thread(target=speak, daemon=True).start()

    def step_clips(self):
        self.heading(
            "Auto-clips",
            "Apex records only the Le Mans Ultimate window, the game sound and your microphone "
            "while you race, then makes a short of your best radio moment after every race. "
            "Everything stays on this PC, in Videos\\Apex. You can turn it off any time.",
        )
        self.choice("Yes, make shorts", True, self.clips)
        self.choice("No thanks", False, self.clips)

    def step_game(self):
        folder = find_lmu()
        if folder:
            self.heading(
                "You're set",
                "Found Le Mans Ultimate. Start the game and join a session: "
                "Apex connects by itself when you start it.",
            )
        else:
            self.heading(
                "Almost set",
                "Apex couldn't find Le Mans Ultimate in your Steam libraries. "
                "That's fine if it's installed somewhere else: Apex connects "
                "to the running game either way.",
            )
        ttk.Label(
            self.page,
            text='Hold your radio button and ask:\n  "How\'s the fuel?"\n  "Where am I losing time?"\n  "What\'s the plan?"',
            justify="left",
        ).pack(anchor="w")

    def finish(self):
        profile = profile_from_answers(
            self.name.get(),
            self.car.get(),
            self.input.get(),
            self.talk.get() == "spicy",
            self.clips.get(),
            self.old,
        )
        save_profile(profile)
        if self.button is not None:
            from talk.ptt import save_button

            save_button(*self.button)
        self.root.destroy()


def sharp_on_scaled_screens():
    """Without this Windows stretches the window on a 125-150% display and the text blurs."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main():
    sharp_on_scaled_screens()
    root = tk.Tk()
    Wizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
