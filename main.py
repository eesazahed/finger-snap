#!/usr/bin/env python3
"""
finger-snap entry point (macOS).

**Default command (no ``hand-test`` prefix):** double finger-snap listener — mic,
optional ``--require-hand`` webcam gate (MediaPipe presence). See ``MainListen``.

**Hand test:** ``python main.py hand-test`` — webcam hand echo / optional notify
(formerly ``HandRaiseNotifyTest.py``). Same ``pip install mediapipe opencv-python-headless``
for camera features; model caches under ``.cache/``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

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
    DoubleWindowMaxSeconds: float = 1.2
    ThirdSnapRejectSeconds: float = 0.38
    ListenCooldownAfterTriggerSeconds: float = 1.0
    NotificationTitle: str = "Finger Snap"
    StartupSoundFilename: str = "assets/audio/startupsong.wav"
    ChromeAppName: str = "Google Chrome"
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
        Vhf = float(np.sum(Power[Freqs >= Config.AntiClapVeryHighCutoffHz])) / Total
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
                self._PostTriggerCooldownUntil = Now + self._Config.ListenCooldownAfterTriggerSeconds
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


def PlayStartupSound(WavPath: Path) -> None:
    """Play a WAV via macOS ``afplay`` without blocking the audio input callback."""
    if not WavPath.is_file():
        if not getattr(PlayStartupSound, "_MissingLogged", False):
            setattr(PlayStartupSound, "_MissingLogged", True)
            print(f"Startup sound missing (skipped): {WavPath}", file=sys.stderr)
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


def SendMacNotification(Title: str, Message: str) -> None:
    SafeTitle = Title.replace("\\", "\\\\").replace('"', '\\"')
    SafeMessage = Message.replace("\\", "\\\\").replace('"', '\\"')
    Script = f'display notification "{SafeMessage}" with title "{SafeTitle}"'
    try:
        subprocess.run(
            ["osascript", "-e", Script],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        print("Could not run osascript; install on macOS for notifications.", file=sys.stderr)


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
        base_options=MpBaseOptions.BaseOptions(model_asset_path=str(ModelPath)),
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
        finally:
            Cap.release()
            Landmarker.close()

    Thread = threading.Thread(target=RunHandLoop, name="HandPresence", daemon=False)
    Thread.start()
    return Tracker, StopEvent, Thread


def MainListen() -> None:
    Parser = argparse.ArgumentParser(description="Detect double finger snaps from the mic.")
    Parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Print to stdout instead of showing a macOS notification.",
    )
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
        help="Camera device index when using --require-hand (default: 0).",
    )
    Args = Parser.parse_args()
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

    HandStop: Optional[threading.Event] = None
    HandThread: Optional[threading.Thread] = None
    HandPresentFn: Optional[Callable[[], bool]] = None
    OnHandGateRejected: Optional[Callable[[], None]] = None
    if Args.require_hand:
        Tracker, HandStop, HandThread = StartHandPresencePipeline(
            ScriptDir, Args.camera_index
        )
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

    def Callback(Indata, Frames, TimeInfo, Status) -> None:
        if Status:
            print(f"Audio status: {Status}", file=sys.stderr)
        Block = Indata.copy()
        Now = time.perf_counter()
        if not Detector.ProcessBlock(Block, Now):
            return
        if not Args.no_startup_sound:
            PlayStartupSound(StartupWav)
        if not Args.no_chrome:
            OpenChromeTab(ChromeUrl, Config.ChromeAppName)
        if Args.no_notify:
            print("Double snap detected.", flush=True)
        else:
            SendMacNotification(Config.NotificationTitle, "Double snap detected.")

    try:
        with sd.InputStream(
            samplerate=Config.SampleRate,
            blocksize=Config.BlockSize,
            channels=1,
            dtype="float32",
            callback=Callback,
        ):
            Msg = "Listening for double snaps. Ctrl+C to stop."
            if Config.AntiClapVeryHighCutoffHz > 0.0 or Config.MaxClapBoomHardRejectRatio > 0.0:
                Msg += " (spectral anti-clap: boom + HF.)"
            if Args.require_hand:
                Msg += " (hand must be visible when the second snap window closes.)"
            print(Msg, file=sys.stderr)
            while True:
                time.sleep(0.25)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
    finally:
        if HandStop is not None:
            HandStop.set()
        if HandThread is not None:
            HandThread.join(timeout=3.0)


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
        help="Minimum seconds between events (echo / optional notify). Default: 2.5.",
    )
    Parser.add_argument(
        "--preview",
        action="store_true",
        help="Show a small camera window; press q to quit.",
    )
    Parser.add_argument(
        "--macos-notify",
        action="store_true",
        help="Also post a macOS notification (osascript) using the same title/body.",
    )
    Parser.add_argument(
        "--notification-title",
        default="Hand",
        help="Echo line title (and macOS title if --macos-notify).",
    )
    Parser.add_argument(
        "--notification-body",
        default="Detected in camera",
        help="Echo line body (and macOS body if --macos-notify).",
    )
    Args = Parser.parse_args()
    if Args.mode == "raised" and Args.clear_y <= Args.raise_y:
        print("--clear-y must be greater than --raise-y (y grows downward).", file=sys.stderr)
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
        base_options=MpBaseOptions.BaseOptions(model_asset_path=str(ModelPath)),
        running_mode=MpVisionMode.VisionTaskRunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.55,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    Landmarker = MpHandLandmarker.HandLandmarker.create_from_options(Options)
    Cap = cv2.VideoCapture(Args.camera_index)
    if not Cap.isOpened():
        print(f"Could not open camera index {Args.camera_index}.", file=sys.stderr)
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
    if Args.macos_notify:
        Hint += " Also posting macOS notifications."
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
                    EchoHandEvent(Args.notification_title, Args.notification_body)
                    if Args.macos_notify:
                        SendMacNotification(
                            Args.notification_title, Args.notification_body
                        )
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
