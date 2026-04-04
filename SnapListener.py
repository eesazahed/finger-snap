#!/usr/bin/env python3
"""
Double finger-snap listener for macOS. Requires Microphone permission for the
terminal or Python interpreter you use to run this script.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd


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
    # After a valid 2nd snap, wait this long; a 3rd snap in this window cancels (no action).
    ThirdSnapRejectSeconds: float = 0.38
    # After a confirmed double snap, ignore new gestures for this long.
    ListenCooldownAfterTriggerSeconds: float = 1.0
    NotificationTitle: str = "Finger Snap"
    StartupSoundFilename: str = "assets/audio/startupsong.wav"
    ChromeAppName: str = "Google Chrome"
    # Share of total FFT power at/above this Hz (snaps are brighter overall).
    HighFreqCutoffHz: float = 3_000.0
    MinHighFreqEnergyRatio: float = 0.34
    # Keyboard clack energy sits in low–mid; snap crack has more power above HighBandLowHz.
    LowMidBandLowHz: float = 100.0
    LowMidBandHighHz: float = 2_400.0
    HighBandLowHz: float = 3_400.0
    MinHighToLowMidPowerRatio: float = 1.4
    # Single-block peak vs RMS; laptop mics smear snaps — keep this modest.
    MinCrestFactor: float = 3.35
    # Pre-emphasis on FFT input only (not peak/RMS) to stress transients vs dull thumps.
    SpectralPreemphasis: float = 0.94


@dataclass
class SpectralMetrics:
    Peak: float
    HfRatio: float
    HighToLowMidRatio: float
    CrestFactor: float


def ComputeSpectralMetrics(Block: np.ndarray, Config: ListenerConfig) -> SpectralMetrics:
    """One FFT; contrasts snap-like brightness/impulsiveness vs keyboard thumps."""
    X = np.asarray(Block, dtype=np.float64).ravel()
    N = int(X.size)
    Peak = float(np.max(np.abs(X)))
    Rms = float(np.sqrt(np.mean(np.square(X)))) + 1e-9
    CrestFactor = Peak / Rms
    if N < 32:
        return SpectralMetrics(Peak, 0.0, 0.0, CrestFactor)
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
    return SpectralMetrics(Peak, HfRatio, HighToLowMidRatio, CrestFactor)


def IsSnapLikeSpectral(Metrics: SpectralMetrics, Config: ListenerConfig) -> bool:
    """
    Prefer all three cues. Laptop mics often soften crest or HF slightly; HiLo ratio
    is kept strict as the main keyboard filter, with one relaxed partner allowed.
    """
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
    def __init__(self, Config: ListenerConfig) -> None:
        self._Config = Config
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


def Main() -> None:
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
            "(default: file URL of index.html in the same directory as SnapListener.py)"
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
    Args = Parser.parse_args()
    Config = ListenerConfig()
    ScriptDir = Path(__file__).resolve().parent
    DefaultIndexFileUrl = (ScriptDir / "index.html").as_uri()
    ChromeUrl = Args.chrome_url or DefaultIndexFileUrl
    StartupWav = (
        Path(Args.startup_wav).expanduser().resolve()
        if Args.startup_wav
        else ScriptDir / Config.StartupSoundFilename
    )

    Detector = DoubleSnapDetector(Config)

    def Callback(Indata, Frames, TimeInfo, Status) -> None:
        if Status:
            print(f"Audio status: {Status}", file=sys.stderr)
        Block = Indata.copy()
        Now = time.perf_counter()
        if Detector.ProcessBlock(Block, Now):
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
            print(
                "Listening for double snaps. Ctrl+C to stop.",
                file=sys.stderr,
            )
            while True:
                time.sleep(0.25)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)


if __name__ == "__main__":
    Main()
