"""Music theory helpers: key/chord analysis and part generators.

Notes are dicts: {"pitch": int, "start": beats, "length": beats, "velocity": int, "channel": int}.
Beats are quarter notes, relative to the start of the clip.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor": [0, 2, 3, 5, 7, 9, 11],
    "major_pentatonic": [0, 2, 4, 7, 9],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10],
}

CHORD_QUALITIES = {
    "": [0, 4, 7],
    "m": [0, 3, 7],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "5": [0, 7],
    "6": [0, 4, 7, 9],
    "m6": [0, 3, 7, 9],
    "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10],
    "m7b5": [0, 3, 6, 10],
    "dim7": [0, 3, 6, 9],
    "add9": [0, 4, 7, 14],
    "madd9": [0, 3, 7, 14],
    "9": [0, 4, 7, 10, 14],
    "maj9": [0, 4, 7, 11, 14],
    "m9": [0, 3, 7, 10, 14],
}
_QUALITY_ALIASES = {"maj": "", "M": "", "min": "m", "-": "m", "M7": "maj7", "Δ7": "maj7", "ø": "m7b5",
                    "°": "dim", "o": "dim", "min7": "m7", "-7": "m7"}

# Krumhansl-Kessler key profiles
_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def pc_from_name(name: str) -> int:
    name = name.strip()
    pc = _PC[name[0].upper()]
    for ch in name[1:]:
        if ch in "#♯":
            pc += 1
        elif ch in "b♭":
            pc -= 1
    return pc % 12


def parse_key(key: str) -> tuple[int, str]:
    """'A minor', 'F# dorian', 'Bb', 'Ebm' -> (tonic pitch class, scale name)."""
    m = re.match(r"^\s*([A-Ga-g][#b♯♭]?)\s*(.*)$", key)
    if not m:
        raise ValueError(f"can't parse key: {key!r}")
    tonic = pc_from_name(m.group(1))
    mode = m.group(2).strip().lower().replace(" ", "_") or "major"
    mode = {"m": "minor", "min": "minor", "maj": "major", "aeolian": "minor", "ionian": "major"}.get(mode, mode)
    if mode not in SCALES:
        raise ValueError(f"unknown scale {mode!r}; use one of {sorted(SCALES)}")
    return tonic, mode


def parse_chord(symbol: str) -> tuple[int, list[int], int | None]:
    """'Am7' -> (root pc, intervals, bass pc or None). Supports slash chords like 'C/E'."""
    symbol = symbol.strip()
    bass = None
    if "/" in symbol:
        symbol, bass_name = symbol.split("/", 1)
        bass = pc_from_name(bass_name)
    m = re.match(r"^([A-G][#b♯♭]?)(.*)$", symbol)
    if not m:
        raise ValueError(f"can't parse chord: {symbol!r}")
    root = pc_from_name(m.group(1))
    qual = _QUALITY_ALIASES.get(m.group(2), m.group(2))
    if qual not in CHORD_QUALITIES:
        raise ValueError(f"unknown chord quality {m.group(2)!r} in {symbol!r}")
    return root, CHORD_QUALITIES[qual], bass


def note_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def scale_pitches(tonic: int, mode: str, low: int, high: int) -> list[int]:
    pcs = {(tonic + i) % 12 for i in SCALES[mode]}
    return [p for p in range(low, high + 1) if p % 12 in pcs]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _pc_histogram(notes: list[dict]) -> list[float]:
    hist = [0.0] * 12
    for n in notes:
        if not n.get("muted") and n.get("channel", 0) != 9:
            hist[n["pitch"] % 12] += max(n.get("length", 0.25), 0.05) * (n.get("velocity", 96) / 127)
    return hist


def _corr(a: list[float], b: list[float]) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def estimate_key(notes: list[dict]) -> list[dict]:
    """Top 3 key candidates with correlation scores."""
    hist = _pc_histogram(notes)
    if not any(hist):
        return []
    scores = []
    for tonic in range(12):
        rotated = hist[tonic:] + hist[:tonic]
        scores.append((_corr(rotated, _MAJOR_PROFILE), f"{NOTE_NAMES[tonic]} major"))
        scores.append((_corr(rotated, _MINOR_PROFILE), f"{NOTE_NAMES[tonic]} minor"))
    scores.sort(reverse=True)
    return [{"key": k, "score": round(s, 3)} for s, k in scores[:3]]


def _best_chord(hist: list[float], bass_pc: int | None) -> str | None:
    if sum(hist) == 0:
        return None
    best, best_score = None, -1e9
    for root in range(12):
        for qual in ("", "m", "7", "maj7", "m7", "dim", "sus4", "sus2", "m7b5"):
            tones = {(root + i) % 12 for i in CHORD_QUALITIES[qual]}
            inside = sum(hist[pc] for pc in tones)
            outside = sum(hist[pc] for pc in range(12) if pc not in tones)
            score = inside - 1.2 * outside - 0.15 * len(tones) * (sum(hist) / 12)
            if bass_pc is not None and bass_pc == root:
                score += 0.25 * sum(hist)
            if score > best_score:
                best, best_score = f"{NOTE_NAMES[root]}{qual}", score
    return best


def chords_by_bar(notes: list[dict], beats_per_bar: float = 4, segments_per_bar: int = 1) -> list[dict]:
    """Guess the chord in each bar (or half-bar with segments_per_bar=2)."""
    seg_len = beats_per_bar / segments_per_bar
    segs: dict[int, list[float]] = defaultdict(lambda: [0.0] * 12)
    bass: dict[int, tuple[int, int]] = {}
    for n in notes:
        if n.get("muted") or n.get("channel", 0) == 9:
            continue
        s, e = n["start"], n["start"] + n.get("length", 0.25)
        i = int(s // seg_len)
        while i * seg_len < e:
            overlap = min(e, (i + 1) * seg_len) - max(s, i * seg_len)
            if overlap > 0:
                segs[i][n["pitch"] % 12] += overlap
                if i not in bass or n["pitch"] < bass[i][0]:
                    bass[i] = (n["pitch"], n["pitch"] % 12)
            i += 1
    if not segs:
        return []
    out = []
    for i in range(max(segs) + 1):
        out.append({"start": i * seg_len, "beats": seg_len,
                    "chord": _best_chord(segs[i], bass.get(i, (None, None))[1]) if i in segs else None})
    return out


def summarize_notes(notes: list[dict], beats_per_bar: float = 4) -> dict:
    pitched = [n for n in notes if n.get("channel", 0) != 9]
    if not pitched:
        return {"note_count": len(notes), "drums_only": bool(notes)}
    pitches = [n["pitch"] for n in pitched]
    return {
        "note_count": len(notes),
        "range": [note_name(min(pitches)), note_name(max(pitches))],
        "range_midi": [min(pitches), max(pitches)],
        "key_candidates": estimate_key(pitched),
        "chords_by_bar": chords_by_bar(pitched, beats_per_bar),
        "notes_per_beat": round(len(pitched) / max(1e-6, max(n["start"] + n.get("length", 0) for n in pitched)), 2),
    }


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def _chord_tones(root: int, intervals: list[int], low: int, high: int) -> list[int]:
    pcs = {(root + i) % 12 for i in intervals}
    return [p for p in range(low, high + 1) if p % 12 in pcs]


def _nearest(pitch: int, root_pc: int, low: int, high: int) -> int:
    cands = [p for p in range(low, high + 1) if p % 12 == root_pc]
    return min(cands, key=lambda p: abs(p - pitch)) if cands else pitch


def _n(pitch, start, length, vel, chan=0):
    return {"pitch": int(pitch), "start": round(start, 4), "length": round(length, 4),
            "velocity": int(max(1, min(127, vel))), "channel": chan}


def _iter_chords(chords: list[dict]):
    t = 0.0
    for c in chords:
        root, ivs, bass = parse_chord(c["chord"])
        yield t, float(c.get("beats", 4)), root, ivs, bass
        t += float(c.get("beats", 4))


def bassline(chords, style="root", octave=2, rng=None):
    rng = rng or random.Random()
    low, high = 12 * (octave + 1) - 5, 12 * (octave + 1) + 14
    notes, prev = [], 12 * (octave + 1)
    chord_list = list(_iter_chords(chords))
    for idx, (t, beats, root, ivs, bass) in enumerate(chord_list):
        r = _nearest(prev, bass if bass is not None else root, low, high)
        fifth = r + 7 if r + 7 <= high else r - 5
        if style == "root":
            notes.append(_n(r, t, beats * 0.95, 100))
        elif style == "pulse":
            for b in range(int(beats * 2)):
                notes.append(_n(r, t + b * 0.5, 0.45, 104 if b % 2 == 0 else 88))
        elif style == "root_fifth":
            for b in range(int(beats)):
                notes.append(_n(r if b % 2 == 0 else fifth, t + b, 0.9, 100 if b % 2 == 0 else 90))
        elif style == "octave":
            for b in range(int(beats * 2)):
                notes.append(_n(r + (12 if b % 2 else 0), t + b * 0.5, 0.4, 100 if b % 2 == 0 else 85))
        elif style == "syncopated":
            pattern = [(0, 0.75, 108), (0.75, 0.5, 88), (1.5, 0.5, 96), (2.5, 0.75, 100), (3.5, 0.5, 84)]
            for off, ln, vel in pattern:
                if off < beats:
                    p = r if off in (0, 2.5) else rng.choice([r, fifth, r + 12 if r + 12 <= high else r])
                    notes.append(_n(p, t + off, ln, vel))
        elif style == "walking":
            nxt = chord_list[(idx + 1) % len(chord_list)]
            target = _nearest(r, nxt[4] if nxt[4] is not None else nxt[2], low, high)
            tones = _chord_tones(root, ivs, low, high)
            steps = int(beats)
            line = [r]
            for b in range(1, steps):
                if b == steps - 1:
                    line.append(target + rng.choice([-1, 1]))  # chromatic approach
                else:
                    line.append(min(tones, key=lambda p: abs(p - (line[-1] + rng.choice([-4, -3, 3, 4]))) + rng.random()))
            for b, p in enumerate(line):
                notes.append(_n(p, t + b, 0.9, 96 if b == 0 else 84 + rng.randint(-6, 6)))
        else:
            raise ValueError("bass style must be root, pulse, root_fifth, octave, syncopated or walking")
        prev = r
    return notes


def arpeggio(chords, pattern="up", rate=0.5, octave=4, span=2, gate=0.8, rng=None):
    rng = rng or random.Random()
    notes = []
    base = 12 * (octave + 1)
    for t, beats, root, ivs, _ in _iter_chords(chords):
        tones = _chord_tones(root, ivs, base, base + 12 * span)
        if pattern == "down":
            seq = tones[::-1]
        elif pattern == "updown":
            seq = tones + tones[-2:0:-1]
        elif pattern == "random":
            seq = tones[:]
            rng.shuffle(seq)
        else:
            seq = tones
        steps = int(round(beats / rate))
        for s in range(steps):
            notes.append(_n(seq[s % len(seq)], t + s * rate, rate * gate, 92 if s % 4 == 0 else 76 + rng.randint(-5, 5)))
    return notes


def pad(chords, octave=4, voicing="close", rng=None):
    notes, prev = [], None
    center = 12 * (octave + 1) + 4
    for t, beats, root, ivs, bass in _iter_chords(chords):
        if voicing == "open":
            pcs = [(root + i) % 12 for i in ivs]
            v = [_nearest(center - 7, pcs[0], 0, 127)]
            for pc in pcs[1:]:
                v.append(_nearest(v[-1] + 6, pc, v[-1] + 1, 127))
        else:
            # voice leading: pick each chord tone nearest the previous voicing's center
            ref = sum(prev) / len(prev) if prev else center
            pcs = sorted({(root + i) % 12 for i in ivs})
            v = sorted(_nearest(int(ref), pc, int(ref) - 6, int(ref) + 6) for pc in pcs)
        for p in v:
            notes.append(_n(p, t, beats, 70))
        prev = v
    return notes


def counter_melody(chords, key, density=0.5, octave=5, melody=None, rng=None):
    """Chord tones on strong beats, scale steps between; moves against the given melody's contour."""
    rng = rng or random.Random()
    tonic, mode = parse_key(key)
    low, high = 12 * (octave + 1) - 3, 12 * (octave + 1) + 16
    scale = scale_pitches(tonic, mode, low, high)
    notes = []
    cur = scale[len(scale) // 2]

    def melody_dir(at: float) -> int:
        if not melody:
            return 0
        before = [n for n in melody if n["start"] <= at]
        after = [n for n in melody if n["start"] > at]
        if not before or not after:
            return 0
        return (after[0]["pitch"] > before[-1]["pitch"]) - (after[0]["pitch"] < before[-1]["pitch"])

    for t, beats, root, ivs, _ in _iter_chords(chords):
        tones = _chord_tones(root, ivs, low, high)
        b = 0.0
        while b < beats:
            strong = b % 2 == 0
            if not strong and rng.random() > density:
                b += 0.5 if density > 0.5 else 1.0
                continue
            if strong:
                cur = min(tones, key=lambda p: abs(p - cur) + rng.random() * 2)
            else:
                d = -melody_dir(t + b) or rng.choice([-1, 1])
                i = min(range(len(scale)), key=lambda k: abs(scale[k] - cur))
                cur = scale[max(0, min(len(scale) - 1, i + d))]
            step = 1.0 if density <= 0.5 else rng.choice([0.5, 0.5, 1.0])
            ln = min(step, beats - b)
            if strong and rng.random() < 0.35:
                ln = min(2.0, beats - b)
            notes.append(_n(cur, t + b, ln * 0.92, 90 if strong else 78))
            b += ln
    return notes


GM = {"kick": 36, "snare": 38, "rim": 37, "clap": 39, "closed_hat": 42, "open_hat": 46, "pedal_hat": 44,
      "low_tom": 45, "mid_tom": 47, "high_tom": 50, "crash": 49, "ride": 51, "shaker": 70, "tamb": 54}

# 16-step patterns per bar (sixteenth notes in 4/4)
DRUM_PATTERNS = {
    "rock": {"kick": "x.......x.x.....", "snare": "....x.......x...", "closed_hat": "x.x.x.x.x.x.x.x."},
    "four_on_floor": {"kick": "x...x...x...x...", "clap": "....x.......x...",
                      "closed_hat": "..x...x...x...x.", "open_hat": "..x...x...x...x."},
    "half_time": {"kick": "x.........x.....", "snare": "........x.......", "closed_hat": "x.x.x.x.x.x.x.x."},
    "breakbeat": {"kick": "x.........x..x..", "snare": "....x..x.x..x...", "closed_hat": "x.x.x.x.x.x.x.x."},
    "trap": {"kick": "x......x..x.....", "snare": "........x.......", "closed_hat": "x.xxx.x.x.xxx.xx"},
    "funk": {"kick": "x..x..x...x..x..", "snare": "....x..x.x..x..x", "closed_hat": "xxxxxxxxxxxxxxxx"},
    "bossa": {"kick": "x..x....x..x....", "rim": "x..x..x...x..x..", "closed_hat": "x.x.x.x.x.x.x.x."},
    "shuffle": {"kick": "x.....x.x.......", "snare": "....x.......x...", "closed_hat": "x..x.xx..x.xx..x"},
}


def drums(style="rock", bars=4, beats_per_bar=4, fill=True, humanize=0.0, rng=None):
    rng = rng or random.Random()
    if style not in DRUM_PATTERNS:
        raise ValueError(f"drum style must be one of {sorted(DRUM_PATTERNS)}")
    pat = DRUM_PATTERNS[style]
    step = beats_per_bar / 16
    notes = []
    for bar in range(bars):
        last = fill and bar == bars - 1 and bars > 1
        for inst, grid in pat.items():
            for i, ch in enumerate(grid):
                if ch != "x" or (last and i >= 12 and inst != "kick"):
                    continue
                vel = 110 if i % 4 == 0 else 85
                if inst in ("closed_hat", "shaker"):
                    vel = 90 if i % 4 == 0 else 65 + rng.randint(-8, 8)
                jitter = rng.uniform(-humanize, humanize) * step
                notes.append(_n(GM[inst], max(0, bar * beats_per_bar + i * step + jitter), step * 0.9,
                                vel + rng.randint(-int(10 * humanize), int(10 * humanize)), 9))
        if last:
            for k, tom in enumerate(["snare", "high_tom", "mid_tom", "low_tom"]):
                notes.append(_n(GM[tom], bar * beats_per_bar + (12 + k) * step, step * 0.9, 95 + k * 5, 9))
        if bar == 0:
            notes.append(_n(GM["crash"], bar * beats_per_bar, 1, 100, 9))
    return notes


PROGRESSIONS = {
    "pop": ["I V vi IV", "vi IV I V", "I vi IV V", "I IV vi V"],
    "melancholic": ["vi IV I V", "i VI III VII", "i iv VI V", "i VII VI VII"],
    "uplifting": ["IV V vi I", "I V vi iii IV I IV V", "IV I V vi"],
    "jazzy": ["ii7 V7 Imaj7 vi7", "Imaj7 vi7 ii7 V7", "iii7 vi7 ii7 V7"],
    "dark": ["i bII i VII", "i iv v i", "i bVI bVII i"],
    "soulful": ["Imaj7 iii7 IVmaj7 V7", "ii7 iii7 IVmaj7 V7", "vi7 ii7 V7 Imaj7"],
    "epic": ["i VI III VII", "VI VII i i", "i VII VI V"],
}

_ROMAN = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}


def roman_to_chords(progression: str, key: str, beats: float = 4) -> list[dict]:
    """'ii7 V7 Imaj7' in 'C major' -> [{'chord': 'Dm7', 'beats': 4}, ...].

    Case sets the third (upper = major, lower = minor). Plain numerals use the key's own scale
    (so 'III' in A minor is C); numerals with b/# are relative to the major scale ('bVI' in A minor is F).
    """
    tonic, mode = parse_key(key)
    own = SCALES[mode] if len(SCALES[mode]) == 7 else SCALES["major"]
    out = []
    for tok in progression.split():
        m = re.match(r"^(b|#)?(vii|iii|ii|iv|vi|v|i|VII|III|II|IV|VI|V|I)(.*)$", tok)
        if not m:
            raise ValueError(f"bad roman numeral {tok!r}")
        acc, num, ext = m.groups()
        deg = _ROMAN[num.lower()]
        if acc:
            root = (tonic + SCALES["major"][deg] + (-1 if acc == "b" else 1)) % 12
        else:
            root = (tonic + own[deg]) % 12
        ext = {"°": "dim", "o": "dim", "ø": "m7b5", "ø7": "m7b5", "°7": "dim7"}.get(ext, ext)
        if num.islower() and ext in ("", "7", "6", "9", "add9"):
            if num.lower() == "vii" and ext in ("", "7") and mode == "major":
                qual = "dim" if ext == "" else "m7b5"
            else:
                qual = "m" + ext
        else:
            qual = ext
        parse_chord(NOTE_NAMES[root] + qual)  # validate
        out.append({"chord": NOTE_NAMES[root] + qual, "beats": beats})
    return out


ARRANGEMENTS = {
    "pop": [("Intro", 4), ("Verse 1", 8), ("Pre-Chorus", 4), ("Chorus", 8), ("Verse 2", 8), ("Pre-Chorus", 4),
            ("Chorus", 8), ("Bridge", 8), ("Chorus", 8), ("Outro", 4)],
    "edm": [("Intro", 16), ("Build", 8), ("Drop", 16), ("Breakdown", 16), ("Build", 8), ("Drop", 16), ("Outro", 16)],
    "hiphop": [("Intro", 4), ("Hook", 8), ("Verse 1", 16), ("Hook", 8), ("Verse 2", 16), ("Hook", 8), ("Outro", 4)],
    "rock": [("Intro", 8), ("Verse 1", 16), ("Chorus", 8), ("Verse 2", 16), ("Chorus", 8), ("Solo", 16),
             ("Chorus", 8), ("Outro", 8)],
    "ambient": [("Emerge", 16), ("Bloom", 32), ("Drift", 16), ("Dissolve", 16)],
    "lofi": [("Intro", 4), ("A", 16), ("B", 8), ("A", 16), ("Outro", 4)],
}

SECTION_COLORS = {"intro": (90, 110, 140), "verse": (70, 140, 200), "pre": (120, 170, 120), "chorus": (220, 120, 70),
                  "hook": (220, 120, 70), "drop": (220, 80, 80), "bridge": (160, 110, 200), "build": (210, 180, 80),
                  "breakdown": (110, 160, 170), "solo": (200, 90, 160), "outro": (90, 110, 140)}


def section_color(name: str):
    low = name.lower()
    for k, c in SECTION_COLORS.items():
        if low.startswith(k):
            return list(c)
    return None
