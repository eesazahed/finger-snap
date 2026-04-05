#!/usr/bin/env python3
"""
finger-snap entry point (macOS).

**Default command (no ``hand-test`` prefix):** double finger-snap listener — mic,
optional ``--require-hand`` webcam gate (MediaPipe presence), optional ``--hand-gesture``
fist/open → Mission Control (⌃↑) or synthetic F3 on the **same** camera thread.
Confirmed snaps print to stdout (no macOS banners). See ``MainListen``.

**Hand test:** ``python main.py hand-test`` — webcam hand echo to stdout
(formerly ``HandRaiseNotifyTest.py``). Same ``pip install mediapipe opencv-python-headless``
for camera features; model caches under ``.cache/``.
"""

from __future__ import annotations

import argparse
import ctypes
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Literal, Optional, Tuple

import numpy as np
import sounddevice as sd

HandLandmarkerModelUrl = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)


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


def DistNormLm(Lm, Ia: int, Ib: int) -> float:
    Ax, Ay = float(Lm[Ia].x), float(Lm[Ia].y)
    Bx, By = float(Lm[Ib].x), float(Lm[Ib].y)
    return math.hypot(Ax - Bx, Ay - By)


def IsFingerExtendedLm(Lm, TipIdx: int, PipIdx: int, ExtendedRatio: float) -> bool:
    Wrist = 0
    DTip = DistNormLm(Lm, Wrist, TipIdx)
    DPip = DistNormLm(Lm, Wrist, PipIdx)
    if DPip < 1e-6:
        return DTip > 1e-6
    return DTip > DPip * ExtendedRatio


def CountExtendedFingersLm(Lm, ExtendedRatio: float) -> int:
    Pairs: List[Tuple[int, int]] = [(8, 6), (12, 10), (16, 14), (20, 18)]
    N = 0
    for TipIdx, PipIdx in Pairs:
        if IsFingerExtendedLm(Lm, TipIdx, PipIdx, ExtendedRatio):
            N += 1
    return N


def ClassifyRawPoseLm(
    Lm,
    ExtendedRatio: float,
    OpenMinExtended: int,
    FistMaxExtended: int,
) -> str:
    N = CountExtendedFingersLm(Lm, ExtendedRatio)
    if N >= OpenMinExtended:
        return "open"
    if N <= FistMaxExtended:
        return "fist"
    return "ambiguous"


F3VirtualKeyCode = 99


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


def QuartzF3Available() -> bool:
    try:
        from Quartz import CGEventCreateKeyboardEvent  # noqa: F401
        from Quartz import CGEventPost  # noqa: F401
        from Quartz import kCGHIDEventTap  # noqa: F401
    except ImportError:
        return False
    return True


def CgEventTimestampNow() -> int:
    try:
        Libc = ctypes.CDLL("/usr/lib/libc.dylib")
        Fn = getattr(Libc, "clock_gettime_nsec_np", None)
        if Fn is None:
            return time.monotonic_ns()
        CLOCK_UPTIME_RAW = 8
        Fn.argtypes = [ctypes.c_int]
        Fn.restype = ctypes.c_uint64
        return int(Fn(CLOCK_UPTIME_RAW))
    except (OSError, AttributeError, TypeError):
        return time.monotonic_ns()


def FrontmostApplicationPid() -> Optional[int]:
    try:
        from AppKit import NSWorkspace
    except ImportError:
        return None
    App = NSWorkspace.sharedWorkspace().frontmostApplication()
    if App is None:
        return None
    return int(App.processIdentifier())


def FrontmostApplicationLabel() -> str:
    try:
        from AppKit import NSWorkspace
    except ImportError:
        return "(AppKit unavailable)"
    App = NSWorkspace.sharedWorkspace().frontmostApplication()
    if App is None:
        return "(none)"
    Pid = int(App.processIdentifier())
    Name = App.localizedName() or App.bundleIdentifier() or "unknown"
    return f"{Name} (pid {Pid})"


def SendF3MacOsQuartz(PulseSeconds: float, Target: str) -> None:
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventPostToPid,
            CGEventSetTimestamp,
            CGEventSourceCreate,
            kCGAnnotatedSessionEventTap,
            kCGEventSourceStateHIDSystemState,
            kCGHIDEventTap,
            kCGSessionEventTap,
        )
    except ImportError as Exc:
        print(f"Quartz import failed: {Exc}", file=sys.stderr)
        return
    TapByName = {
        "hid": kCGHIDEventTap,
        "session": kCGSessionEventTap,
        "annotated": kCGAnnotatedSessionEventTap,
    }
    try:
        Src = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)
    except Exception:
        Src = None

    def PostDownUpToTap(TapLoc: int) -> None:
        DownEv = CGEventCreateKeyboardEvent(Src, F3VirtualKeyCode, True)
        if DownEv is None:
            print("Quartz: could not create F3 key-down.", file=sys.stderr)
            return
        CGEventSetTimestamp(DownEv, CgEventTimestampNow())
        CGEventPost(TapLoc, DownEv)
        if PulseSeconds > 0:
            time.sleep(PulseSeconds)
        UpEv = CGEventCreateKeyboardEvent(Src, F3VirtualKeyCode, False)
        if UpEv is None:
            print("Quartz: could not create F3 key-up.", file=sys.stderr)
            return
        CGEventSetTimestamp(UpEv, CgEventTimestampNow())
        CGEventPost(TapLoc, UpEv)

    def PostDownUpToPid(Pid: int) -> None:
        DownEv = CGEventCreateKeyboardEvent(Src, F3VirtualKeyCode, True)
        if DownEv is None:
            print("Quartz: could not create F3 key-down.", file=sys.stderr)
            return
        CGEventSetTimestamp(DownEv, CgEventTimestampNow())
        CGEventPostToPid(Pid, DownEv)
        if PulseSeconds > 0:
            time.sleep(PulseSeconds)
        UpEv = CGEventCreateKeyboardEvent(Src, F3VirtualKeyCode, False)
        if UpEv is None:
            print("Quartz: could not create F3 key-up.", file=sys.stderr)
            return
        CGEventSetTimestamp(UpEv, CgEventTimestampNow())
        CGEventPostToPid(Pid, UpEv)

    try:
        if Target == "frontmost":
            Pid = FrontmostApplicationPid()
            if Pid is None:
                print(
                    "Quartz: no frontmost app; posting F3 to hid tap instead.",
                    file=sys.stderr,
                )
                PostDownUpToTap(kCGHIDEventTap)
                return
            PostDownUpToPid(Pid)
            return
        TapLoc = TapByName.get(Target, kCGHIDEventTap)
        PostDownUpToTap(TapLoc)
    except Exception as Exc:
        print(f"Quartz F3 post failed: {Exc}", file=sys.stderr)


def SendF3MacOsAppleScript() -> None:
    Script = 'tell application "System Events" to key code 99'
    try:
        R = subprocess.run(
            ["osascript", "-e", Script],
            check=False,
            capture_output=True,
            text=True,
        )
        if R.returncode != 0:
            Msg = (R.stderr or R.stdout or "").strip()
            print(f"osascript F3 failed ({R.returncode}): {Msg}", file=sys.stderr)
    except OSError as Exc:
        print(f"osascript failed: {Exc}", file=sys.stderr)


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


def ResolveF3Emitter(
    Backend: str,
    PulseSeconds: float,
    Target: str,
) -> Tuple[Callable[[], None], str]:
    if Backend == "quartz":
        if not QuartzF3Available():
            print(
                "gesture F3 backend quartz requires: pip install pyobjc-framework-Quartz",
                file=sys.stderr,
            )
            sys.exit(1)
        return (
            lambda: SendF3MacOsQuartz(PulseSeconds, Target),
            f"Quartz+F3 ({Target})",
        )
    if Backend == "applescript":
        return (SendF3MacOsAppleScript, "AppleScript F3")
    if QuartzF3Available():
        return (
            lambda: SendF3MacOsQuartz(PulseSeconds, Target),
            f"Quartz+F3 ({Target})",
        )
    print(
        "gesture F3: Quartz not installed — using AppleScript. "
        "For HID F3: pip install pyobjc-framework-Quartz",
        file=sys.stderr,
    )
    return (SendF3MacOsAppleScript, "AppleScript F3 (fallback)")


@dataclass
class HandGestureMissionConfig:
    StableFrames: int
    ExtendedRatio: float
    OpenMinExtended: int
    FistMaxExtended: int
    CooldownSec: float
    HandSide: str


class HandMissionGestureRuntime:
    """Fist → open / open → fist state machine; calls EmitFn on transitions."""

    def __init__(
        self,
        Config: HandGestureMissionConfig,
        EmitFn: Callable[[], None],
        DryRun: bool,
    ) -> None:
        self.Config = Config
        self.EmitFn = EmitFn
        self.DryRun = DryRun
        self.LastFireT = 0.0
        self.F3On = False
        self.StablePose: Optional[str] = None
        self.CandidatePose = ""
        self.CandidateCount = 0

    def ProcessLandmarkerResult(self, Result, Now: float) -> None:
        Cfg = self.Config
        ChosenIdx: Optional[int] = None
        N = len(Result.hand_landmarks)
        if N > 0:
            if Cfg.HandSide == "any":
                ChosenIdx = 0
            else:
                Want = Cfg.HandSide
                for I, Cats in enumerate(Result.handedness):
                    if not Cats:
                        continue
                    Name = Cats[0].category_name
                    if Name and Name.lower() == Want:
                        ChosenIdx = I
                        break
                if ChosenIdx is None:
                    ChosenIdx = 0

        if ChosenIdx is not None:
            Lm = Result.hand_landmarks[ChosenIdx]
            Raw = ClassifyRawPoseLm(
                Lm,
                Cfg.ExtendedRatio,
                Cfg.OpenMinExtended,
                Cfg.FistMaxExtended,
            )
            if Raw != "ambiguous":
                if Raw == self.CandidatePose:
                    self.CandidateCount += 1
                else:
                    self.CandidatePose = Raw
                    self.CandidateCount = 1

                if self.CandidateCount >= Cfg.StableFrames:
                    OldStable = self.StablePose
                    NewStable = self.CandidatePose
                    if NewStable != OldStable:
                        WouldFire = (
                            OldStable == "fist"
                            and NewStable == "open"
                            and not self.F3On
                        ) or (
                            OldStable == "open"
                            and NewStable == "fist"
                            and self.F3On
                        )
                        CoolOk = (Now - self.LastFireT) >= Cfg.CooldownSec
                        if WouldFire and not CoolOk:
                            pass
                        else:
                            self.StablePose = NewStable
                            if WouldFire and CoolOk:
                                Fired = False
                                if (
                                    OldStable == "fist"
                                    and NewStable == "open"
                                    and not self.F3On
                                ):
                                    self.F3On = True
                                    Fired = True
                                    print("F3_ON", flush=True)
                                    if not self.DryRun:
                                        self.EmitFn()
                                elif (
                                    OldStable == "open"
                                    and NewStable == "fist"
                                    and self.F3On
                                ):
                                    self.F3On = False
                                    Fired = True
                                    print("F3_OFF", flush=True)
                                    if not self.DryRun:
                                        self.EmitFn()
                                if Fired:
                                    self.LastFireT = Now
                    self.CandidateCount = 0
        else:
            self.CandidateCount = 0
            self.CandidatePose = ""


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
            "Fist → open palm / open → fist toggles Mission Control or F3 (same camera thread "
            "as --require-hand). See --gesture-* options."
        ),
    )
    Parser.add_argument(
        "--gesture-invoke",
        choices=("mission-control", "f3"),
        default="mission-control",
        help="mission-control: AppleScript ⌃↑. f3: --gesture-f3-* (Quartz preferred).",
    )
    Parser.add_argument(
        "--gesture-stable-frames",
        type=int,
        default=8,
        help="Frames to confirm fist/open pose. Default: 8.",
    )
    Parser.add_argument(
        "--gesture-extended-ratio",
        type=float,
        default=1.04,
        metavar="RATIO",
        help="Tip–wrist vs PIP–wrist ratio for extended finger. Default: 1.04.",
    )
    Parser.add_argument(
        "--gesture-open-min",
        type=int,
        default=4,
        help="Min extended fingers (of 4) for open palm. Default: 4.",
    )
    Parser.add_argument(
        "--gesture-fist-max",
        type=int,
        default=1,
        help="Max extended fingers for fist. Default: 1.",
    )
    Parser.add_argument(
        "--gesture-cooldown",
        type=float,
        default=1.2,
        metavar="SEC",
        help="Seconds between gesture actions. Default: 1.2.",
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
        help="Print F3_ON / F3_OFF only; do not run Mission Control or F3.",
    )
    Parser.add_argument(
        "--gesture-f3-backend",
        choices=("auto", "quartz", "applescript"),
        default="auto",
        help="With --gesture-invoke f3: Quartz or AppleScript key code 99.",
    )
    Parser.add_argument(
        "--gesture-f3-pulse-sec",
        type=float,
        default=0.02,
        metavar="SEC",
        help="Quartz F3: hold between down/up. Default: 0.02.",
    )
    Parser.add_argument(
        "--gesture-f3-target",
        choices=("hid", "session", "annotated", "frontmost"),
        default="hid",
        help="Quartz F3 event tap (hid default) or frontmost PID.",
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
        if Args.gesture_stable_frames < 1:
            print("--gesture-stable-frames must be >= 1.", file=sys.stderr)
            sys.exit(1)
        if Args.gesture_open_min < 1 or Args.gesture_open_min > 4:
            print("--gesture-open-min must be 1..4.", file=sys.stderr)
            sys.exit(1)
        if Args.gesture_fist_max < 0 or Args.gesture_fist_max > 3:
            print("--gesture-fist-max must be 0..3.", file=sys.stderr)
            sys.exit(1)
        if Args.gesture_fist_max >= Args.gesture_open_min:
            print("--gesture-fist-max must be < --gesture-open-min.", file=sys.stderr)
            sys.exit(1)
        if Args.gesture_f3_pulse_sec < 0:
            print("--gesture-f3-pulse-sec must be >= 0.", file=sys.stderr)
            sys.exit(1)

    GestureEmit: Optional[Callable[[], None]] = None
    GestureEmitLabel = ""
    if Args.hand_gesture:
        if Args.gesture_invoke == "mission-control":
            GestureEmit = SendMissionControlViaAppleScript
            GestureEmitLabel = "AppleScript (⌃↑ Mission Control)"
        else:
            GestureEmit, GestureEmitLabel = ResolveF3Emitter(
                Args.gesture_f3_backend,
                Args.gesture_f3_pulse_sec,
                Args.gesture_f3_target,
            )
        if not Args.gesture_dry_run:
            if not MacOsAccessibilityTrusted():
                print(
                    "Accessibility is OFF — macOS will ignore gesture shortcuts. "
                    "System Settings → Privacy & Security → Accessibility → enable the "
                    "app running Python. Remove and re-add the entry if needed.",
                    file=sys.stderr,
                )
            UsesQuartzGesture = Args.gesture_invoke == "f3" and (
                (Args.gesture_f3_backend == "quartz")
                or (
                    Args.gesture_f3_backend == "auto"
                    and QuartzF3Available()
                )
            )
            if UsesQuartzGesture and Args.gesture_f3_target == "frontmost":
                print(
                    "gesture F3 → frontmost only: "
                    + FrontmostApplicationLabel()
                    + ". For Mission Control use --gesture-f3-target hid.",
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
            if Args.hand_gesture and GestureEmit is not None:
                Gcfg = HandGestureMissionConfig(
                    StableFrames=Args.gesture_stable_frames,
                    ExtendedRatio=Args.gesture_extended_ratio,
                    OpenMinExtended=Args.gesture_open_min,
                    FistMaxExtended=Args.gesture_fist_max,
                    CooldownSec=Args.gesture_cooldown,
                    HandSide=Args.gesture_hand,
                )
                MissionRt = HandMissionGestureRuntime(
                    Gcfg,
                    GestureEmit,
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
                    Msg += (
                        f" Hand gesture: {GestureEmitLabel}"
                        + (" (dry-run)." if Args.gesture_dry_run else ".")
                    )
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
    Rest = sys.argv[1:]
    if Rest and Rest[0] == "hand-test":
        sys.argv = [sys.argv[0]] + Rest[1:]
        MainHandTest()
        return
    MainListen()


if __name__ == "__main__":
    Main()
