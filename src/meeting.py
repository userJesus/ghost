import os
import subprocess
import time
import threading
import tempfile
from pathlib import Path
from datetime import datetime

import numpy as np
import soundcard as sc
import soundfile as sf
import imageio.v2 as imageio
import imageio_ffmpeg

from .capture import capture_fullscreen, capture_region
from .scroll_capture import capture_monitor
from PIL import Image


SAMPLE_RATE = 16000  # 16kHz mono = smaller files + enough for speech
CHANNELS = 1
BLOCK_SIZE = 1024

VIDEO_FPS = 5  # 5 frames per second is plenty for meetings
VIDEO_MAX_WIDTH = 1280  # downscale larger captures for file size


class MeetingRecorder:
    """Records system audio (loopback) + microphone simultaneously.

    Emits periodic screenshots from a chosen monitor for visual context.
    """

    def __init__(self):
        self._running = False
        self._start_time: float | None = None
        self._stop_time: float | None = None

        self._system_audio: list[np.ndarray] = []
        self._mic_audio: list[np.ndarray] = []
        self._screenshots: list[tuple[float, Image.Image]] = []  # (offset_sec, image)

        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._monitor: dict | None = None
        self._window_hwnd: int = 0
        self._screenshot_interval = 30.0  # seconds
        self._video_tmp_path: Path | None = None
        self._video_writer = None
        self._video_size: tuple[int, int] | None = None

    def is_running(self) -> bool:
        return self._running

    def elapsed(self) -> float:
        if not self._start_time:
            return 0.0
        end = self._stop_time or time.time()
        return end - self._start_time

    def start(self, monitor: dict | None = None, window_hwnd: int = 0,
              fallback_monitor: dict | None = None):
        if self._running:
            return
        self._system_audio.clear()
        self._mic_audio.clear()
        self._screenshots.clear()
        self._start_time = time.time()
        self._stop_time = None
        self._monitor = monitor
        self._window_hwnd = window_hwnd
        self._fallback_monitor = fallback_monitor or monitor

        self._video_tmp_path = Path(tempfile.gettempdir()) / f"ghost_video_{int(time.time())}.mp4"
        self._video_writer = None
        self._video_size = None

        self._running = True
        self._threads = [
            threading.Thread(target=self._system_loop, daemon=True),
            threading.Thread(target=self._mic_loop, daemon=True),
            threading.Thread(target=self._screenshot_loop, daemon=True),
            threading.Thread(target=self._video_loop, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._stop_time = time.time()
        for t in self._threads:
            t.join(timeout=5.0)
        self._threads = []
        try:
            if self._video_writer is not None:
                self._video_writer.close()
        except Exception:
            pass
        self._video_writer = None

    def _system_loop(self):
        """Record system output via WASAPI loopback."""
        try:
            default_speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(default_speaker.name), include_loopback=True)
            with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK_SIZE) as rec:
                while self._running:
                    data = rec.record(numframes=BLOCK_SIZE)
                    with self._lock:
                        self._system_audio.append(data.copy())
        except Exception as e:
            print(f"[meeting] system audio error: {e}", flush=True)

    def _mic_loop(self):
        """Record default microphone."""
        try:
            default_mic = sc.default_microphone()
            with default_mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK_SIZE) as rec:
                while self._running:
                    data = rec.record(numframes=BLOCK_SIZE)
                    with self._lock:
                        self._mic_audio.append(data.copy())
        except Exception as e:
            print(f"[meeting] mic error: {e}", flush=True)

    def _capture_target(self) -> Image.Image | None:
        """Capture based on precedence: window hwnd > monitor > fullscreen.

        If a window was chosen but it's minimized/closed/invalid, skip the frame
        rather than falling back to capturing the entire desktop.
        """
        if self._window_hwnd:
            try:
                import win32gui
                # Skip if window became invalid or minimized
                if not win32gui.IsWindow(self._window_hwnd):
                    return None
                if win32gui.IsIconic(self._window_hwnd):
                    return None
                rect = win32gui.GetWindowRect(self._window_hwnd)
                x, y = rect[0], rect[1]
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w > 0 and h > 0:
                    return capture_region(x, y, w, h)
                return None
            except Exception as e:
                print(f"[meeting] window rect error: {e}", flush=True)
                return None
        if self._monitor:
            return capture_monitor(self._monitor)
        if self._fallback_monitor:
            return capture_monitor(self._fallback_monitor)
        return capture_fullscreen()

    def _screenshot_loop(self):
        """Take periodic screenshots (in-memory only, used for summary)."""
        next_shot = self._screenshot_interval
        while self._running:
            elapsed = self.elapsed()
            if elapsed >= next_shot:
                try:
                    img = self._capture_target()
                    if img is not None:
                        with self._lock:
                            self._screenshots.append((elapsed, img))
                except Exception as e:
                    print(f"[meeting] screenshot error: {e}", flush=True)
                next_shot += self._screenshot_interval
            time.sleep(0.5)

    def _video_loop(self):
        """Record low-fps video of the target area to an MP4 file."""
        writer = None
        try:
            # Wait for a valid first frame (window may still be loading/minimized)
            first = None
            for _ in range(20):
                if not self._running:
                    return
                first = self._capture_target()
                if first is not None:
                    break
                time.sleep(0.25)
            if first is None:
                print("[meeting] no initial frame available for video", flush=True)
                return
            w, h = first.size
            if w > VIDEO_MAX_WIDTH:
                scale = VIDEO_MAX_WIDTH / w
                w = VIDEO_MAX_WIDTH
                h = int(h * scale)
            if w % 2 != 0:
                w -= 1
            if h % 2 != 0:
                h -= 1
            self._video_size = (w, h)

            writer = imageio.get_writer(
                str(self._video_tmp_path),
                fps=VIDEO_FPS,
                codec="libx264",
                quality=7,
                pixelformat="yuv420p",
                macro_block_size=1,
            )
            self._video_writer = writer

            last_frame_arr = np.array(first.resize((w, h), Image.LANCZOS) if first.size != (w, h) else first)
            frame_interval = 1.0 / VIDEO_FPS
            next_frame = time.time() + frame_interval
            while self._running:
                now = time.time()
                if now >= next_frame:
                    try:
                        img = self._capture_target()
                        if img is not None:
                            if img.size != (w, h):
                                img = img.resize((w, h), Image.LANCZOS)
                            last_frame_arr = np.array(img)
                        # If capture returned None (window minimized/hidden), repeat last frame
                        # so audio/video stay in sync with real time.
                        writer.append_data(last_frame_arr)
                    except Exception as e:
                        print(f"[meeting] video frame error: {e}", flush=True)
                    next_frame = now + frame_interval
                time.sleep(0.02)
        except Exception as e:
            print(f"[meeting] video loop error: {e}", flush=True)
        finally:
            try:
                if writer is not None:
                    writer.close()
            except Exception:
                pass

    def export_audio(self, out_path: Path) -> Path:
        """Mix + save combined audio as WAV at 16kHz mono.

        Returns the path of the written file.
        """
        with self._lock:
            sys_buf = list(self._system_audio)
            mic_buf = list(self._mic_audio)

        sys_arr = np.concatenate(sys_buf, axis=0) if sys_buf else np.zeros((0, CHANNELS), dtype=np.float32)
        mic_arr = np.concatenate(mic_buf, axis=0) if mic_buf else np.zeros((0, CHANNELS), dtype=np.float32)

        target_len = max(len(sys_arr), len(mic_arr))
        if len(sys_arr) < target_len:
            pad = np.zeros((target_len - len(sys_arr), CHANNELS), dtype=np.float32)
            sys_arr = np.concatenate([sys_arr, pad], axis=0)
        if len(mic_arr) < target_len:
            pad = np.zeros((target_len - len(mic_arr), CHANNELS), dtype=np.float32)
            mic_arr = np.concatenate([mic_arr, pad], axis=0)

        if target_len == 0:
            mixed = np.zeros((0, CHANNELS), dtype=np.float32)
        else:
            mixed = sys_arr + mic_arr
            peak = np.max(np.abs(mixed))
            if peak > 1.0:
                mixed = mixed / peak

        if mixed.ndim == 2 and mixed.shape[1] == 1:
            mixed = mixed.flatten()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), mixed, SAMPLE_RATE, subtype="PCM_16")
        return out_path

    def get_screenshots(self) -> list[tuple[float, Image.Image]]:
        with self._lock:
            return list(self._screenshots)

    def export_audio_range(self, start_sec: float, end_sec: float,
                           out_path: Path) -> Path | None:
        """Save a slice [start_sec, end_sec) of the current audio buffer to WAV."""
        if end_sec <= start_sec:
            return None
        with self._lock:
            sys_buf = list(self._system_audio)
            mic_buf = list(self._mic_audio)
        if not sys_buf and not mic_buf:
            return None

        sys_arr = np.concatenate(sys_buf, axis=0) if sys_buf else np.zeros((0, CHANNELS), dtype=np.float32)
        mic_arr = np.concatenate(mic_buf, axis=0) if mic_buf else np.zeros((0, CHANNELS), dtype=np.float32)

        start_idx = int(start_sec * SAMPLE_RATE)
        end_idx = int(end_sec * SAMPLE_RATE)

        sys_slice = sys_arr[start_idx:end_idx] if start_idx < len(sys_arr) else np.zeros((0, CHANNELS), dtype=np.float32)
        mic_slice = mic_arr[start_idx:end_idx] if start_idx < len(mic_arr) else np.zeros((0, CHANNELS), dtype=np.float32)

        target_len = max(len(sys_slice), len(mic_slice))
        if target_len == 0:
            return None
        if len(sys_slice) < target_len:
            pad = np.zeros((target_len - len(sys_slice), CHANNELS), dtype=np.float32)
            sys_slice = np.concatenate([sys_slice, pad], axis=0)
        if len(mic_slice) < target_len:
            pad = np.zeros((target_len - len(mic_slice), CHANNELS), dtype=np.float32)
            mic_slice = np.concatenate([mic_slice, pad], axis=0)

        mixed = sys_slice + mic_slice
        peak = float(np.max(np.abs(mixed))) if len(mixed) else 1.0
        if peak > 1.0:
            mixed = mixed / peak

        if mixed.ndim == 2 and mixed.shape[1] == 1:
            mixed = mixed.flatten()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), mixed, SAMPLE_RATE, subtype="PCM_16")
        return out_path

    def export_video_with_audio(self, video_src: Path, audio_src: Path,
                                 output_path: Path) -> Path | None:
        """Mux silent video + wav audio into a single MP4 using bundled ffmpeg."""
        if video_src is None or not video_src.exists():
            return None
        if audio_src is None or not audio_src.exists():
            import shutil
            shutil.copy(str(video_src), str(output_path))
            return output_path
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            cmd = [
                ffmpeg_exe, "-y",
                "-i", str(video_src),
                "-i", str(audio_src),
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                str(output_path),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                print(f"[meeting] ffmpeg mux error: {result.stderr[:500]}", flush=True)
                return None
            return output_path
        except Exception as e:
            print(f"[meeting] mux exception: {e}", flush=True)
            return None

    @property
    def video_tmp_path(self) -> Path | None:
        return self._video_tmp_path

    def split_audio_chunks(self, audio_path: Path, chunk_minutes: int = 10) -> list[Path]:
        """Split a WAV file into chunks of chunk_minutes each, for Whisper (25MB limit).

        Returns list of chunk file paths in a temp dir.
        """
        data, sr = sf.read(str(audio_path))
        chunk_samples = chunk_minutes * 60 * sr
        total_samples = len(data)
        if total_samples <= chunk_samples:
            return [audio_path]

        tmp_dir = Path(tempfile.mkdtemp(prefix="ghost_chunks_"))
        chunks = []
        for i, start in enumerate(range(0, total_samples, chunk_samples)):
            end = min(start + chunk_samples, total_samples)
            chunk = data[start:end]
            chunk_path = tmp_dir / f"chunk_{i:03d}.wav"
            sf.write(str(chunk_path), chunk, sr, subtype="PCM_16")
            chunks.append(chunk_path)
        return chunks


def format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
