import subprocess
import tempfile
import threading
import time
from pathlib import Path

import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
import soundcard as sc
import soundfile as sf
from PIL import Image

from src.capture_pkg.screenshot import capture_fullscreen, capture_region
from src.capture_pkg.scroll import capture_monitor

SAMPLE_RATE = 48000  # full-range speech/music; Whisper auto-downsamples when transcribing
CHANNELS = 1
BLOCK_SIZE = 2048

VIDEO_FPS = 10  # smooth enough for shared screens without blowing up file size
VIDEO_MAX_WIDTH = 1920  # keep full-HD fidelity; meetings are often the only thing on screen
VIDEO_QUALITY = 9  # imageio quality 0–10; 9 ≈ crf 10 for libx264 (near-visually-lossless)


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
        # Latest frame from the video loop, surfaced to `capture_now()`. We
        # cache instead of grabbing on demand so live Q&A doesn't race the
        # video thread on PrintWindow / GDI contexts — concurrent
        # PrintWindow calls against the same hwnd can deadlock the caller.
        self._last_frame: Image.Image | None = None

    def is_running(self) -> bool:
        return self._running

    def elapsed(self) -> float:
        if not self._start_time:
            return 0.0
        end = self._stop_time or time.time()
        return end - self._start_time

    def start(self, monitor: dict | None = None, window_hwnd: int = 0,
              fallback_monitor: dict | None = None,
              window_title_patterns: list[str] | None = None):
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
        # Title substrings the target window's title should continue to
        # match. When the user picks a browser tab running a meeting and
        # then switches to a non-meeting tab, Chrome keeps the same HWND
        # but now renders different content. Without this check the
        # recording would follow them to Gmail/etc. Empty list = no filter
        # (e.g. user picked a native app window where tab switching doesn't apply).
        self._window_title_patterns = [p.lower() for p in (window_title_patterns or [])]

        self._video_tmp_path = Path(tempfile.gettempdir()) / f"ghost_video_{int(time.time())}.mp4"
        self._video_writer = None
        self._video_size = None
        self._last_frame = None

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
        # Per-thread timeouts: audio/screenshot loops exit within their
        # sleep interval (≤500ms), so 1.5s is generous. The video loop's
        # finally block flushes the MP4 buffer which can take 1–3s for
        # multi-hour meetings, so we give it 3.0s. Total worst case:
        # 4.5s + 3s = 7.5s, down from 20s (4×5s) pre-1.1.26. Threads
        # are daemon so even if the 3s video budget expires, its
        # finally still finishes in the background; the explicit
        # writer.close() below is a safety net for the common case
        # where video_loop exited before the finally ran.
        for i, t in enumerate(self._threads):
            t.join(timeout=3.0 if i == 3 else 1.5)
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

    def _capture_window_content(self, hwnd: int) -> Image.Image | None:
        """Capture the *content* of a window using PrintWindow, independent of
        z-order — so if the user opens another app on top, the recording still
        shows the meeting, not whatever is in front. PW_RENDERFULLCONTENT
        is required for Chromium/hardware-accelerated apps (Chrome, Edge,
        Teams desktop); without it they render as a black rectangle.

        Returns None on failure; caller falls back to region grab."""
        try:
            import ctypes
            from ctypes import wintypes

            bounds = self._window_visible_rect(hwnd)
            if bounds is None:
                return None
            _, _, w, h = bounds

            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32

            hwnd_w = wintypes.HWND(hwnd)
            hdc_window = user32.GetWindowDC(hwnd_w)
            if not hdc_window:
                return None
            hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
            hbmp = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
            old = gdi32.SelectObject(hdc_mem, hbmp)
            try:
                PW_RENDERFULLCONTENT = 0x00000002
                ok = user32.PrintWindow(hwnd_w, hdc_mem, PW_RENDERFULLCONTENT)
                if not ok:
                    return None

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD),
                    ]

                class BITMAPINFO(ctypes.Structure):
                    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

                bmi = BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = w
                bmi.bmiHeader.biHeight = -h  # top-down
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = 0  # BI_RGB

                buf = ctypes.create_string_buffer(w * h * 4)
                gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
                return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
            finally:
                gdi32.SelectObject(hdc_mem, old)
                gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(hwnd_w, hdc_window)
        except Exception as e:
            print(f"[meeting] printwindow error: {e}", flush=True)
            return None

    def _window_visible_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        """Return (x, y, w, h) of the window's visible frame, excluding the
        invisible DWM shadow margins that `GetWindowRect` includes. Without
        this, a captured window shows a transparent border that bleeds
        through to whatever is behind the window — which on a multi-monitor
        setup often looks like a black strip next to the real content."""
        try:
            import ctypes
            from ctypes import wintypes
            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            rect = wintypes.RECT()
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if hr == 0:
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                if w > 0 and h > 0:
                    return (rect.left, rect.top, w, h)
        except Exception:
            pass
        # Fallback: GetWindowRect (may include invisible shadow border)
        try:
            import win32gui
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2] - r[0], r[3] - r[1]
            if w > 0 and h > 0:
                return (r[0], r[1], w, h)
        except Exception:
            pass
        return None

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
                # Tab-switch guard: if the user picked a meeting tab but the
                # title no longer looks like a meeting (they switched to
                # another tab), return None so the video loop repeats the
                # last valid meeting frame instead of recording Gmail.
                if self._window_title_patterns:
                    cur_title = win32gui.GetWindowText(self._window_hwnd).lower()
                    if not any(p in cur_title for p in self._window_title_patterns):
                        return None
                # Primary: PrintWindow — captures window content even when
                # covered by other apps. Essential for locking capture to
                # the chosen meeting window while the user alt-tabs around.
                img = self._capture_window_content(self._window_hwnd)
                if img is not None:
                    return img
                # Fallback: screen-coordinate grab (older behavior). Only
                # hit when PrintWindow fails outright.
                bounds = self._window_visible_rect(self._window_hwnd)
                if bounds is None:
                    return None
                x, y, w, h = bounds
                return capture_region(x, y, w, h)
            except Exception as e:
                print(f"[meeting] window rect error: {e}", flush=True)
                return None
        if self._monitor:
            return capture_monitor(self._monitor)
        if self._fallback_monitor:
            return capture_monitor(self._fallback_monitor)
        return capture_fullscreen()

    def capture_now(self) -> Image.Image | None:
        """Return the most recent frame from the video loop. Used by live
        Q&A for screen context without re-entering PrintWindow/GDI
        concurrently with the video thread (which deadlocks on some
        Chromium windows)."""
        with self._lock:
            return self._last_frame

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
        """Record video of the target area to an MP4 file."""
        writer = None
        try:
            tgt_desc = (
                f"window hwnd={self._window_hwnd}" if self._window_hwnd
                else (f"monitor={self._monitor}" if self._monitor else "fullscreen")
            )
            print(f"[meeting] video target: {tgt_desc}", flush=True)
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
            src_w, src_h = first.size
            w, h = src_w, src_h
            if w > VIDEO_MAX_WIDTH:
                scale = VIDEO_MAX_WIDTH / w
                w = VIDEO_MAX_WIDTH
                h = int(h * scale)
            if w % 2 != 0:
                w -= 1
            if h % 2 != 0:
                h -= 1
            self._video_size = (w, h)
            print(f"[meeting] capture size {src_w}x{src_h} → encode {w}x{h} @ {VIDEO_FPS}fps q={VIDEO_QUALITY}", flush=True)

            writer = imageio.get_writer(
                str(self._video_tmp_path),
                fps=VIDEO_FPS,
                codec="libx264",
                quality=VIDEO_QUALITY,
                pixelformat="yuv420p",
                macro_block_size=1,
            )
            self._video_writer = writer

            first_scaled = first.resize((w, h), Image.LANCZOS) if first.size != (w, h) else first
            last_frame_arr = np.array(first_scaled)
            with self._lock:
                self._last_frame = first_scaled
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
                            with self._lock:
                                self._last_frame = img
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
