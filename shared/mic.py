from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Protocol, Tuple, Union

import numpy as np


logger = logging.getLogger(__name__)


class MicEventType(str, Enum):
    """Events emitted by :class:`ContinuousMic`."""

    SPEECH_STARTED = "speech_started"
    SPEECH_PAUSED = "speech_paused"
    SPEECH_RESUMED = "speech_resumed"
    PARTIAL_TRANSCRIPT = "partial_transcript"
    SPEECH_ENDED = "speech_ended"
    FINAL_TRANSCRIPT = "final_transcript"
    INTERRUPTION_DETECTED = "interruption_detected"
    ERROR = "error"


@dataclass(frozen=True)
class MicEvent:
    """A timestamped microphone/conversation event.

    ``timestamp`` is Unix time in seconds. Event-specific metrics and metadata
    are stored in ``data``. Transcript events set ``text`` and ``is_final``.
    """

    type: MicEventType
    timestamp: float
    utterance_id: Optional[int] = None
    text: Optional[str] = None
    is_final: bool = False
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MicrophoneDevice:
    """A sounddevice input device suitable for display or selection by index."""

    index: int
    name: str
    host_api: int
    max_input_channels: int
    default_sample_rate: float


class ContinuousMicError(RuntimeError):
    """Raised when microphone setup or lifecycle management fails."""


class VoiceActivityDetector(Protocol):
    """Replaceable streaming VAD interface."""

    def initialize(self, sample_rate: int, frame_samples: int) -> None:
        ...

    def reset(self) -> None:
        ...

    def speech_probability(self, audio: np.ndarray, sample_rate: int) -> float:
        ...


class Transcriber(Protocol):
    """Replaceable speech-recognition interface."""

    def prepare(self) -> None:
        ...

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        language: str,
        *,
        is_final: bool,
    ) -> str:
        ...


class SileroVAD:
    """Stateful Silero VAD adapter for 8 kHz or 16 kHz PCM frames."""

    def __init__(self) -> None:
        self._model: Any = None
        self._torch: Any = None

    def initialize(self, sample_rate: int, frame_samples: int) -> None:
        required_samples = 512 if sample_rate == 16000 else 256 if sample_rate == 8000 else None
        if required_samples is None:
            raise ValueError("Silero VAD supports only 8000 Hz and 16000 Hz audio")
        if frame_samples != required_samples:
            raise ValueError(
                f"Silero VAD requires {required_samples} samples per frame at {sample_rate} Hz"
            )

        if self._model is None:
            try:
                import torch
                from silero_vad import load_silero_vad
            except ImportError as error:
                raise ContinuousMicError(
                    "Silero VAD is unavailable. Install 'silero-vad' or use vad='webrtc'."
                ) from error

            self._torch = torch
            self._model = load_silero_vad()
        self.reset()

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset_states()

    def speech_probability(self, audio: np.ndarray, sample_rate: int) -> float:
        if self._model is None or self._torch is None:
            raise RuntimeError("Silero VAD has not been initialized")
        normalized = audio.astype(np.float32, copy=False) / 32768.0
        tensor = self._torch.from_numpy(normalized)
        with self._torch.no_grad():
            return float(self._model(tensor, sample_rate).item())


class WebRTCVAD:
    """Lightweight fallback VAD using the repository's existing dependency."""

    def __init__(self, aggressiveness: int = 2) -> None:
        self.aggressiveness = aggressiveness
        self._vad: Any = None

    def initialize(self, sample_rate: int, frame_samples: int) -> None:
        frame_ms = frame_samples * 1000 / sample_rate
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("WebRTC VAD does not support this sample rate")
        if frame_ms not in (10.0, 20.0, 30.0):
            raise ValueError("WebRTC VAD frames must be 10, 20, or 30 ms")
        try:
            import webrtcvad
        except ImportError as error:
            raise ContinuousMicError(
                "WebRTC VAD is unavailable. Install 'webrtcvad-wheels'."
            ) from error
        self._vad = webrtcvad.Vad(self.aggressiveness)

    def reset(self) -> None:
        return None

    def speech_probability(self, audio: np.ndarray, sample_rate: int) -> float:
        if self._vad is None:
            raise RuntimeError("WebRTC VAD has not been initialized")
        return 1.0 if self._vad.is_speech(audio.tobytes(), sample_rate) else 0.0


class FasterWhisperTranscriber:
    """Dutch Faster-Whisper backend with CPU-friendly defaults."""

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        partial_beam_size: int = 1,
        hotwords: Optional[str] = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.partial_beam_size = partial_beam_size
        self.hotwords = hotwords
        self._model: Any = None
        self._load_lock = threading.Lock()

    def prepare(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise ContinuousMicError(
                    "Faster-Whisper is unavailable. Install 'faster-whisper'."
                ) from error
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        language: str,
        *,
        is_final: bool,
    ) -> str:
        if sample_rate != 16000:
            raise ValueError("Faster-Whisper expects 16000 Hz audio in this microphone pipeline")
        self.prepare()
        waveform = audio.astype(np.float32, copy=False) / 32768.0
        segments, _ = self._model.transcribe(
            waveform,
            language=language,
            beam_size=self.beam_size if is_final else self.partial_beam_size,
            condition_on_previous_text=False,
            temperature=0.0,
            vad_filter=False,
            without_timestamps=True,
            hotwords=self.hotwords,
        )
        return " ".join(
            segment.text.strip() for segment in segments if segment.text.strip()
        ).strip()


EventCallback = Callable[[MicEvent], None]


@dataclass(frozen=True)
class _AudioFrame:
    samples: np.ndarray
    wall_time: float
    monotonic_time: float


@dataclass(frozen=True)
class _AsrTask:
    utterance_id: int
    audio: np.ndarray
    is_final: bool
    revision: int
    audio_duration_ms: float
    speech_ended_at: Optional[float] = None
    speech_ended_monotonic: Optional[float] = None
    queued_monotonic: float = field(default_factory=time.monotonic)


class _BoundedAsrQueue:
    """Small priority queue that coalesces partials and protects final tasks."""

    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._items: Deque[_AsrTask] = deque()
        self._condition = threading.Condition()
        self._closed = False

    def put(self, task: _AsrTask) -> Tuple[bool, List[_AsrTask]]:
        removed: List[_AsrTask] = []
        with self._condition:
            if self._closed:
                return False, removed

            if not task.is_final:
                for index in range(len(self._items) - 1, -1, -1):
                    existing = self._items[index]
                    if not existing.is_final and existing.utterance_id == task.utterance_id:
                        removed.append(existing)
                        self._items[index] = task
                        self._condition.notify()
                        return True, removed
                if len(self._items) >= self._max_size:
                    return False, removed
                self._items.append(task)
                self._condition.notify()
                return True, removed

            retained: Deque[_AsrTask] = deque()
            for existing in self._items:
                if not existing.is_final and existing.utterance_id == task.utterance_id:
                    removed.append(existing)
                else:
                    retained.append(existing)
            self._items = retained

            if len(self._items) >= self._max_size:
                for index, existing in enumerate(self._items):
                    if not existing.is_final:
                        removed.append(existing)
                        del self._items[index]
                        break
            if len(self._items) >= self._max_size:
                return False, removed

            insert_at = len(self._items)
            for index, existing in enumerate(self._items):
                if not existing.is_final:
                    insert_at = index
                    break
            self._items.insert(insert_at, task)
            self._condition.notify()
            return True, removed

    def get(self) -> Optional[_AsrTask]:
        with self._condition:
            while not self._items and not self._closed:
                self._condition.wait()
            if not self._items:
                return None
            return self._items.popleft()

    def close(self) -> List[_AsrTask]:
        with self._condition:
            self._closed = True
            removed = list(self._items)
            self._items.clear()
            self._condition.notify_all()
            return removed

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)


class _BoundedEventQueue:
    """Bounded callback queue that replaces stale partial transcript events."""

    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._items: Deque[MicEvent] = deque()
        self._condition = threading.Condition()
        self._closed = False

    def put(self, event: MicEvent) -> bool:
        with self._condition:
            if self._closed:
                return False
            if event.type == MicEventType.PARTIAL_TRANSCRIPT:
                for index in range(len(self._items) - 1, -1, -1):
                    existing = self._items[index]
                    if (
                        existing.type == MicEventType.PARTIAL_TRANSCRIPT
                        and existing.utterance_id == event.utterance_id
                    ):
                        self._items[index] = event
                        self._condition.notify()
                        return True

            if len(self._items) >= self._max_size:
                for index, existing in enumerate(self._items):
                    if existing.type == MicEventType.PARTIAL_TRANSCRIPT:
                        del self._items[index]
                        break
                else:
                    self._items.popleft()

            self._items.append(event)
            self._condition.notify()
            return True

    def get(self) -> Optional[MicEvent]:
        with self._condition:
            while not self._items and not self._closed:
                self._condition.wait()
            if not self._items:
                return None
            return self._items.popleft()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._items.clear()
            self._condition.notify_all()


class ContinuousMic:
    """Continuously capture speech and emit VAD/transcription events.

    By default this class uses Silero VAD and Faster-Whisper in Dutch. Pass
    custom ``vad`` and ``transcriber`` objects implementing the protocols above
    to substitute either backend. ``start(capture=False)`` plus ``feed_audio``
    can be used by tests or by a future non-PortAudio audio source.

    VAD-based interruption events are only a signal that speech-like audio was
    heard while ``robot_speaking`` was true. Without acoustic echo cancellation
    they cannot reliably distinguish a visitor from the robot's own speakers.
    """

    def __init__(
        self,
        *,
        device: Optional[Union[int, str]] = None,
        sample_rate: int = 16000,
        frame_samples: Optional[int] = None,
        vad: Union[str, VoiceActivityDetector] = "silero",
        vad_threshold: float = 0.5,
        transcriber: Optional[Union[str, Transcriber]] = "faster-whisper",
        language: str = "nl",
        model_size: str = "medium",
        asr_device: str = "cpu",
        compute_type: str = "int8",
        hotwords: Optional[str] = None,
        speech_onset_ms: int = 200,
        pause_ms: int = 250,
        end_silence_ms: int = 800,
        pre_roll_ms: int = 250,
        max_utterance_seconds: float = 30.0,
        enable_partials: bool = True,
        partial_interval_seconds: float = 2.0,
        partial_min_audio_seconds: float = 1.0,
        audio_queue_size: int = 128,
        asr_queue_size: int = 4,
        event_queue_size: int = 256,
        callback: Optional[EventCallback] = None,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.vad_threshold = vad_threshold
        self.language = language
        self.speech_onset_ms = speech_onset_ms
        self.pause_ms = pause_ms
        self.end_silence_ms = end_silence_ms
        self.pre_roll_ms = pre_roll_ms
        self.max_utterance_seconds = max_utterance_seconds
        self.enable_partials = enable_partials
        self.partial_interval_seconds = partial_interval_seconds
        self.partial_min_audio_seconds = partial_min_audio_seconds
        self._audio_queue_size = audio_queue_size
        self._asr_queue_size = asr_queue_size
        self._event_queue_size = event_queue_size

        if isinstance(vad, str):
            normalized_vad = vad.lower()
            if normalized_vad == "silero":
                self._vad: VoiceActivityDetector = SileroVAD()
                default_frame_samples = 512 if sample_rate == 16000 else 256
            elif normalized_vad == "webrtc":
                self._vad = WebRTCVAD()
                default_frame_samples = int(sample_rate * 0.03)
            else:
                raise ValueError("vad must be 'silero', 'webrtc', or a VAD backend object")
        else:
            self._vad = vad
            default_frame_samples = 512 if sample_rate == 16000 else int(sample_rate * 0.032)
        self.frame_samples = frame_samples or default_frame_samples

        if transcriber == "faster-whisper":
            self._transcriber: Optional[Transcriber] = FasterWhisperTranscriber(
                model_size=model_size,
                device=asr_device,
                compute_type=compute_type,
                hotwords=hotwords,
            )
        elif isinstance(transcriber, str):
            raise ValueError("transcriber must be 'faster-whisper', None, or a backend object")
        else:
            self._transcriber = transcriber

        self._validate_configuration()
        self._frame_duration_ms = self.frame_samples * 1000.0 / self.sample_rate
        self._onset_frames = self._frames_for_ms(self.speech_onset_ms)
        self._pause_frames = self._frames_for_ms(self.pause_ms)
        self._end_silence_frames = self._frames_for_ms(self.end_silence_ms)
        self._pre_roll_frames = max(
            0, math.ceil(self.pre_roll_ms / self._frame_duration_ms)
        )
        self._max_active_frames = max(
            1, math.ceil(self.max_utterance_seconds * self.sample_rate / self.frame_samples)
        )
        self._partial_interval_frames = max(
            1, math.ceil(self.partial_interval_seconds * self.sample_rate / self.frame_samples)
        )
        self._partial_min_frames = max(
            1, math.ceil(self.partial_min_audio_seconds * self.sample_rate / self.frame_samples)
        )

        self._callbacks: Dict[str, List[EventCallback]] = defaultdict(list)
        self._callbacks_lock = threading.Lock()
        if callback is not None:
            self.on("*", callback)

        self._lifecycle_lock = threading.Lock()
        self._robot_state_lock = threading.Lock()
        self._utterance_state_lock = threading.Lock()
        self._asr_state_lock = threading.Lock()
        self._running = False
        self._robot_speaking = False
        self._stream: Any = None
        self._processing_thread: Optional[threading.Thread] = None
        self._asr_thread: Optional[threading.Thread] = None
        self._event_thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._audio_queue: queue.Queue[_AudioFrame]
        self._asr_queue: _BoundedAsrQueue
        self._event_queue: _BoundedEventQueue
        self._dropped_audio_frames = 0
        self._dropped_asr_tasks = 0
        self._pending_stream_status: Optional[str] = None
        self._partial_inflight: set[int] = set()
        self._partial_revisions: Dict[int, int] = {}
        self._last_partial_text: Dict[int, str] = {}
        self._active_utterance_id: Optional[int] = None
        self._utterance_counter = 0
        self._reset_segmenter()

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return self._running

    @property
    def robot_speaking(self) -> bool:
        with self._robot_state_lock:
            return self._robot_speaking

    @property
    def frame_duration_ms(self) -> float:
        return self._frame_duration_ms

    @staticmethod
    def list_microphones() -> List[MicrophoneDevice]:
        """Return all input-capable PortAudio devices."""

        try:
            import sounddevice as sd
        except ImportError as error:
            raise ContinuousMicError("Install 'sounddevice' to enumerate microphones") from error
        devices = []
        for index, device in enumerate(sd.query_devices()):
            max_inputs = int(device["max_input_channels"])
            if max_inputs > 0:
                devices.append(
                    MicrophoneDevice(
                        index=index,
                        name=str(device["name"]),
                        host_api=int(device["hostapi"]),
                        max_input_channels=max_inputs,
                        default_sample_rate=float(device["default_samplerate"]),
                    )
                )
        return devices

    def on(self, event_type: Union[MicEventType, str], callback: EventCallback) -> EventCallback:
        """Register a callback for one event type or ``"*"`` for every event."""

        key = event_type.value if isinstance(event_type, MicEventType) else str(event_type)
        valid_types = {event.value for event in MicEventType}
        if key != "*" and key not in valid_types:
            raise ValueError(f"Unknown microphone event type: {key}")
        with self._callbacks_lock:
            self._callbacks[key].append(callback)
        return callback

    def off(self, event_type: Union[MicEventType, str], callback: EventCallback) -> None:
        """Remove a previously registered callback."""

        key = event_type.value if isinstance(event_type, MicEventType) else str(event_type)
        with self._callbacks_lock:
            callbacks = self._callbacks.get(key, [])
            if callback in callbacks:
                callbacks.remove(callback)

    def set_robot_speaking(self, speaking: bool) -> None:
        """Set state sampled on speech frames for one advisory event per utterance."""

        with self._robot_state_lock:
            self._robot_speaking = bool(speaking)

    def start(self, *, capture: bool = True) -> "ContinuousMic":
        """Start worker threads and, by default, the physical microphone stream."""

        with self._lifecycle_lock:
            if self._running:
                return self
            lingering_threads = (
                self._processing_thread,
                self._asr_thread,
                self._event_thread,
            )
            if any(thread is not None and thread.is_alive() for thread in lingering_threads):
                raise ContinuousMicError(
                    "Previous microphone workers are still shutting down; try again shortly"
                )
            self._stopping.clear()
            self._running = True

        self._audio_queue = queue.Queue(maxsize=self._audio_queue_size)
        self._asr_queue = _BoundedAsrQueue(self._asr_queue_size)
        self._event_queue = _BoundedEventQueue(self._event_queue_size)
        self._dropped_audio_frames = 0
        self._dropped_asr_tasks = 0
        self._pending_stream_status = None
        self._partial_inflight.clear()
        self._partial_revisions.clear()
        self._last_partial_text.clear()
        self._reset_segmenter()

        try:
            self._vad.initialize(self.sample_rate, self.frame_samples)
            self._vad.reset()
        except Exception as error:
            with self._lifecycle_lock:
                self._running = False
            raise ContinuousMicError(f"Could not initialize VAD: {error}") from error

        if capture:
            try:
                import sounddevice as sd

                sd.check_input_settings(
                    device=self.device,
                    channels=1,
                    dtype="int16",
                    samplerate=self.sample_rate,
                )
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.frame_samples,
                    device=self.device,
                    channels=1,
                    dtype="int16",
                    callback=self._audio_callback,
                )
            except Exception as error:
                with self._lifecycle_lock:
                    self._running = False
                raise ContinuousMicError(f"Could not open microphone input: {error}") from error

        self._event_thread = threading.Thread(
            target=self._event_loop,
            name="continuous-mic-events",
            daemon=True,
        )
        self._event_thread.start()

        self._processing_thread = threading.Thread(
            target=self._processing_loop,
            name="continuous-mic-vad",
            daemon=True,
        )
        self._processing_thread.start()

        if capture:
            try:
                self._stream.start()
            except Exception as error:
                self._emit_error(
                    "Could not start microphone input",
                    stage="capture_start",
                    recoverable=False,
                    error=error,
                )
                self.stop()
                raise ContinuousMicError(f"Could not start microphone input: {error}") from error

        if self._transcriber is not None:
            self._asr_thread = threading.Thread(
                target=self._asr_loop,
                name="continuous-mic-asr",
                daemon=True,
            )
            self._asr_thread.start()
        else:
            self._asr_thread = None
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """Stop capture, discard unfinished work, and release resources."""

        with self._lifecycle_lock:
            was_running = self._running
            self._running = False
            if was_running:
                self._stopping.set()
        if not was_running:
            return

        deadline = time.monotonic() + max(0.0, timeout)
        removed_tasks = self._asr_queue.close()
        self._discard_asr_tasks(removed_tasks)
        self._event_queue.close()

        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception as error:
                logger.warning("Could not stop microphone stream: %s", error)
            try:
                stream.close()
            except Exception as error:
                logger.warning("Could not close microphone stream: %s", error)

        self._discard_audio_frames()
        self._join_thread(self._processing_thread, deadline)
        self._join_thread(self._asr_thread, deadline)
        self._join_thread(self._event_thread, deadline)
        if self._processing_thread is None or not self._processing_thread.is_alive():
            try:
                self._vad.reset()
            except Exception:
                logger.debug("VAD reset failed during shutdown", exc_info=True)

    def feed_audio(self, audio: np.ndarray, *, timestamp: Optional[float] = None) -> None:
        """Feed exact PCM frames when using ``start(capture=False)``.

        The array may contain one or several complete frames. Integer input is
        interpreted as PCM16; floating-point input must be normalized to
        ``[-1.0, 1.0]``.
        """

        if not self.is_running:
            raise ContinuousMicError("ContinuousMic must be started before feeding audio")
        samples = self._to_pcm16(audio)
        if len(samples) % self.frame_samples:
            raise ValueError(
                f"Audio length must be a multiple of frame_samples ({self.frame_samples})"
            )
        base_wall = timestamp if timestamp is not None else time.time()
        base_monotonic = time.monotonic()
        frame_seconds = self.frame_samples / self.sample_rate
        for index, start in enumerate(range(0, len(samples), self.frame_samples)):
            offset = index * frame_seconds
            self._enqueue_audio(
                _AudioFrame(
                    samples=samples[start : start + self.frame_samples].copy(),
                    wall_time=base_wall + offset,
                    monotonic_time=base_monotonic + offset,
                )
            )

    def __enter__(self) -> "ContinuousMic":
        return self.start()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.stop()

    def _validate_configuration(self) -> None:
        if self.sample_rate <= 0 or self.frame_samples <= 0:
            raise ValueError("sample_rate and frame_samples must be positive")
        if not 0.0 <= self.vad_threshold <= 1.0:
            raise ValueError("vad_threshold must be between 0 and 1")
        if self.speech_onset_ms <= 0 or self.end_silence_ms <= 0:
            raise ValueError("speech onset and end silence must be positive")
        if self.pause_ms <= 0 or self.pause_ms >= self.end_silence_ms:
            raise ValueError("pause_ms must be positive and shorter than end_silence_ms")
        if self.pre_roll_ms < 0 or self.max_utterance_seconds <= 0:
            raise ValueError("pre-roll cannot be negative and maximum duration must be positive")
        if self.partial_interval_seconds <= 0 or self.partial_min_audio_seconds <= 0:
            raise ValueError("partial transcription timings must be positive")
        if min(self._audio_queue_size, self._asr_queue_size, self._event_queue_size) <= 0:
            raise ValueError("queue sizes must be positive")

    def _frames_for_ms(self, milliseconds: float) -> int:
        return max(1, math.ceil(milliseconds / self._frame_duration_ms))

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if self._stopping.is_set():
            return
        if status:
            self._pending_stream_status = str(status)
        if frames != self.frame_samples:
            self._pending_stream_status = (
                f"PortAudio supplied {frames} samples; expected {self.frame_samples}"
            )
            return
        try:
            samples = np.asarray(indata[:, 0], dtype=np.int16).copy()
            self._enqueue_audio(
                _AudioFrame(samples=samples, wall_time=time.time(), monotonic_time=time.monotonic())
            )
        except Exception:
            self._dropped_audio_frames += 1

    def _enqueue_audio(self, frame: _AudioFrame) -> None:
        if self._stopping.is_set():
            return
        try:
            self._audio_queue.put_nowait(frame)
            return
        except queue.Full:
            pass
        try:
            self._audio_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._audio_queue.put_nowait(frame)
        except queue.Full:
            pass
        self._dropped_audio_frames += 1

    def _discard_audio_frames(self) -> None:
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                return

    def _processing_loop(self) -> None:
        reported_drops = 0
        last_drop_report = 0.0
        last_vad_error = 0.0
        try:
            while not self._stopping.is_set():
                try:
                    frame = self._audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                now = time.monotonic()
                if self._pending_stream_status is not None:
                    status, self._pending_stream_status = self._pending_stream_status, None
                    self._emit_error(
                        status,
                        stage="capture",
                        recoverable=True,
                    )
                if self._dropped_audio_frames > reported_drops and now - last_drop_report >= 1.0:
                    dropped = self._dropped_audio_frames - reported_drops
                    reported_drops = self._dropped_audio_frames
                    last_drop_report = now
                    self._emit_error(
                        "Audio processing fell behind; oldest frames were dropped",
                        stage="audio_queue",
                        recoverable=True,
                        extra={"dropped_frames": dropped},
                    )

                try:
                    probability = float(
                        self._vad.speech_probability(frame.samples, self.sample_rate)
                    )
                    probability = min(1.0, max(0.0, probability))
                except Exception as error:
                    if now - last_vad_error >= 1.0:
                        last_vad_error = now
                        self._emit_error(
                            "Voice activity detection failed",
                            stage="vad",
                            recoverable=True,
                            error=error,
                        )
                    continue
                if self._stopping.is_set():
                    break
                self._process_vad_frame(frame, probability)
        finally:
            if self._stopping.is_set():
                self._reset_segmenter()

    def _process_vad_frame(self, frame: _AudioFrame, probability: float) -> None:
        is_speech = probability >= self.vad_threshold
        if self._active_utterance_id is None:
            self._pre_roll.append(frame.samples)
            if is_speech:
                self._candidate_speech_frames += 1
            else:
                self._candidate_speech_frames = 0
            if self._candidate_speech_frames >= self._onset_frames:
                self._start_utterance(frame, probability)
            return

        self._utterance_frames.append(frame.samples)
        self._active_frame_count += 1
        if is_speech:
            self._speech_frame_count += 1
            if self._pause_emitted:
                pause_duration_ms = self._quiet_frames * self._frame_duration_ms
                self._emit(
                    MicEventType.SPEECH_RESUMED,
                    utterance_id=self._active_utterance_id,
                    timestamp=frame.wall_time,
                    data={"pause_duration_ms": pause_duration_ms},
                )
            self._quiet_frames = 0
            self._pause_emitted = False
            self._maybe_emit_interruption(frame)
        else:
            self._quiet_frames += 1
            if not self._pause_emitted and self._quiet_frames >= self._pause_frames:
                self._pause_emitted = True
                self._emit(
                    MicEventType.SPEECH_PAUSED,
                    utterance_id=self._active_utterance_id,
                    timestamp=frame.wall_time,
                    data={"silence_ms": self._quiet_frames * self._frame_duration_ms},
                )

        if self._quiet_frames >= self._end_silence_frames:
            self._finalize_utterance(reason="silence", frame=frame)
            return
        if self._active_frame_count >= self._max_active_frames:
            self._finalize_utterance(reason="max_duration", frame=frame)
            return
        self._maybe_schedule_partial()

    def _start_utterance(self, frame: _AudioFrame, probability: float) -> None:
        self._utterance_counter += 1
        utterance_id = self._utterance_counter
        with self._utterance_state_lock:
            self._active_utterance_id = utterance_id
        self._utterance_frames = list(self._pre_roll)
        self._active_frame_count = self._candidate_speech_frames
        self._speech_frame_count = self._candidate_speech_frames
        self._quiet_frames = 0
        self._pause_emitted = False
        self._interruption_emitted = False
        self._last_partial_frame_count = 0
        self._candidate_speech_frames = 0
        robot_speaking = self.robot_speaking
        estimated_onset = frame.wall_time - (
            (self._onset_frames - 1) * self._frame_duration_ms / 1000.0
        )
        self._emit(
            MicEventType.SPEECH_STARTED,
            utterance_id=utterance_id,
            timestamp=frame.wall_time,
            data={
                "estimated_onset_timestamp": estimated_onset,
                "onset_confirmation_ms": self._onset_frames * self._frame_duration_ms,
                "pre_roll_ms": self.pre_roll_ms,
                "speech_probability": probability,
                "robot_speaking": robot_speaking,
            },
        )
        self._maybe_emit_interruption(frame, robot_speaking=robot_speaking)
        self._maybe_schedule_partial()

    def _maybe_emit_interruption(
        self,
        frame: _AudioFrame,
        *,
        robot_speaking: Optional[bool] = None,
    ) -> None:
        if self._interruption_emitted:
            return
        if robot_speaking is None:
            robot_speaking = self.robot_speaking
        if not robot_speaking:
            return
        self._interruption_emitted = True
        self._emit(
            MicEventType.INTERRUPTION_DETECTED,
            utterance_id=self._active_utterance_id,
            timestamp=frame.wall_time,
            data={
                "robot_speaking": True,
                "verified": False,
                "limitation": "VAD cannot distinguish visitor speech from speaker echo",
            },
        )

    def _maybe_schedule_partial(self) -> None:
        utterance_id = self._active_utterance_id
        if (
            self._stopping.is_set()
            or not self.enable_partials
            or self._transcriber is None
            or utterance_id is None
            or self._active_frame_count < self._partial_min_frames
            or self._active_frame_count - self._last_partial_frame_count
            < self._partial_interval_frames
        ):
            return
        with self._asr_state_lock:
            if utterance_id in self._partial_inflight:
                return
            revision = self._partial_revisions.get(utterance_id, 0) + 1
            self._partial_revisions[utterance_id] = revision
            self._partial_inflight.add(utterance_id)
        self._last_partial_frame_count = self._active_frame_count
        audio = self._frames_to_audio(self._utterance_frames)
        task = _AsrTask(
            utterance_id=utterance_id,
            audio=audio,
            is_final=False,
            revision=revision,
            audio_duration_ms=len(audio) * 1000.0 / self.sample_rate,
        )
        accepted, removed = self._asr_queue.put(task)
        self._discard_asr_tasks(removed)
        if not accepted:
            with self._asr_state_lock:
                self._partial_inflight.discard(utterance_id)
                self._dropped_asr_tasks += 1

    def _finalize_utterance(self, reason: str, frame: _AudioFrame) -> None:
        utterance_id = self._active_utterance_id
        if utterance_id is None:
            return
        wall_time = frame.wall_time
        monotonic_time = frame.monotonic_time
        silence_ms = self._quiet_frames * self._frame_duration_ms
        active_duration_ms = self._active_frame_count * self._frame_duration_ms
        full_audio_duration_ms = len(self._utterance_frames) * self._frame_duration_ms

        with self._utterance_state_lock:
            self._active_utterance_id = None
        self._emit(
            MicEventType.SPEECH_ENDED,
            utterance_id=utterance_id,
            timestamp=wall_time,
            data={
                "reason": reason,
                "silence_ms": silence_ms,
                "speech_end_detection_latency_ms": silence_ms if reason == "silence" else 0.0,
                "active_duration_ms": active_duration_ms,
                "buffered_audio_duration_ms": full_audio_duration_ms,
                "speech_frames": self._speech_frame_count,
                "dropped_audio_frames": self._dropped_audio_frames,
            },
        )

        if self._transcriber is not None:
            keep_quiet_frames = self._frames_for_ms(100)
            trim_frames = max(0, self._quiet_frames - keep_quiet_frames)
            final_frames = (
                self._utterance_frames[:-trim_frames]
                if trim_frames
                else self._utterance_frames
            )
            audio = self._frames_to_audio(final_frames)
            with self._asr_state_lock:
                revision = self._partial_revisions.get(utterance_id, 0) + 1
                self._partial_revisions[utterance_id] = revision
            task = _AsrTask(
                utterance_id=utterance_id,
                audio=audio,
                is_final=True,
                revision=revision,
                audio_duration_ms=len(audio) * 1000.0 / self.sample_rate,
                speech_ended_at=wall_time,
                speech_ended_monotonic=monotonic_time,
            )
            accepted, removed = self._asr_queue.put(task)
            self._discard_asr_tasks(removed)
            if not accepted:
                self._finish_asr_task(task)
                with self._asr_state_lock:
                    self._dropped_asr_tasks += 1
                self._emit_error(
                    "ASR queue is full; final utterance could not be transcribed",
                    stage="asr_queue",
                    recoverable=True,
                    utterance_id=utterance_id,
                    extra={"dropped_asr_tasks": self._dropped_asr_tasks},
                )

        recent_frames = self._utterance_frames[-(self._pre_roll_frames + self._onset_frames) :]
        self._pre_roll = deque(
            recent_frames,
            maxlen=self._pre_roll_frames + self._onset_frames,
        )
        self._utterance_frames = []
        self._candidate_speech_frames = 0
        self._active_frame_count = 0
        self._speech_frame_count = 0
        self._quiet_frames = 0
        self._pause_emitted = False
        self._interruption_emitted = False
        self._last_partial_frame_count = 0

    def _asr_loop(self) -> None:
        preparation_error: Optional[Exception] = None
        try:
            prepare = getattr(self._transcriber, "prepare", None)
            if prepare is not None:
                prepare()
        except Exception as error:
            preparation_error = error
            self._emit_error(
                "Speech recognition model could not be initialized",
                stage="asr_initialization",
                recoverable=False,
                error=error,
            )

        while True:
            task = self._asr_queue.get()
            if task is None:
                return
            if self._stopping.is_set():
                self._finish_asr_task(task)
                continue
            if preparation_error is not None:
                if task.is_final:
                    self._emit_error(
                        "Final transcription unavailable because ASR initialization failed",
                        stage="asr_final",
                        recoverable=False,
                        error=preparation_error,
                        utterance_id=task.utterance_id,
                    )
                self._finish_asr_task(task)
                continue

            started = time.monotonic()
            queue_wait_ms = (started - task.queued_monotonic) * 1000.0
            try:
                text = self._transcriber.transcribe(
                    task.audio,
                    self.sample_rate,
                    self.language,
                    is_final=task.is_final,
                )
                text = " ".join(text.split())
                completed = time.monotonic()
                transcription_latency_ms = (completed - started) * 1000.0
                if self._stopping.is_set():
                    continue
                if task.is_final:
                    end_to_transcript_ms = None
                    if task.speech_ended_monotonic is not None:
                        end_to_transcript_ms = (
                            completed - task.speech_ended_monotonic
                        ) * 1000.0
                    with self._asr_state_lock:
                        dropped_asr_tasks = self._dropped_asr_tasks
                    self._emit(
                        MicEventType.FINAL_TRANSCRIPT,
                        utterance_id=task.utterance_id,
                        text=text,
                        is_final=True,
                        data={
                            "language": self.language,
                            "revision": task.revision,
                            "audio_duration_ms": task.audio_duration_ms,
                            "asr_queue_wait_ms": queue_wait_ms,
                            "transcription_latency_ms": transcription_latency_ms,
                            "speech_end_to_transcript_ms": end_to_transcript_ms,
                            "speech_ended_timestamp": task.speech_ended_at,
                            "dropped_audio_frames": self._dropped_audio_frames,
                            "dropped_asr_tasks": dropped_asr_tasks,
                        },
                    )
                elif self._partial_is_current(task) and text:
                    previous = self._last_partial_text.get(task.utterance_id)
                    if text != previous:
                        self._last_partial_text[task.utterance_id] = text
                        self._emit(
                            MicEventType.PARTIAL_TRANSCRIPT,
                            utterance_id=task.utterance_id,
                            text=text,
                            is_final=False,
                            data={
                                "language": self.language,
                                "revision": task.revision,
                                "audio_duration_ms": task.audio_duration_ms,
                                "asr_queue_wait_ms": queue_wait_ms,
                                "transcription_latency_ms": transcription_latency_ms,
                                "provisional": True,
                            },
                        )
            except Exception as error:
                if not self._stopping.is_set():
                    self._emit_error(
                        "Speech transcription failed",
                        stage="asr_final" if task.is_final else "asr_partial",
                        recoverable=True,
                        error=error,
                        utterance_id=task.utterance_id,
                    )
            finally:
                self._finish_asr_task(task)

    def _partial_is_current(self, task: _AsrTask) -> bool:
        if task.is_final:
            return False
        with self._utterance_state_lock:
            active = self._active_utterance_id
        with self._asr_state_lock:
            latest_revision = self._partial_revisions.get(task.utterance_id)
        return active == task.utterance_id and latest_revision == task.revision

    def _finish_asr_task(self, task: _AsrTask) -> None:
        with self._asr_state_lock:
            self._partial_inflight.discard(task.utterance_id)
            if task.is_final:
                self._partial_revisions.pop(task.utterance_id, None)
                self._last_partial_text.pop(task.utterance_id, None)

    def _discard_asr_tasks(self, tasks: List[_AsrTask]) -> None:
        if not tasks:
            return
        with self._asr_state_lock:
            self._dropped_asr_tasks += len(tasks)
            for task in tasks:
                if not task.is_final:
                    self._partial_inflight.discard(task.utterance_id)

    def _event_loop(self) -> None:
        while True:
            event = self._event_queue.get()
            if event is None:
                return
            if self._stopping.is_set():
                continue
            with self._callbacks_lock:
                callbacks = list(self._callbacks.get(event.type.value, []))
                callbacks.extend(self._callbacks.get("*", []))
            for callback in callbacks:
                if self._stopping.is_set():
                    break
                try:
                    callback(event)
                except Exception:
                    logger.exception("ContinuousMic event callback failed for %s", event.type.value)

    def _emit(
        self,
        event_type: MicEventType,
        *,
        utterance_id: Optional[int] = None,
        timestamp: Optional[float] = None,
        text: Optional[str] = None,
        is_final: bool = False,
        data: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if self._stopping.is_set():
            return
        event = MicEvent(
            type=event_type,
            timestamp=timestamp if timestamp is not None else time.time(),
            utterance_id=utterance_id,
            text=text,
            is_final=is_final,
            data=dict(data or {}),
        )
        self._event_queue.put(event)

    def _emit_error(
        self,
        message: str,
        *,
        stage: str,
        recoverable: bool,
        error: Optional[Exception] = None,
        utterance_id: Optional[int] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        data: Dict[str, Any] = {
            "message": message,
            "stage": stage,
            "recoverable": recoverable,
        }
        if error is not None:
            data["exception_type"] = type(error).__name__
            data["exception"] = str(error)
        if extra:
            data.update(extra)
        self._emit(MicEventType.ERROR, utterance_id=utterance_id, data=data)

    def _reset_segmenter(self) -> None:
        self._pre_roll: Deque[np.ndarray] = deque(
            maxlen=self._pre_roll_frames + self._onset_frames
        )
        self._utterance_frames: List[np.ndarray] = []
        self._candidate_speech_frames = 0
        self._active_frame_count = 0
        self._speech_frame_count = 0
        self._quiet_frames = 0
        self._pause_emitted = False
        self._interruption_emitted = False
        self._last_partial_frame_count = 0
        with self._utterance_state_lock:
            self._active_utterance_id = None

    @staticmethod
    def _frames_to_audio(frames: List[np.ndarray]) -> np.ndarray:
        if not frames:
            return np.empty(0, dtype=np.int16)
        return np.concatenate(frames).astype(np.int16, copy=False)

    @staticmethod
    def _to_pcm16(audio: np.ndarray) -> np.ndarray:
        samples = np.asarray(audio).reshape(-1)
        if np.issubdtype(samples.dtype, np.floating):
            return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        return np.clip(samples, -32768, 32767).astype(np.int16, copy=False)

    @staticmethod
    def _join_thread(thread: Optional[threading.Thread], deadline: float) -> None:
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout=max(0.0, deadline - time.monotonic()))


__all__ = [
    "ContinuousMic",
    "ContinuousMicError",
    "FasterWhisperTranscriber",
    "MicEvent",
    "MicEventType",
    "MicrophoneDevice",
    "SileroVAD",
    "Transcriber",
    "VoiceActivityDetector",
    "WebRTCVAD",
]
