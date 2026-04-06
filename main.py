#!/usr/bin/env python3
"""
finger-snap entry point (macOS).

**Default command (no ``hand-test`` prefix):** double finger-snap listener — mic,
optional ``--require-hand`` webcam gate (MediaPipe presence), optional ``--hand-gesture``
**palm swipe up / down** (big motion, sensitive defaults) → **Mission Control** via ⌃↑ on the **same** thread
(swipe up when we assume MC is off; swipe down when we assume it is on — state follows our own actions).
Confirmed snaps print to stdout (no macOS banners). See ``MainListen``.

**Hand test:** ``python main.py hand-test`` — webcam hand echo to stdout
(formerly ``HandRaiseNotifyTest.py``). Same ``pip install mediapipe opencv-python-headless``
for camera features; model caches under ``.cache/``.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Literal, Optional, Sequence, Tuple

import numpy as np
import sounddevice as sd

HandLandmarkerModelUrl = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)

FingersnapLogFileName = "fingersnap.log"


def FingersnapAtomicWriteLogLines(LogPath: Path, Lines: Sequence[str]) -> None:
    Content = "\n".join(Lines)
    if Content:
        Content += "\n"
    Tmp = LogPath.with_name(LogPath.name + ".tmp")
    try:
        Tmp.write_text(Content, encoding="utf-8", errors="replace")
        os.replace(Tmp, LogPath)
    except OSError:
        try:
            Tmp.unlink(missing_ok=True)
        except OSError:
            pass


def MaybeInstallFingersnapPipeLog() -> None:
    """
    When FINGERSNAP_CAP_LOG is truthy, capture stdout/stderr (fd 1 and 2) through a pipe
    and keep at most FINGERSNAP_LOG_MAX_LINES (default 100) complete lines in LogPath (FIFO).
    start.sh sets this for background supervise runs; hand-test and direct runs are unchanged.
    """
    Val = (os.environ.get("FINGERSNAP_CAP_LOG") or "").strip().lower()
    if Val not in ("1", "true", "yes", "on"):
        return
    try:
        Lim = int(os.environ.get("FINGERSNAP_LOG_MAX_LINES", "100"))
    except ValueError:
        Lim = 100
    if Lim < 1:
        Lim = 100
    LogPath = Path(__file__).resolve().parent / FingersnapLogFileName
    Lines: Deque[str] = deque(maxlen=Lim)
    if LogPath.is_file():
        try:
            Text = LogPath.read_text(encoding="utf-8", errors="replace")
            for L in Text.splitlines():
                Lines.append(L)
        except OSError:
            pass
    if Lines:
        FingersnapAtomicWriteLogLines(LogPath, Lines)
    ReadFd, WriteFd = os.pipe()
    Lock = threading.Lock()

    def RunReader() -> None:
        Buf = b""
        try:
            while True:
                Chunk = os.read(ReadFd, 65536)
                if not Chunk:
                    break
                Buf += Chunk
                while True:
                    I = Buf.find(b"\n")
                    if I < 0:
                        break
                    Line = Buf[:I].decode("utf-8", errors="replace")
                    Buf = Buf[I + 1 :]
                    with Lock:
                        Lines.append(Line)
                        FingersnapAtomicWriteLogLines(LogPath, Lines)
            if Buf:
                Line = Buf.decode("utf-8", errors="replace")
                if Line:
                    with Lock:
                        Lines.append(Line)
                        FingersnapAtomicWriteLogLines(LogPath, Lines)
        finally:
            try:
                os.close(ReadFd)
            except OSError:
                pass

    threading.Thread(target=RunReader, name="FingersnapLogReader", daemon=True).start()
    try:
        os.dup2(WriteFd, 1)
        os.dup2(WriteFd, 2)
    except OSError:
        try:
            os.close(ReadFd)
        except OSError:
            pass
        try:
            os.close(WriteFd)
        except OSError:
            pass
        return
    try:
        os.close(WriteFd)
    except OSError:
        pass
    sys.stdout = os.fdopen(1, "w", buffering=1, encoding="utf-8", errors="replace")
    sys.stderr = os.fdopen(2, "w", buffering=1, encoding="utf-8", errors="replace")


def EnsureHandLandmarkerModel(CacheDir: Path) -> Optional[Path]:
    try:
        CacheDir.mkdir(parents=True, exist_ok=True)
    except OSError as Exc:
        print(f"Could not create {CacheDir}: {Exc}", file=sys.stderr)
        return None
    Dest = CacheDir / "hand_landmarker.task"
    MinBytes = 1_400_000
    if Dest.is_file() and Dest.stat().st_size >= MinBytes:
        return Dest
    print("Downloading hand landmarker model (~4 MB, one-time)…", file=sys.stderr)
    try:
        urllib.request.urlretrieve(HandLandmarkerModelUrl, Dest)
    except (urllib.error.URLError, OSError) as Exc:
        print(f"Download failed: {Exc}", file=sys.stderr)
        try:
            Dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if not Dest.is_file() or Dest.stat().st_size < MinBytes:
        return None
    return Dest


def PalmCenterY(Lm) -> float:
    """Normalized vertical reference: wrist (0) + middle MCP (9); y grows downward."""
    return 0.5 * (float(Lm[0].y) + float(Lm[9].y))


def MacOsAccessibilityTrusted() -> bool:
    try:
        Lib = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        Fn = Lib.AXIsProcessTrusted
        Fn.argtypes = []
        Fn.restype = ctypes.c_bool
        return bool(Fn())
    except OSError:
        return True


def SendMissionControlViaAppleScript() -> None:
    Script = 'tell application "System Events" to key code 126 using control down'
    try:
        R = subprocess.run(
            ["osascript", "-e", Script],
            check=False,
            capture_output=True,
            text=True,
        )
        if R.returncode != 0:
            Msg = (R.stderr or R.stdout or "").strip()
            print(
                f"osascript Mission Control (⌃↑) failed ({R.returncode}): {Msg}",
                file=sys.stderr,
            )
    except OSError as Exc:
        print(f"osascript failed: {Exc}", file=sys.stderr)


@dataclass
class HandGestureMissionConfig:
    HistoryFrames: int
    MinDeltaY: float
    MinDeltaYDown: float
    MinSpeed: float
    CooldownSec: float
    HandSide: str


class HandMissionGestureRuntime:
    """
    **Swipe up** (palm center moves up → y decreases): send ⌃↑ if we assume Mission Control is off.
    **Swipe down**: send ⌃↑ if we assume it is on. ⌃↑ is always the same key; we track assumed state
    so up/down map to open/close. If you toggle MC with the keyboard, state may desync until you
    swipe the direction that matches reality once.
    """

    def __init__(self, Config: HandGestureMissionConfig, DryRun: bool) -> None:
        self.Config = Config
        self.DryRun = DryRun
        self.LastFireT = 0.0
        self.McBelievedOpen = False
        MaxLen = max(4, Config.HistoryFrames)
        self._History: Deque[Tuple[float, float]] = deque(maxlen=MaxLen)

    def _ChooseLandmarkIndex(self, Result) -> Optional[int]:
        Cfg = self.Config
        N = len(Result.hand_landmarks)
        if N <= 0:
            return None
        if Cfg.HandSide == "any":
            return 0
        Want = Cfg.HandSide
        for I, Cats in enumerate(Result.handedness):
            if not Cats:
                continue
            Name = Cats[0].category_name
            if Name and Name.lower() == Want:
                return I
        return 0

    def ProcessLandmarkerResult(self, Result, Now: float) -> None:
        Cfg = self.Config
        ChosenIdx = self._ChooseLandmarkIndex(Result)
        if ChosenIdx is None:
            self._History.clear()
            return
        Lm = Result.hand_landmarks[ChosenIdx]
        self._History.append((Now, PalmCenterY(Lm)))

        if len(self._History) < 4:
            return
        if (Now - self.LastFireT) < Cfg.CooldownSec:
            return
        T0, Y0 = self._History[0]
        T1, Y1 = self._History[-1]
        Dt = T1 - T0
        if Dt <= 1e-3:
            return
        DeltaUp = Y0 - Y1
        DeltaDown = Y1 - Y0
        SpeedUp = DeltaUp / Dt
        SpeedDown = DeltaDown / Dt

        if (
            (not self.McBelievedOpen)
            and DeltaUp >= Cfg.MinDeltaY
            and SpeedUp >= Cfg.MinSpeed
        ):
            if not self.DryRun:
                SendMissionControlViaAppleScript()
            self.McBelievedOpen = True
            self.LastFireT = Now
            self._History.clear()
        elif (
            self.McBelievedOpen
            and DeltaDown >= Cfg.MinDeltaYDown
            and SpeedDown >= Cfg.MinSpeed
        ):
            if not self.DryRun:
                SendMissionControlViaAppleScript()
            self.McBelievedOpen = False
            self.LastFireT = Now
            self._History.clear()


def EchoHandEvent(Title: str, Body: str) -> None:
    Ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{Ts}] {Title}: {Body}", flush=True)


def FrameHandPresenceState(NumHands: int) -> Literal["clear", "raised"]:
    return "raised" if NumHands > 0 else "clear"


def FrameHandRaisedState(
    NumHands: int,
    MinWristY: Optional[float],
    RaiseY: float,
    ClearY: float,
) -> Literal["clear", "raised", "neutral"]:
    if NumHands == 0 or MinWristY is None:
        return "clear"
    if MinWristY < RaiseY:
        return "raised"
    if MinWristY > ClearY:
        return "clear"
    return "neutral"


@dataclass
class ListenerConfig:
    SampleRate: int = 16_000
    BlockSize: int = 512
    RelativeMultiplier: float = 10.0
    AbsoluteThreshold: float = 0.015
    BaselineAlpha: float = 0.995
    CooldownSeconds: float = 0.12
    DoubleWindowMinSeconds: float = 0.2
    DoubleWindowMaxSeconds: float = 2.5
    ThirdSnapRejectSeconds: float = 0.38
    ListenCooldownAfterTriggerSeconds: float = 1.0
    StartupSoundFilename: str = "assets/audio/startupsong.wav"
    ChromeAppName: str = "Google Chrome"
    VsCodeAppName: str = "Visual Studio Code"
    HighFreqCutoffHz: float = 3_000.0
    MinHighFreqEnergyRatio: float = 0.34
    LowMidBandLowHz: float = 100.0
    LowMidBandHighHz: float = 2_400.0
    HighBandLowHz: float = 3_400.0
    MinHighToLowMidPowerRatio: float = 1.4
    MinCrestFactor: float = 3.35
    SpectralPreemphasis: float = 0.94
    ClapBoomBandLowHz: float = 60.0
    ClapBoomBandHighHz: float = 420.0
    MaxClapBoomHardRejectRatio: float = 0.34
    ClapBoomSoftThreshold: float = 0.16
    MinVeryHighFreqWhenBoomyPresent: float = 0.082
    AntiClapVeryHighCutoffHz: float = 4_200.0
    MinVeryHighFreqEnergyRatio: float = 0.062


@dataclass
class SpectralMetrics:
    Peak: float
    HfRatio: float
    HighToLowMidRatio: float
    CrestFactor: float
    VeryHighFreqRatio: float
    ClapBoomRatio: float


def ComputeSpectralMetrics(Block: np.ndarray, Config: ListenerConfig) -> SpectralMetrics:
    """One FFT; contrasts snap-like brightness/impulsiveness vs keyboard thumps."""
    X = np.asarray(Block, dtype=np.float64).ravel()
    N = int(X.size)
    Peak = float(np.max(np.abs(X)))
    Rms = float(np.sqrt(np.mean(np.square(X)))) + 1e-9
    CrestFactor = Peak / Rms
    if N < 32:
        return SpectralMetrics(Peak, 0.0, 0.0, CrestFactor, 1.0, 0.0)
    Window = np.hanning(N)
    A = Config.SpectralPreemphasis
    if N >= 2 and A > 0.0:
        Diff = np.empty_like(X)
        Diff[0] = X[0]
        Diff[1:] = X[1:] - A * X[:-1]
        SpectralX = Diff
    else:
        SpectralX = X
    Xw = SpectralX * Window
    Power = np.square(np.abs(np.fft.rfft(Xw))).astype(np.float64)
    Freqs = np.fft.rfftfreq(N, d=1.0 / float(Config.SampleRate))
    Total = float(np.sum(Power)) + 1e-18
    HfRatio = float(np.sum(Power[Freqs >= Config.HighFreqCutoffHz])) / Total
    LowMid = (
        float(
            np.sum(
                Power[
                    (Freqs >= Config.LowMidBandLowHz)
                    & (Freqs <= Config.LowMidBandHighHz)
                ]
            )
        )
        + 1e-18
    )
    HighBand = float(np.sum(Power[Freqs >= Config.HighBandLowHz]))
    HighToLowMidRatio = HighBand / LowMid
    if Config.AntiClapVeryHighCutoffHz > 0.0:
        Vhf = float(
            np.sum(Power[Freqs >= Config.AntiClapVeryHighCutoffHz])) / Total
    else:
        Vhf = 1.0
    Boom = float(
        np.sum(
            Power[
                (Freqs >= Config.ClapBoomBandLowHz)
                & (Freqs <= Config.ClapBoomBandHighHz)
            ]
        )
    ) / Total
    return SpectralMetrics(Peak, HfRatio, HighToLowMidRatio, CrestFactor, Vhf, Boom)


def IsSnapLikeSpectral(Metrics: SpectralMetrics, Config: ListenerConfig) -> bool:
    if Config.MaxClapBoomHardRejectRatio > 0.0:
        if Metrics.ClapBoomRatio > Config.MaxClapBoomHardRejectRatio:
            return False
    if Config.AntiClapVeryHighCutoffHz > 0.0:
        VhfNeed = Config.MinVeryHighFreqEnergyRatio
        if Config.ClapBoomSoftThreshold > 0.0:
            if Metrics.ClapBoomRatio > Config.ClapBoomSoftThreshold:
                VhfNeed = max(VhfNeed, Config.MinVeryHighFreqWhenBoomyPresent)
        if Metrics.VeryHighFreqRatio < VhfNeed:
            return False
    HfOk = Metrics.HfRatio >= Config.MinHighFreqEnergyRatio
    HiLoOk = Metrics.HighToLowMidRatio >= Config.MinHighToLowMidPowerRatio
    CrestOk = Metrics.CrestFactor >= Config.MinCrestFactor
    if HfOk and HiLoOk and CrestOk:
        return True
    if not HiLoOk:
        return False
    SoftCrest = Metrics.CrestFactor >= Config.MinCrestFactor * 0.82
    SoftHf = Metrics.HfRatio >= Config.MinHighFreqEnergyRatio * 0.88
    if HfOk and SoftCrest:
        return True
    if CrestOk and SoftHf:
        return True
    return False


class DoubleSnapDetector:
    def __init__(
        self,
        Config: ListenerConfig,
        HandPresentFn: Optional[Callable[[], bool]] = None,
        OnHandGateRejected: Optional[Callable[[], None]] = None,
    ) -> None:
        self._Config = Config
        self._HandPresentFn = HandPresentFn
        self._OnHandGateRejected = OnHandGateRejected
        self._Baseline: float = 0.001
        self._CooldownUntil: float = 0.0
        self._PostTriggerCooldownUntil: float = 0.0
        self._EpisodeState: str = "idle"
        self._EpisodeT0: float = 0.0
        self._PendingConfirmUntil: float = 0.0

    def ProcessBlock(self, Block: np.ndarray, Now: float) -> bool:
        """Returns True only when exactly two snaps are confirmed (a third snap cancels)."""
        Metrics = ComputeSpectralMetrics(Block, self._Config)
        Peak = Metrics.Peak
        Threshold = max(
            self._Config.AbsoluteThreshold,
            self._Baseline * self._Config.RelativeMultiplier,
        )
        SnapSpectralOk = IsSnapLikeSpectral(Metrics, self._Config)
        LoudButNotSnapLike = Peak >= Threshold and not SnapSpectralOk
        PeakForBaseline = self._Baseline if LoudButNotSnapLike else Peak
        self._Baseline = (
            self._Config.BaselineAlpha * self._Baseline
            + (1.0 - self._Config.BaselineAlpha) * PeakForBaseline
        )

        IsOnset = Peak >= Threshold and SnapSpectralOk
        InPostTriggerCooldown = Now < self._PostTriggerCooldownUntil
        GestureOnset = IsOnset and not InPostTriggerCooldown

        if self._EpisodeState == "pending":
            if GestureOnset:
                self._ResetEpisode()
                self._CooldownUntil = Now + self._Config.CooldownSeconds
                return False
            if Now >= self._PendingConfirmUntil:
                if self._HandPresentFn is not None and not self._HandPresentFn():
                    self._ResetEpisode()
                    self._CooldownUntil = Now + self._Config.CooldownSeconds
                    if self._OnHandGateRejected is not None:
                        self._OnHandGateRejected()
                    return False
                self._ResetEpisode()
                self._PostTriggerCooldownUntil = Now + \
                    self._Config.ListenCooldownAfterTriggerSeconds
                self._CooldownUntil = Now + self._Config.CooldownSeconds
                return True

        if InPostTriggerCooldown:
            return False

        if self._EpisodeState == "one" and not GestureOnset:
            if Now - self._EpisodeT0 > self._Config.DoubleWindowMaxSeconds:
                self._EpisodeState = "idle"

        if not GestureOnset:
            return False

        if Now < self._CooldownUntil:
            return False

        self._CooldownUntil = Now + self._Config.CooldownSeconds

        if self._EpisodeState == "idle":
            self._EpisodeState = "one"
            self._EpisodeT0 = Now
            return False

        if self._EpisodeState == "one":
            Delta = Now - self._EpisodeT0
            if Delta < self._Config.DoubleWindowMinSeconds:
                return False
            if Delta > self._Config.DoubleWindowMaxSeconds:
                self._EpisodeT0 = Now
                return False
            self._EpisodeState = "pending"
            self._PendingConfirmUntil = Now + self._Config.ThirdSnapRejectSeconds
            return False

        return False

    def _ResetEpisode(self) -> None:
        self._EpisodeState = "idle"


def OpenChromeTab(Url: str, ChromeAppName: str) -> None:
    """Opens a new tab in Google Chrome (macOS ``open``)."""
    try:
        Result = subprocess.run(
            ["open", "-a", ChromeAppName, Url],
            check=False,
            capture_output=True,
            text=True,
        )
        if Result.returncode != 0:
            print(
                f"Could not open Chrome ({Result.returncode}): {Result.stderr.strip()}",
                file=sys.stderr,
            )
    except OSError as Exc:
        print(f"Could not run open for Chrome: {Exc}", file=sys.stderr)


def OpenVisualStudioCode(WorkspacePath: Path, AppName: str) -> None:
    """Opens a folder in VS Code (macOS ``open -a``)."""
    try:
        Result = subprocess.run(
            ["open", "-a", AppName, str(WorkspacePath.resolve())],
            check=False,
            capture_output=True,
            text=True,
        )
        if Result.returncode != 0:
            print(
                f"Could not open VS Code ({Result.returncode}): {Result.stderr.strip()}",
                file=sys.stderr,
            )
    except OSError as Exc:
        print(f"Could not run open for VS Code: {Exc}", file=sys.stderr)


def PlayStartupSound(WavPath: Path) -> None:
    """Play a WAV via macOS ``afplay`` without blocking the audio input callback."""
    if not WavPath.is_file():
        if not getattr(PlayStartupSound, "_MissingLogged", False):
            setattr(PlayStartupSound, "_MissingLogged", True)
            print(
                f"Startup sound missing (skipped): {WavPath}", file=sys.stderr)
        return
    try:
        subprocess.Popen(
            ["afplay", str(WavPath)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as Exc:
        print(f"Could not play startup sound: {Exc}", file=sys.stderr)


class HandPresenceTracker:
    """Thread-safe latest hand-in-frame flag (MediaPipe presence: any hand)."""

    def __init__(self) -> None:
        self._Lock = threading.Lock()
        self._Present = False

    def Set(self, Present: bool) -> None:
        with self._Lock:
            self._Present = Present

    def IsPresent(self) -> bool:
        with self._Lock:
            return self._Present


def StartHandPresencePipeline(
    ScriptDir: Path,
    CameraIndex: int,
    MissionRuntime: Optional[HandMissionGestureRuntime] = None,
) -> tuple[HandPresenceTracker, threading.Event, threading.Thread]:
    """Opens the camera and runs MediaPipe Hand Landmarker in a background thread."""
    try:
        import cv2
        from mediapipe.tasks.python.core import base_options as MpBaseOptions
        from mediapipe.tasks.python.vision import hand_landmarker as MpHandLandmarker
        from mediapipe.tasks.python.vision.core import image as MpImage
        from mediapipe.tasks.python.vision.core import vision_task_running_mode as MpVisionMode
    except ImportError as Exc:
        print(
            "Install: pip install mediapipe opencv-python-headless "
            f"({Exc})",
            file=sys.stderr,
        )
        raise SystemExit(1) from Exc

    ModelPath = EnsureHandLandmarkerModel(ScriptDir / ".cache")
    if ModelPath is None:
        raise SystemExit(1)

    Options = MpHandLandmarker.HandLandmarkerOptions(
        base_options=MpBaseOptions.BaseOptions(
            model_asset_path=str(ModelPath)),
        running_mode=MpVisionMode.VisionTaskRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.55,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    Landmarker = MpHandLandmarker.HandLandmarker.create_from_options(Options)
    Cap = cv2.VideoCapture(CameraIndex)
    if not Cap.isOpened():
        print(f"Could not open camera index {CameraIndex}.", file=sys.stderr)
        Landmarker.close()
        raise SystemExit(1)
    Cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    Cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    Tracker = HandPresenceTracker()
    StopEvent = threading.Event()

    def RunHandLoop() -> None:
        VideoTsMs = 0
        try:
            while not StopEvent.is_set():
                Ok, Bgr = Cap.read()
                if not Ok or Bgr is None:
                    time.sleep(0.02)
                    continue
                Rgb = cv2.cvtColor(Bgr, cv2.COLOR_BGR2RGB)
                Rgb = np.ascontiguousarray(Rgb)
                VideoTsMs += 33
                MpFrame = MpImage.Image(MpImage.ImageFormat.SRGB, Rgb)
                Result = Landmarker.detect_for_video(MpFrame, VideoTsMs)
                NumHands = len(Result.hand_landmarks)
                Tracker.Set(NumHands > 0)
                if MissionRuntime is not None:
                    MissionRuntime.ProcessLandmarkerResult(
                        Result, time.monotonic()
                    )
        finally:
            Cap.release()
            Landmarker.close()

    Thread = threading.Thread(
        target=RunHandLoop, name="HandPresence", daemon=False)
    Thread.start()
    return Tracker, StopEvent, Thread


def MainListen() -> None:
    Parser = argparse.ArgumentParser(
        description="Detect double finger snaps from the mic.")
    Parser.add_argument(
        "--chrome-url",
        default=None,
        metavar="URL",
        help=(
            "URL to open in Chrome on double snap "
            "(default: file URL of index.html next to main.py)"
        ),
    )
    Parser.add_argument(
        "--no-chrome",
        action="store_true",
        help="Do not open Google Chrome when a double snap is detected.",
    )
    Parser.add_argument(
        "--no-vscode",
        action="store_true",
        help="Do not open Visual Studio Code for this repo folder on double snap.",
    )
    Parser.add_argument(
        "--no-startup-sound",
        action="store_true",
        help="Do not play startupsong.wav when a double snap is confirmed.",
    )
    Parser.add_argument(
        "--startup-wav",
        default=None,
        metavar="PATH",
        help="WAV file to play on double snap (default: assets/audio/startupsong.wav under repo root).",
    )
    Parser.add_argument(
        "--disable-very-high-snap-gate",
        action="store_true",
        help=(
            "Disable spectral anti-clap (very-high-frequency + low-frequency boom checks; "
            "see ListenerConfig)."
        ),
    )
    Parser.add_argument(
        "--require-hand",
        action="store_true",
        help=(
            "After two snaps, a hand must be visible on the webcam (MediaPipe presence). "
            "Install mediapipe and opencv-python-headless."
        ),
    )
    Parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        metavar="N",
        help="Camera index for --require-hand and/or --hand-gesture (default: 0).",
    )
    Parser.add_argument(
        "--hand-gesture",
        action="store_true",
        help=(
            "Palm swipe up / swipe down → Mission Control (⌃↑), same camera thread as "
            "--require-hand. Sensitive defaults; see --gesture-*."
        ),
    )
    Parser.add_argument(
        "--gesture-history-frames",
        type=int,
        default=10,
        help="Frames in palm-Y sliding window (min 4). Shorter = less distance to register. Default: 10.",
    )
    Parser.add_argument(
        "--gesture-min-delta-y",
        type=float,
        default=0.05,
        metavar="0-1",
        help="Min upward palm motion (y_old − y_new) for swipe up. Default: 0.05 (easy).",
    )
    Parser.add_argument(
        "--gesture-min-delta-y-down",
        type=float,
        default=0.05,
        metavar="0-1",
        help="Min downward motion for swipe down. Default: 0.05.",
    )
    Parser.add_argument(
        "--gesture-min-speed",
        type=float,
        default=0.15,
        metavar="PER_SEC",
        help="Min |Δy|/Δt (norm coords/sec). Default: 0.15 (easy).",
    )
    Parser.add_argument(
        "--gesture-cooldown",
        type=float,
        default=0.85,
        metavar="SEC",
        help="Min seconds between swipe actions. Default: 0.85.",
    )
    Parser.add_argument(
        "--gesture-hand",
        choices=("any", "left", "right"),
        default="any",
        help="Which hand to track (MediaPipe handedness). Default: any.",
    )
    Parser.add_argument(
        "--gesture-dry-run",
        action="store_true",
        help="Detect swipes but do not send ⌃↑.",
    )
    Parser.add_argument(
        "--supervise",
        action="store_true",
        help=(
            "After each listen session ends, start another (delay from FINGERSNAP_RESTART_DELAY). "
            "Use under launchd so the venv Python process owns the camera (green menu bar indicator)."
        ),
    )
    Args = Parser.parse_args()
    if Args.hand_gesture:
        if Args.gesture_history_frames < 1:
            print("--gesture-history-frames must be >= 1.", file=sys.stderr)
            sys.exit(1)
        if Args.gesture_min_delta_y <= 0 or Args.gesture_min_delta_y_down <= 0:
            print("--gesture-min-delta-y and --gesture-min-delta-y-down must be > 0.", file=sys.stderr)
            sys.exit(1)
        if Args.gesture_min_speed <= 0:
            print("--gesture-min-speed must be > 0.", file=sys.stderr)
            sys.exit(1)

    GestureLabel = ""
    if Args.hand_gesture:
        GestureLabel = (
            "swipe up/down → MC (dry-run)"
            if Args.gesture_dry_run
            else "swipe up/down → Mission Control (⌃↑)"
        )
        if not Args.gesture_dry_run and not MacOsAccessibilityTrusted():
            print(
                "Accessibility is OFF — macOS will ignore ⌃↑ from System Events. "
                "System Settings → Privacy & Security → Accessibility → enable the "
                "app running Python. Remove and re-add the entry if needed.",
                file=sys.stderr,
            )

    Config = ListenerConfig()
    if Args.disable_very_high_snap_gate:
        Config.AntiClapVeryHighCutoffHz = 0.0
        Config.MaxClapBoomHardRejectRatio = 0.0
        Config.ClapBoomSoftThreshold = 0.0
    ScriptDir = Path(__file__).resolve().parent
    DefaultIndexFileUrl = (ScriptDir / "index.html").as_uri()
    ChromeUrl = Args.chrome_url or DefaultIndexFileUrl
    StartupWav = (
        Path(Args.startup_wav).expanduser().resolve()
        if Args.startup_wav
        else ScriptDir / Config.StartupSoundFilename
    )
    RestartDelaySec = float(os.environ.get("FINGERSNAP_RESTART_DELAY", "5"))

    def RunOneListenSession() -> None:
        HandStop: Optional[threading.Event] = None
        HandThread: Optional[threading.Thread] = None
        HandPresentFn: Optional[Callable[[], bool]] = None
        OnHandGateRejected: Optional[Callable[[], None]] = None
        NeedCamera = Args.require_hand or Args.hand_gesture
        if NeedCamera:
            MissionRt: Optional[HandMissionGestureRuntime] = None
            if Args.hand_gesture:
                Gcfg = HandGestureMissionConfig(
                    HistoryFrames=Args.gesture_history_frames,
                    MinDeltaY=Args.gesture_min_delta_y,
                    MinDeltaYDown=Args.gesture_min_delta_y_down,
                    MinSpeed=Args.gesture_min_speed,
                    CooldownSec=Args.gesture_cooldown,
                    HandSide=Args.gesture_hand,
                )
                MissionRt = HandMissionGestureRuntime(
                    Gcfg,
                    Args.gesture_dry_run,
                )
            Tracker, HandStop, HandThread = StartHandPresencePipeline(
                ScriptDir, Args.camera_index, MissionRuntime=MissionRt
            )
            if Args.require_hand:
                HandPresentFn = Tracker.IsPresent

                def OnHandGateRejected() -> None:
                    print(
                        "Double snap ignored: no hand visible in the camera.",
                        file=sys.stderr,
                    )

        Detector = DoubleSnapDetector(
            Config,
            HandPresentFn=HandPresentFn,
            OnHandGateRejected=OnHandGateRejected,
        )

        RestartRequested = threading.Event()
        MaxSustainedAudioErrors = 250
        AudioErrorStreak = [0]

        def Callback(Indata, Frames, TimeInfo, Status) -> None:
            if Status:
                AudioErrorStreak[0] += 1
                print(f"Audio status: {Status}", file=sys.stderr)
                if AudioErrorStreak[0] >= MaxSustainedAudioErrors:
                    print(
                        "Audio input errors sustained; restarting listen session.",
                        file=sys.stderr,
                    )
                    RestartRequested.set()
                    return
            else:
                AudioErrorStreak[0] = 0
            Block = Indata.copy()
            Now = time.perf_counter()
            if not Detector.ProcessBlock(Block, Now):
                return

            def RunDoubleSnapSideEffects() -> None:
                """Must not run in the audio callback: blocking ``open`` calls starve other threads (camera / gestures)."""
                try:
                    if not Args.no_startup_sound:
                        PlayStartupSound(StartupWav)
                    if not Args.no_chrome:
                        OpenChromeTab("https://gmail.com", Config.ChromeAppName)
                        OpenChromeTab("https://chatgpt.com", Config.ChromeAppName)
                        OpenChromeTab("https://x.com", Config.ChromeAppName)
                        OpenChromeTab("https://linkedin.com", Config.ChromeAppName)
                        OpenChromeTab(ChromeUrl, Config.ChromeAppName)
                    if not Args.no_vscode:
                        OpenVisualStudioCode(ScriptDir, Config.VsCodeAppName)
                    print("Double snap detected.", flush=True)
                except Exception as Exc:
                    print(f"Double snap side effects failed: {Exc}", file=sys.stderr)

            threading.Thread(
                target=RunDoubleSnapSideEffects,
                name="DoubleSnapSideEffects",
                daemon=True,
            ).start()

        try:
            with sd.InputStream(
                samplerate=Config.SampleRate,
                blocksize=Config.BlockSize,
                channels=1,
                dtype="float32",
                callback=Callback,
            ):
                Msg = "Listening for double snaps. Ctrl+C to stop."
                if (
                    Config.AntiClapVeryHighCutoffHz > 0.0
                    or Config.MaxClapBoomHardRejectRatio > 0.0
                ):
                    Msg += " (spectral anti-clap: boom + HF.)"
                if Args.require_hand:
                    Msg += " (hand must be visible when the second snap window closes.)"
                if Args.hand_gesture:
                    Msg += f" Hand gesture: {GestureLabel}."
                if Args.supervise:
                    Msg += f" (supervise: {RestartDelaySec}s between session restarts.)"
                print(Msg, file=sys.stderr)
                while not RestartRequested.is_set():
                    time.sleep(0.25)
        finally:
            if HandStop is not None:
                HandStop.set()
            if HandThread is not None:
                HandThread.join(timeout=3.0)

    if Args.supervise:
        print(
            "Supervise: relaunching listen sessions in-process (same Python = camera indicator). "
            "Ctrl+C to stop.",
            file=sys.stderr,
        )
        try:
            while True:
                RunOneListenSession()
                print(
                    f"Listen session ended; pausing {RestartDelaySec}s before restart.",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(RestartDelaySec)
        except KeyboardInterrupt:
            print("Stopped.", file=sys.stderr)
    else:
        try:
            RunOneListenSession()
        except KeyboardInterrupt:
            print("Stopped.", file=sys.stderr)


def MainHandTest() -> None:
    Parser = argparse.ArgumentParser(
        description="Webcam (headless): echo to stdout when a hand is detected."
    )
    Parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        help="OpenCV camera index (default: 0).",
    )
    Parser.add_argument(
        "--mode",
        choices=("presence", "raised"),
        default="presence",
        help="presence: echo when any hand is in frame (default). raised: wrist in upper band only.",
    )
    Parser.add_argument(
        "--raise-y",
        type=float,
        default=0.38,
        metavar="0-1",
        help="[raised mode] Wrist above this normalized y triggers. Default: 0.38.",
    )
    Parser.add_argument(
        "--clear-y",
        type=float,
        default=0.52,
        metavar="0-1",
        help="[raised mode] Re-arm after wrist drops below this. Default: 0.52.",
    )
    Parser.add_argument(
        "--min-interval",
        type=float,
        default=2.5,
        metavar="SEC",
        help="Minimum seconds between echo lines. Default: 2.5.",
    )
    Parser.add_argument(
        "--preview",
        action="store_true",
        help="Show a small camera window; press q to quit.",
    )
    Parser.add_argument(
        "--notification-title",
        default="Hand",
        help="Echo line title prefix.",
    )
    Parser.add_argument(
        "--notification-body",
        default="Detected in camera",
        help="Echo line body text.",
    )
    Args = Parser.parse_args()
    if Args.mode == "raised" and Args.clear_y <= Args.raise_y:
        print(
            "--clear-y must be greater than --raise-y (y grows downward).", file=sys.stderr)
        sys.exit(2)

    try:
        import cv2
        from mediapipe.tasks.python.core import base_options as MpBaseOptions
        from mediapipe.tasks.python.vision import hand_landmarker as MpHandLandmarker
        from mediapipe.tasks.python.vision.core import image as MpImage
        from mediapipe.tasks.python.vision.core import vision_task_running_mode as MpVisionMode
    except ImportError:
        print("Install: pip install mediapipe opencv-python-headless", file=sys.stderr)
        sys.exit(1)

    ScriptDir = Path(__file__).resolve().parent
    ModelPath = EnsureHandLandmarkerModel(ScriptDir / ".cache")
    if ModelPath is None:
        sys.exit(1)

    Options = MpHandLandmarker.HandLandmarkerOptions(
        base_options=MpBaseOptions.BaseOptions(
            model_asset_path=str(ModelPath)),
        running_mode=MpVisionMode.VisionTaskRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.55,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    Landmarker = MpHandLandmarker.HandLandmarker.create_from_options(Options)
    Cap = cv2.VideoCapture(Args.camera_index)
    if not Cap.isOpened():
        print(
            f"Could not open camera index {Args.camera_index}.", file=sys.stderr)
        Landmarker.close()
        sys.exit(1)
    Cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    Cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    Armed = True
    LastNotifyT = 0.0
    VideoTsMs = 0
    if Args.mode == "presence":
        Hint = (
            "Hand in frame → echo line on stdout (once). Remove hand to re-arm. "
            "Headless; no window. Ctrl+C to stop."
        )
    else:
        Hint = (
            "Raise wrist into the **upper** band to echo; lower past clear line to re-arm. "
            "Ctrl+C to stop."
        )
    if Args.preview:
        Hint += " Press q in the preview window to quit."
    print(Hint, file=sys.stderr)

    try:
        while True:
            Ok, Bgr = Cap.read()
            if not Ok or Bgr is None:
                time.sleep(0.02)
                continue
            H, W = Bgr.shape[:2]
            Rgb = cv2.cvtColor(Bgr, cv2.COLOR_BGR2RGB)
            Rgb = np.ascontiguousarray(Rgb)
            VideoTsMs += 33
            MpFrame = MpImage.Image(MpImage.ImageFormat.SRGB, Rgb)
            Result = Landmarker.detect_for_video(MpFrame, VideoTsMs)
            NumHands = len(Result.hand_landmarks)
            MinWristY: Optional[float] = None
            if NumHands > 0:
                MinWristY = min(float(h[0].y) for h in Result.hand_landmarks)

            if Args.mode == "presence":
                State = FrameHandPresenceState(NumHands)
            else:
                State = FrameHandRaisedState(
                    NumHands, MinWristY, Args.raise_y, Args.clear_y
                )
            if State == "clear":
                Armed = True
            elif State == "raised" and Armed:
                Now = time.perf_counter()
                if Now - LastNotifyT >= Args.min_interval:
                    EchoHandEvent(Args.notification_title,
                                  Args.notification_body)
                    LastNotifyT = Now
                    Armed = False

            if Args.preview:
                LineRaise = int(Args.raise_y * H)
                LineClear = int(Args.clear_y * H)
                cv2.line(Bgr, (0, LineRaise), (W, LineRaise), (0, 255, 255), 1)
                cv2.line(Bgr, (0, LineClear), (W, LineClear), (0, 165, 255), 1)
                Label = f"hands={NumHands}"
                if MinWristY is not None:
                    Label += f" minY={MinWristY:.2f} state={State}"
                cv2.putText(
                    Bgr,
                    Label,
                    (8, 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 0),
                    1,
                    cv2.LINE_AA,
                )
                cv2.imshow("Hand raise test (q to quit)", Bgr)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        Cap.release()
        Landmarker.close()
        if Args.preview:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass


def Main() -> None:
    MaybeInstallFingersnapPipeLog()
    Rest = sys.argv[1:]
    if Rest and Rest[0] == "hand-test":
        sys.argv = [sys.argv[0]] + Rest[1:]
        MainHandTest()
        return
    MainListen()


if __name__ == "__main__":
    Main()
