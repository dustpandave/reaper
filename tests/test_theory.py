import random

import numpy as np
import soundfile as sf

from reaper_agent import theory
from reaper_agent.analysis import analyze_audio

PROG = [{"chord": c, "beats": 4} for c in ["Am", "F", "C", "G"]]


def test_parse():
    assert theory.parse_key("F# dorian") == (6, "dorian")
    assert theory.parse_key("Ebm") == (3, "minor")
    assert theory.parse_chord("C/E") == (0, [0, 4, 7], 4)
    assert theory.parse_chord("Bbmaj7")[0] == 10


def test_roman():
    assert [c["chord"] for c in theory.roman_to_chords("vi IV I V", "G")] == ["Em", "C", "G", "D"]
    assert [c["chord"] for c in theory.roman_to_chords("i bVI bVII i", "A minor")] == ["Am", "F", "G", "Am"]
    assert [c["chord"] for c in theory.roman_to_chords("ii7 V7 Imaj7", "Bb")] == ["Cm7", "F7", "A#maj7"]


def test_key_and_chord_detection():
    notes = theory.pad(PROG, octave=4) + theory.bassline(PROG, "root")
    assert theory.estimate_key(notes)[0]["key"] in ("A minor", "C major")
    assert [c["chord"] for c in theory.chords_by_bar(notes)] == ["Am", "F", "C", "G"]


def test_generators_stay_in_range_and_time():
    rng = random.Random(1)
    for kind, notes in {
        "bass": theory.bassline(PROG, "walking", rng=rng),
        "arp": theory.arpeggio(PROG, "updown", rng=rng),
        "counter": theory.counter_melody(PROG, "A minor", density=0.8, rng=rng),
        "drums": theory.drums("trap", bars=4, rng=rng),
    }.items():
        assert notes, kind
        assert all(0 <= n["pitch"] <= 127 and 0 <= n["start"] < 16 and n["length"] > 0 for n in notes), kind
    scale = {(9 + i) % 12 for i in theory.SCALES["minor"]}
    assert all(n["pitch"] % 12 in scale for n in theory.counter_melody(PROG, "A minor", rng=rng))


def test_audio_analysis(tmp_path):
    sr = 48000
    t = np.arange(sr * 3) / sr
    tone = 0.5 * np.sin(2 * np.pi * 100 * t)
    path = tmp_path / "tone.wav"
    sf.write(path, np.stack([tone, tone], axis=1), sr)
    a = analyze_audio(str(path))
    assert abs(a["sample_peak_dbfs"] - (-6.02)) < 0.1
    assert a["stereo_correlation"] == 1.0
    assert a["spectral_balance_pct"]["low"] > 90
    assert -12 < a["integrated_lufs"] < -6
