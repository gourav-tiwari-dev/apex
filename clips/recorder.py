"""Records a race the way the NVIDIA App would, so auto-clips need nothing else installed.

Picture: only the Le Mans Ultimate window (Windows Graphics Capture through ffmpeg's gfxcapture),
never the desktop. Encoded on the graphics card's own encoder when there is one (NVENC, AMD,
Intel), in 30 s pieces so a crash loses at most the last piece. Measured 30 Sep on his laptop:
2560x1600 at 60 fps for about 4% extra CPU on the desktop (ddagrab does not work on a laptop
whose screen hangs off the integrated GPU, gfxcapture does).
Sound: what the driver hears (WASAPI loopback: the game and Apex's radio) and the microphone
(his push-to-talk questions), each to its own file.
stop() joins them into one file shaped like an NVIDIA recording: video, audio 0 = game + radio,
audio 1 = microphone. That is the shape clips/find_moments.py reads.
"""

import os
import subprocess
import threading
import time
import wave

GAME_WINDOW = r"(?i)Le Mans Ultimate\.exe"
PIECE_S = 30
# fastest first; the first one ffmpeg can open on this machine is used
ENCODERS = [
    ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "12M"],
    ["-c:v", "h264_amf", "-quality", "speed", "-rc", "vbr_peak", "-b:v", "12M"],
    ["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", "12M"],
    ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"],
]


def working_encoder():
    """The first encoder that can encode one test frame here."""
    for encoder in ENCODERS:
        test = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
             *encoder, "-f", "null", "-"],
            capture_output=True,
        )
        if test.returncode == 0:
            return encoder
    return None


class SoundRecorder:
    """One input (loopback or microphone) written to a WAV file as it arrives."""

    def __init__(self, audio, device, path):
        self.audio = audio
        self.device = device
        self.path = path
        self.first_sample_at = None
        self.stream = None
        self.file = None

    def start(self):
        import pyaudiowpatch as pyaudio

        channels = max(1, min(2, int(self.device["maxInputChannels"])))
        rate = int(self.device["defaultSampleRate"])
        self.file = wave.open(self.path, "wb")
        self.file.setnchannels(channels)
        self.file.setsampwidth(2)
        self.file.setframerate(rate)

        def arrived(data, frame_count, time_info, status):
            if self.first_sample_at is None:
                # the first block was captured frame_count samples before it reached us
                self.first_sample_at = time.perf_counter() - frame_count / rate
            self.file.writeframes(data)
            return (None, pyaudio.paContinue)

        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=self.device["index"],
            frames_per_buffer=1024,
            stream_callback=arrived,
        )

    def stop(self):
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
        if self.file is not None:
            self.file.close()


class RaceRecorder:
    def __init__(self, out_dir, window=GAME_WINDOW, with_microphone=True):
        self.out_dir = out_dir
        self.window = window
        self.with_microphone = with_microphone
        self.video = None
        self.video_started_at = None
        self.sounds = []
        self.audio = None
        self.silence = None
        self.piece_dir = None

    def start(self):
        """Starts recording; False (and a reason printed) if this machine can't."""
        encoder = working_encoder()
        if encoder is None:
            print("[clips] no video encoder works here - no recording")
            return False
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.piece_dir = os.path.join(self.out_dir, f"race_{stamp}")
        os.makedirs(self.piece_dir, exist_ok=True)
        capture = f"gfxcapture=window_exe='{self.window}':max_framerate=60:capture_cursor=0"
        self.video = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", capture,
             "-vf", "hwdownload,format=bgra", "-r", "60", *encoder, "-g", "120",
             "-f", "segment", "-segment_time", str(PIECE_S), "-reset_timestamps", "1",
             "-progress", "pipe:1", os.path.join(self.piece_dir, "piece_%04d.mp4")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        threading.Thread(target=self.watch_first_frame, daemon=True).start()
        self.start_sound()
        return True

    def watch_first_frame(self):
        """ffmpeg's progress: the wall time of frame 0 = now minus the frames encoded so far.
        The output is a constant 60 fps (ffmpeg repeats frames when the window doesn't change),
        and out_time reads N/A while writing pieces, so the frame count is the clock."""
        for line in self.video.stdout:
            if self.video_started_at is not None:
                continue                      # keep reading so ffmpeg never blocks on the pipe
            if line.startswith("frame="):
                frames = line.split("=")[1].strip()
                if frames.isdigit() and int(frames) > 0:
                    self.video_started_at = time.perf_counter() - int(frames) / 60

    def start_sound(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            print("[clips] pyaudiowpatch missing - the clips will have no sound")
            return
        self.audio = pyaudio.PyAudio()
        loopback = self.audio.get_default_wasapi_loopback()
        self.keep_loopback_flowing(loopback)
        inputs = [("game", loopback)]
        if self.with_microphone:
            inputs.append(("mic", self.audio.get_default_input_device_info()))
        for name, device in inputs:
            sound = SoundRecorder(self.audio, device, os.path.join(self.piece_dir, f"{name}.wav"))
            sound.start()
            self.sounds.append(sound)

    def keep_loopback_flowing(self, loopback):
        """Windows only sends loopback sound while something plays: a quiet moment would send
        nothing, and every gap would pull the sound out of step with the picture (seen 30 Sep:
        8 s of silence gave no samples at all). Playing silence on the same speakers keeps it
        coming, and silence is recorded as silence."""
        import pyaudiowpatch as pyaudio

        speakers = None
        for number in range(self.audio.get_device_count()):
            device = self.audio.get_device_info_by_index(number)
            if device["name"] == loopback["name"].replace(" [Loopback]", "") and \
                    device["maxOutputChannels"] > 0 and device["hostApi"] == loopback["hostApi"]:
                speakers = device
                break
        if speakers is None:
            print("[clips] could not find the speakers behind the loopback - sound may drift")
            return
        channels = max(1, min(2, int(speakers["maxOutputChannels"])))
        rate = int(speakers["defaultSampleRate"])
        quiet = bytes(2 * channels * 1024)

        def more_silence(data, frame_count, time_info, status):
            return (quiet[: 2 * channels * frame_count], pyaudio.paContinue)

        self.silence = self.audio.open(
            format=pyaudio.paInt16, channels=channels, rate=rate, output=True,
            output_device_index=speakers["index"], frames_per_buffer=1024,
            stream_callback=more_silence,
        )

    def stop(self):
        """Stops and joins everything into race.mp4; returns its path, or None."""
        if self.video is None:
            return None
        try:
            self.video.stdin.write("q")
            self.video.stdin.flush()
        except OSError:
            pass
        try:
            self.video.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.video.kill()
        for sound in self.sounds:
            sound.stop()
        if self.silence is not None:
            self.silence.stop_stream()
            self.silence.close()
        if self.audio is not None:
            self.audio.terminate()
        pieces = sorted(p for p in os.listdir(self.piece_dir) if p.startswith("piece_"))
        if not pieces or self.video_started_at is None:
            print("[clips] the game window was never captured - is LMU running in a window?")
            return None
        return self.join(pieces)

    def join(self, pieces):
        listing = os.path.join(self.piece_dir, "pieces.txt")
        with open(listing, "w") as f:
            for piece in pieces:
                f.write(f"file '{piece}'\n")
        inputs = ["-f", "concat", "-safe", "0", "-i", listing]
        maps = ["-map", "0:v"]
        number = 0
        for sound in self.sounds:
            if sound.first_sample_at is None:
                print(f"[clips] {os.path.basename(sound.path)} got no sound - left out")
                continue
            number += 1
            # a positive offset: the sound started after the picture, so it is delayed to match
            offset = sound.first_sample_at - self.video_started_at
            inputs += ["-itsoffset", f"{offset:.3f}", "-i", sound.path]
            maps += ["-map", f"{number}:a"]
        race = os.path.join(self.piece_dir, "race.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *inputs, *maps, "-c:v", "copy", "-c:a", "aac",
             "-b:a", "192k", "-shortest", race],
            check=True,
        )
        for piece in pieces:
            os.remove(os.path.join(self.piece_dir, piece))
        os.remove(listing)
        return race
