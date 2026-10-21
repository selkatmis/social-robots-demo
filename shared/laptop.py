import io
import shutil
import subprocess
import time
import wave

import sounddevice as sd
import webrtcvad

RATE = 16000
FRAME = 480         # 30 ms at 16 kHz, a frame size webrtcvad accepts
FRAME_S = FRAME / RATE
PRE_ROLL = 10       # quiet frames kept from just before speech, so the first word isn't clipped
MIN_SPEECH = 10     # speech frames (0.3 s) needed; shorter sounds (a cough, a click) are ignored


class LaptopRobot:
    """Stand-in for a robot: your laptop's microphone and speaker, or the keyboard with typing=True.

    Has the say/listen/set_expression/play_gesture/stop/shutdown methods the robot scripts use,
    so you can try a conversation without the robot.
    """

    def __init__(self, llm, typing=False, voice="Xander", hints=None, end_silence=1.0):
        self.llm = llm
        self.typing = typing
        self.voice = voice              # macOS voice; `say -v '?'` lists them
        self.hints = hints              # words speech recognition should expect, e.g. names
        self.end_silence = end_silence  # seconds of quiet that end your turn
        self._vad = webrtcvad.Vad(2)

    def listen(self, timeout=10):
        if self.typing:
            return input("Jij:    ").strip() or "[TIMEOUT]"
        print("(luisteren...)", end="\r", flush=True)
        frames = self._record(timeout)
        text = self.llm.transcribe(_to_wav(frames), hints=self.hints) if frames else ""
        if text:
            print(f"Jij:    {text}")
        return text or "[TIMEOUT]"

    def _record(self, timeout):
        """Wait for speech, then record until `end_silence` seconds of quiet."""
        frames, speech, quiet = [], 0, 0
        deadline = time.time() + timeout
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
            while True:
                frame = stream.read(FRAME)[0].tobytes()
                if self._vad.is_speech(frame, RATE):
                    speech += 1
                    quiet = 0
                elif speech:
                    quiet += 1
                frames.append(frame)
                if not speech:
                    frames = frames[-PRE_ROLL:]

                if speech and quiet * FRAME_S >= self.end_silence:
                    if speech >= MIN_SPEECH:
                        return frames
                    frames, speech, quiet = [], 0, 0  # just a noise: keep waiting
                if not speech and time.time() > deadline:
                    return []
                if len(frames) * FRAME_S > 30:
                    return frames

    def say(self, text):
        if not self.typing and shutil.which("say"):
            subprocess.run(["say", "-v", self.voice, text])

    def set_expression(self, expression):
        pass

    def play_gesture(self, gesture, speed=0.5):
        pass

    def stop(self):
        pass

    def shutdown(self):
        pass


def _to_wav(frames):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(frames))
    return buffer.getvalue()
