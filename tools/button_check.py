"""Is the push-to-talk button reaching Apex? Prints every controller and every button event,
and says whether push-to-talk would count it. No mic, no speech-to-text: just the button.

    python tools/button_check.py [seconds] [--third-session]   (default 60; run it with LMU focused)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ptt import load_button, start_sdl, Controller

numbers = [a for a in sys.argv[1:] if not a.startswith("--")]
seconds = float(numbers[0]) if numbers else 60.0
saved = load_button()
print(f"saved push-to-talk button: {saved}", flush=True)

# --third-session: build and drop two push-to-talks first, the way a race after qualifying and
# practice does (25 Sep: only the first session of a run could hear the button)
if "--third-session" in sys.argv:
    import gc
    for _ in range(2):
        earlier = Controller(saved)
        for _ in range(50):
            earlier.poll()
            time.sleep(0.01)
        del earlier
        gc.collect()
    print("(two earlier sessions built and dropped: this is session 3)", flush=True)

# the real Controller, fed the same events, so "counted" means exactly what Apex would do
controller = Controller(saved)
for instance_id, pad in controller.pads.items():
    print(f"controller open: {pad.get_name()!r} (id {instance_id})", flush=True)
pygame = controller.pygame
end = time.time() + seconds
downs = counted = 0
while time.time() < end:
    for event in pygame.event.get():
        if event.type == pygame.JOYDEVICEADDED:
            pad = pygame.joystick.Joystick(event.device_index)
            controller.pads[pad.get_instance_id()] = pad
            print(f"controller connected: {pad.get_name()!r} (id {pad.get_instance_id()})", flush=True)
        elif event.type == pygame.JOYDEVICEREMOVED:
            controller.pads.pop(event.instance_id, None)
            print(f"controller removed: id {event.instance_id}", flush=True)
        elif event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
            pad = controller.pads.get(event.instance_id)
            name = pad.get_name() if pad else "?"
            is_ptt = event.button == controller.button and controller.mine(event.instance_id)
            if event.type == pygame.JOYBUTTONDOWN:
                downs += 1
                counted += is_ptt
            state = "down" if event.type == pygame.JOYBUTTONDOWN else "up  "
            print(f"{time.strftime('%H:%M:%S')} button {event.button} {state} on {name!r}"
                  f"{'  <- PUSH-TO-TALK, counted' if is_ptt else ''}", flush=True)
    time.sleep(0.01)
print(f"done: {downs} button presses seen, {counted} counted as push-to-talk", flush=True)
