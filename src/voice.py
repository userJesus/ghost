"""Voice recorder — grava mic OU system audio e transcreve via Whisper.

Diferente do MeetingRecorder (que grava mic + loopback + screenshots pra doc final),
aqui é um fluxo curto: usuário grava uma pergunta falada, Whisper transcreve, texto
vai pro campo de input pra enviar pro GPT.
"""
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundcard as sc
import soundfile as sf

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = 1024


class VoiceRecorder:
    """Single-source audio recorder. Records either 'mic' or 'system' loopback."""

    def __init__(self):
        self._running = False
        self._source = "mic"
        self._audio: list[np.ndarray] = []
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._start_ts: float | None = None
        self._stop_ts: float | None = None
        self._error: str | None = None

    def is_running(self) -> bool:
        return self._running

    def source(self) -> str:
        return self._source

    def elapsed(self) -> float:
        if not self._start_ts:
            return 0.0
        end = self._stop_ts or time.time()
        return max(0.0, end - self._start_ts)

    def last_error(self) -> str | None:
        return self._error

    def start(self, source: str = "mic"):
        """source: 'mic' = default microphone, 'system' = WASAPI loopback."""
        if self._running:
            return
        if source not in ("mic", "system"):
            source = "mic"
        self._source = source
        self._audio.clear()
        self._start_ts = time.time()
        self._stop_ts = None
        self._error = None
        self._running = True

        target = self._system_loop if source == "system" else self._mic_loop
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def stop(self) -> Path | None:
        """Stop recording, write wav to a tempfile, return its Path.
        Returns None if no audio was captured."""
        if not self._running and not self._audio:
            return None
        self._running = False
        self._stop_ts = time.time()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

        with self._lock:
            if not self._audio:
                return None
            merged = np.concatenate(self._audio, axis=0)
            self._audio.clear()

        # Normaliza amplitude fraca (mic baixo) pra Whisper pegar melhor
        peak = float(np.max(np.abs(merged))) if merged.size else 0.0
        if 0 < peak < 0.3:
            merged = merged * (0.7 / peak)

        out = Path(tempfile.gettempdir()) / f"ghost_voice_{int(time.time() * 1000)}.wav"
        sf.write(str(out), merged, SAMPLE_RATE, subtype="PCM_16")
        return out

    def cancel(self):
        """Stop and discard. Do not transcribe."""
        self._running = False
        self._stop_ts = time.time()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            self._audio.clear()

    def _mic_loop(self):
        try:
            mic = sc.default_microphone()
            with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK_SIZE) as rec:
                while self._running:
                    data = rec.record(numframes=BLOCK_SIZE)
                    with self._lock:
                        self._audio.append(data.copy())
        except Exception as e:
            self._error = f"mic: {e}"
            self._running = False
            print(f"[voice] mic error: {e}", flush=True)

    def _system_loop(self):
        try:
            default_speaker = sc.default_speaker()
            loop = sc.get_microphone(id=str(default_speaker.name), include_loopback=True)
            with loop.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK_SIZE) as rec:
                while self._running:
                    data = rec.record(numframes=BLOCK_SIZE)
                    with self._lock:
                        self._audio.append(data.copy())
        except Exception as e:
            self._error = f"system: {e}"
            self._running = False
            print(f"[voice] system error: {e}", flush=True)
