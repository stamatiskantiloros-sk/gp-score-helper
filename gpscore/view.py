"""Text views of a score: overview, structure, and bar-by-bar notation."""
from __future__ import annotations

from fractions import Fraction

from .music import pitch_name
from .notation import DrumKit, format_voice
from .score import Score, Track, _ids, child_text

MAJOR_KEYS = {-7: "Cb", -6: "Gb", -5: "Db", -4: "Ab", -3: "Eb", -2: "Bb", -1: "F", 0: "C", 1: "G", 2: "D",
              3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#"}
MINOR_KEYS = {-7: "Ab", -6: "Eb", -5: "Bb", -4: "F", -3: "C", -2: "G", -1: "D", 0: "A", 1: "E", 2: "B",
              3: "F#", 4: "C#", 5: "G#", 6: "D#", 7: "A#"}
TEMPO_UNITS = {1: "e", 2: "q", 3: "q.", 4: "h", 5: "h."}


def parse_range(spec: str | None, last: int) -> list[int]:
    """'9-16', '3', '1,4,9-12', 'all' / None -> sorted bar numbers (1-based)."""
    if spec is None or str(spec).strip().lower() in ("", "all", "*"):
        return list(range(1, last + 1))
    out: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else last
            out.update(range(a, b + 1))
        else:
            out.add(int(part))
    bad = [b for b in out if not 1 <= b <= last]
    if bad:
        raise IndexError(f"bar(s) {bad} out of range 1-{last}")
    return sorted(out)


def tempo_points(score: Score) -> list[dict]:
    out = []
    for a in score.root.findall("MasterTrack/Automations/Automation"):
        if child_text(a, "Type") != "Tempo":
            continue
        val = child_text(a, "Value").split()
        if not val:
            continue
        out.append({"bar": int(child_text(a, "Bar", "0")) + 1, "pos": float(child_text(a, "Position", "0") or 0),
                    "bpm": float(val[0]), "unit": TEMPO_UNITS.get(int(float(val[1])) if len(val) > 1 else 2, "q"),
                    "linear": child_text(a, "Linear") == "true", "text": child_text(a, "Text") or None,
                    "el": a})
    out.sort(key=lambda p: (p["bar"], p["pos"]))
    return out


def _fmt_bpm(p: dict) -> str:
    bpm = int(p["bpm"]) if p["bpm"] == int(p["bpm"]) else p["bpm"]
    s = f"{p['unit']}={bpm}"
    if p["pos"]:
        s += f"@{p['pos']:g}"
    if p["linear"]:
        s += "~"   # ramps to the next tempo point
    if p["text"]:
        s += f' "{p["text"]}"'
    return s


def masterbar_marks(score: Score, bar: int, tempos: dict[int, list[dict]]) -> str:
    mb = score.mb(bar)
    marks = []
    rep = mb.find("Repeat")
    if rep is not None and rep.get("start") == "true":
        marks.append("|:")
    endings = child_text(mb, "AlternateEndings")
    if endings:
        marks.append("ending=" + ",".join(endings.split()))
    sec = mb.find("Section")
    if sec is not None:
        letter, text = child_text(sec, "Letter"), child_text(sec, "Text")
        marks.append(f"[{letter}: {text}]" if letter else f"[{text}]")
    for p in tempos.get(bar, []):
        marks.append("tempo " + _fmt_bpm(p))
    if mb.find("Fermatas") is not None:
        marks.append("fermata")
    d = mb.find("Directions")
    if d is not None:
        for t in d:
            marks.append(f"{t.tag.lower()}={(t.text or '').strip()}")
    if mb.find("DoubleBar") is not None:
        marks.append("||")
    if rep is not None and rep.get("end") == "true":
        marks.append(f":|x{rep.get('count') or 2}")
    key = child_text(mb, "Key/AccidentalCount", "0")
    if bar == 1 or key != child_text(score.mb(bar - 1), "Key/AccidentalCount", "0"):
        k = int(key or 0)
        mode = child_text(mb, "Key/Mode", "Major")
        names = MINOR_KEYS if mode.lower() == "minor" else MAJOR_KEYS
        marks.append(f"key={names.get(k, k)} {mode.lower()}")
    return " ".join(marks)


def track_line(t: Track) -> str:
    s = f"T{t.number:<2} {t.name:<22} {t.kind}"
    if t.kind == "fretted":
        tun = " ".join(pitch_name(p) for p in reversed(t.tuning))
        s += f"  {t.strings}str  tuning (string 1→{t.strings}) {tun}"
        if t.capo:
            s += f"  capo {t.capo}"
        if t.transpose:
            s += f"  written {-t.transpose:+d} semitones"
    if t.staves > 1:
        s += f"  {t.staves} staves"
    return s


def overview(score: Score) -> str:
    title = child_text(score.root, "Score/Title")
    sub = child_text(score.root, "Score/SubTitle")
    artist = child_text(score.root, "Score/Artist")
    lines = [f"# {title or '(untitled)'}" + (f" — {sub}" if sub else "") + (f" / {artist}" if artist else ""),
             f"Guitar Pro {child_text(score.root, 'GPVersion', '?')}, {score.bar_count} bars, "
             f"{len(score.tracks)} tracks", "", "Tracks:"]
    lines += ["  " + track_line(t) for t in score.tracks]
    tempos = tempo_points(score)
    lines += ["", "Tempo: " + (" | ".join(f"b{p['bar']} {_fmt_bpm(p)}" for p in tempos) or "(none)")]
    return "\n".join(lines)


def structure(score: Score) -> str:
    """One line per bar: time signature, structure marks, and which tracks play."""
    tempos: dict[int, list[dict]] = {}
    for p in tempo_points(score):
        tempos.setdefault(p["bar"], []).append(p)
    lines = ["Structure (bar, time, marks, tracks with notes):"]
    prev_time = None
    for bar in range(1, score.bar_count + 1):
        time = score.time(bar)
        playing = []
        for t in score.tracks:
            has = any(score.note_ids(b) for v in score.voice_ids(t, bar) for b in score.beat_ids(v))
            if has:
                playing.append(str(t.number))
        marks = masterbar_marks(score, bar, tempos)
        tshow = time if time != prev_time else "   "
        prev_time = time
        lines.append(f"  b{bar:<4} {tshow:<5} {marks:<40} tracks {','.join(playing) or '-'}")
    return "\n".join(lines)


def _prev_dynamic(score: Score, track: Track, bar: int, slot: int, staff: int) -> str | None:
    for b in range(bar - 1, 0, -1):
        vid = score.voice_ids(track, b, staff)[slot]
        bids = score.beat_ids(vid)
        if bids:
            return child_text(score.beats[bids[-1]], "Dynamic", "MF")
    return None


def bars_view(score: Score, track: Track, bars: list[int], pitch: bool = False, staff: int | None = None,
              legend: bool = True) -> str:
    kit = DrumKit(track) if track.kind == "drums" else None
    tempos: dict[int, list[dict]] = {}
    for p in tempo_points(score):
        tempos.setdefault(p["bar"], []).append(p)
    staves = [staff] if staff else list(range(1, track.staves + 1))
    lines = [f"== {track.label()} ({track.kind}) bars {_compress(bars)} =="]
    used: set[int] = set()
    body = []
    for st in staves:
        if track.staves > 1:
            body.append(f"-- staff {st} --")
        dyn = {slot: _prev_dynamic(score, track, bars[0], slot, st) if bars else None for slot in range(4)}
        prev_bar = None
        for bar in bars:
            if prev_bar is not None and bar != prev_bar + 1:
                dyn = {slot: _prev_dynamic(score, track, bar, slot, st) for slot in range(4)}
            prev_bar = bar
            marks = masterbar_marks(score, bar, tempos)
            bar_el = score.bar_el(track, bar, st)
            simile = child_text(bar_el, "SimileMark")
            body.append(f"b{bar} {score.time(bar)}" + (f"  {marks}" if marks else "") +
                        (f"  % simile ({simile})" if simile else ""))
            any_voice = False
            for slot, vid in enumerate(score.voice_ids(track, bar, st)):
                if vid == "-1" or not score.beat_ids(vid):
                    continue
                any_voice = True
                text, dyn[slot] = format_voice(score, track, vid, kit, dyn[slot], pitch, score.key_flats(bar))
                length = score.voice_length(vid)
                warn = "" if length == score.bar_len(bar) else \
                    f"   !! voice is {_q(length)} of {_q(score.bar_len(bar))} quarters"
                body.append(f"  v{slot + 1}  {text}{warn}")
                if kit:
                    for bid in score.beat_ids(vid):
                        for nid in score.note_ids(bid):
                            a = score.note_articulation(score.notes[nid])
                            if a is not None:
                                used.add(a)
            if not any_voice:
                body.append("  (empty)")
    if kit and legend:
        lines.append("legend: " + (kit.legend(used) if used else "(no notes)"))
    return "\n".join(lines + body)


def _q(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _compress(bars: list[int]) -> str:
    if not bars:
        return "-"
    parts, start, prev = [], bars[0], bars[0]
    for b in bars[1:] + [None]:
        if b is not None and b == prev + 1:
            prev = b
            continue
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
        if b is not None:
            start = prev = b
    return ",".join(parts)


NOTATION_HELP = """\
Notation: beats separated by spaces, DURATION(NOTES){OPTIONS}
  durations w h q e s t x (whole…64th), dots q. e.., tuplets e/3 s/5:4
  notes: STRING:FRET (string 1 = highest) or drum abbreviation; '+' joins a chord; '-' = rest; '~' = tied from previous
  note flags [..]: > accent, ^ heavy accent, g ghost, st staccato, ten tenuto, pm palm mute, lr let ring,
                   x dead note, h hammer/pull to next, v/vw vibrato, sl/lsl slide; (read-only: bend harm tap trill)
  beat options {..}: dynamic ppp…fff (shown when it changes), cresc/dim hairpin, grace/grace-on, trem=1/8
  bar marks: |: repeat start, :|xN repeat end, [A: text] section, tempo q=120 (~ = ramps to next), ending=1,2
"""
