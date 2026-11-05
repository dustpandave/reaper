"""Offline audio analysis for rendered mixes and stems."""

from __future__ import annotations

import numpy as np
import soundfile as sf

try:
    import pyloudnorm
except ImportError:  # optional
    pyloudnorm = None

BANDS = [("sub", 20, 60), ("low", 60, 250), ("low_mid", 250, 1000), ("mid", 1000, 4000),
         ("presence", 4000, 8000), ("air", 8000, 20000)]


def _db(x: float) -> float:
    return float(20 * np.log10(x)) if x > 0 else -150.0


def _true_peak(data: np.ndarray) -> float:
    """4x oversampled peak estimate via FFT interpolation on the loudest second."""
    mono_abs = np.abs(data).max(axis=1)
    centre = int(mono_abs.argmax())
    n = min(len(data), 48000)
    start = max(0, min(len(data) - n, centre - n // 2))
    seg = data[start:start + n]
    spec = np.fft.rfft(seg, axis=0)
    up = np.fft.irfft(spec, n=len(seg) * 4, axis=0) * 4
    return float(np.abs(up).max())


def analyze_audio(path: str) -> dict:
    data, sr = sf.read(path, always_2d=True, dtype="float64")
    if len(data) == 0:
        return {"path": path, "error": "file is empty"}
    channels = data.shape[1]
    mono = data.mean(axis=1)
    peak = float(np.abs(data).max())
    rms = float(np.sqrt(np.mean(data ** 2)))

    out: dict = {
        "path": path,
        "duration_s": round(len(data) / sr, 3),
        "sample_rate": sr,
        "channels": channels,
        "sample_peak_dbfs": round(_db(peak), 2),
        "true_peak_dbtp_est": round(_db(_true_peak(data)), 2),
        "rms_dbfs": round(_db(rms), 2),
        "crest_factor_db": round(_db(peak) - _db(rms), 2),
        "clipped_samples": int((np.abs(data) >= 0.9999).sum()),
        "dc_offset": round(float(mono.mean()), 5),
    }

    if pyloudnorm is not None and len(data) > sr * 0.4:
        meter = pyloudnorm.Meter(sr)
        out["integrated_lufs"] = round(float(meter.integrated_loudness(data)), 2)
        # short-term (3s) loudness range approximation
        win, hop = int(3 * sr), int(sr)
        st = [meter.integrated_loudness(data[i:i + win]) for i in range(0, max(1, len(data) - win), hop)]
        st = [x for x in st if np.isfinite(x) and x > -70]
        if len(st) > 2:
            out["loudness_range_lu"] = round(float(np.percentile(st, 95) - np.percentile(st, 10)), 2)
            out["max_short_term_lufs"] = round(float(max(st)), 2)

    if channels >= 2:
        l, r = data[:, 0], data[:, 1]
        denom = np.sqrt((l ** 2).sum() * (r ** 2).sum())
        out["stereo_correlation"] = round(float((l * r).sum() / denom), 3) if denom else None
        mid, side = (l + r) / 2, (l - r) / 2
        out["side_to_mid_db"] = round(_db(np.sqrt(np.mean(side ** 2))) - _db(np.sqrt(np.mean(mid ** 2))), 2)

    # Spectral balance: share of energy per band, averaged over frames
    n_fft = 8192
    hop = n_fft // 2
    if len(mono) >= n_fft:
        frames = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop]
        if len(frames) > 400:
            frames = frames[np.linspace(0, len(frames) - 1, 400).astype(int)]
        power = (np.abs(np.fft.rfft(frames * np.hanning(n_fft), axis=1)) ** 2).mean(axis=0)
        freqs = np.fft.rfftfreq(n_fft, 1 / sr)
        total = power[(freqs >= 20) & (freqs <= 20000)].sum() or 1.0
        out["spectral_balance_pct"] = {
            name: round(100 * float(power[(freqs >= lo) & (freqs < hi)].sum() / total), 1) for name, lo, hi in BANDS
        }
        centroid = float((freqs * power).sum() / power.sum()) if power.sum() else 0
        out["spectral_centroid_hz"] = round(centroid, 1)

    return out
