"""The compact text notation used by `gpscore view` (reading) and by the `write` operation (writing).

One voice of one bar is a row of beats separated by spaces:

    e(K+HHc){f}  e(HHc)  e(S[>]+HHc)  e(HHc)  q(-)  q(K[^]+CR1)

    beat   := DURATION "(" NOTES ")" [ "{" OPTIONS "}" ]
    NOTES  := "-" (a rest)  |  NOTE ( "+" NOTE )*
    NOTE   := ["~"] ( STRING ":" FRET  |  DRUM ) [ "[" FLAG ("," FLAG)* "]" ]

DURATION  w h q e s t x (whole … 64th), dots "q." "e..", tuplets "e/3" (3:2) or "s/5:4".
STRING    1 = highest string, like in tablature (6 = low E on a 6-string guitar).
DRUM      a drum abbreviation from the track legend (K, S, HHc, CR1, ...), or #<index>.
"~"       the note is tied from the previous note on the same string / drum.
FLAG      >  ^  g  st  ten  pm  lr  x  h  v  vw  sl  lsl   (see music.FLAG_HELP)
          read-only, shown but not writable: bend harm tap trill
OPTIONS   a dynamic (ppp … fff), cresc / dim (hairpin), grace / grace-on, trem=1/8
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .music import DYNAMICS, HAIRPIN_NAMES, HAIRPINS, Rhythm, pitch_name
from .score import Score, Track, child_text

# ---------------------------------------------------------------- drum abbreviations

_DRUM_ABBR = {  # (element, articulation) -> abbreviation ; GP8 standard kit
    ("Kick Drum", "Kick (hit)"): "K", ("Acoustic Kick Drum", "Kick (hit)"): "K2",
    ("Snare", "Snare (hit)"): "S", ("Snare", "Snare (side stick)"): "SS", ("Snare", "Snare (rim shot)"): "SR",
    ("Electric Snare", "Electric Snare (hit)"): "ES", ("Sticks", "Snare (side stick)"): "STK",
    ("Charley", "Hi-Hat (closed)"): "HHc", ("Charley", "Hi-Hat (half)"): "HHh",
    ("Charley", "Hi-Hat (open)"): "HHo", ("Charley", "Pedal Hi-Hat (hit)"): "HHp",
    ("Tom Very High", "High Floor Tom (hit)"): "tom1", ("Tom High", "High Tom (hit)"): "tom2",
    ("Tom Medium", "Mid Tom (hit)"): "tom3", ("Tom Low", "Low Tom (hit)"): "tom4",
    ("Tom Very Low", "Very Low Tom (hit)"): "tom5", ("Very Low Floor Tom", "Low Floor Tom (hit)"): "tom6",
    ("Ride", "Ride (edge)"): "RDe", ("Ride", "Ride (middle)"): "RD", ("Ride", "Ride (bell)"): "RDb",
    ("Ride", "Ride (choke)"): "RDx", ("Ride Cymbal 2", "Ride (edge)"): "RD2e",
    ("Ride Cymbal 2", "Ride (middle)"): "RD2", ("Ride Cymbal 2", "Ride (bell)"): "RD2b",
    ("Ride Cymbal 2", "Ride (choke)"): "RD2x",
    ("Splash", "Splash (hit)"): "SPL", ("Splash", "Splash (choke)"): "SPLx",
    ("China", "China (hit)"): "CH", ("China", "China (choke)"): "CHx",
    ("Crash High", "Crash high (hit)"): "CR1", ("Crash High", "Crash high (choke)"): "CR1x",
    ("Crash Medium", "Crash medium (hit)"): "CR2", ("Crash Medium", "Crash medium (choke)"): "CR2x",
    ("Reverse Cymbal", "Reverse Cymbal (hit)"): "REV", ("Hand Clap", "Hand Clap (hit)"): "CLAP",
    ("Tambourine", "Tambourine (hit)"): "TAMB", ("Cowbell Medium", "Cowbell medium (hit)"): "CB",
}


def _auto_abbr(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower() or "drum"


class DrumKit:
    def __init__(self, track: Track):
        self.track = track
        self.abbr: list[str] = []
        used: set[str] = set()
        for a in track.articulations:
            ab = _DRUM_ABBR.get((a.element, a.name)) or _auto_abbr(a.name)
            base, n = ab, 2
            while ab.lower() in used:
                ab = f"{base}{n}"
                n += 1
            used.add(ab.lower())
            self.abbr.append(ab)
        self._lookup = {ab.lower(): i for i, ab in enumerate(self.abbr)}
        for i, a in enumerate(track.articulations):  # also accept slugged full names
            self._lookup.setdefault(_auto_abbr(a.name), i)

    def name(self, index: int) -> str:
        return self.abbr[index] if 0 <= index < len(self.abbr) else f"#{index}"

    def index(self, token: str) -> int:
        t = token.strip()
        if t.startswith("#") and t[1:].isdigit():
            i = int(t[1:])
            if 0 <= i < len(self.abbr):
                return i
        i = self._lookup.get(t.lower())
        if i is None:
            raise ValueError(f"unknown drum '{token}' for {self.track.label()}; "
                             f"see the legend (gpscore legend) or use #<index>")
        return i

    def legend(self, used: set[int] | None = None) -> str:
        items = [f"{self.abbr[i]}={a.name}" + (f" [{a.element}]" if self.abbr[i].endswith("2") else "")
                 for i, a in enumerate(self.track.articulations) if used is None or i in used]
        return "  ".join(items)


# ---------------------------------------------------------------- reading (XML -> text)

def note_flags(score: Score, n) -> list[str]:
    flags = []
    acc = int(child_text(n, "Accent", "0") or 0)
    if acc & 8:
        flags.append(">")
    if acc & 4:
        flags.append("^")
    if n.find("AntiAccent") is not None:
        flags.append("g")
    if acc & 1:
        flags.append("st")
    if acc & 16:
        flags.append("ten")
    if score.note_has_prop(n, "PalmMuted"):
        flags.append("pm")
    if n.find("LetRing") is not None:
        flags.append("lr")
    if score.note_has_prop(n, "Muted"):
        flags.append("x")
    if score.note_has_prop(n, "HopoOrigin"):
        flags.append("h")
    vib = child_text(n, "Vibrato")
    if vib:
        flags.append("vw" if vib == "Wide" else "v")
    slide = score.note_prop(n, "Slide", "Flags")
    if slide:
        s = int(slide)
        flags.append("sl" if s == 1 else "lsl" if s == 2 else f"sl={s}")
    if score.note_has_prop(n, "Bended"):
        flags.append("bend")
    if score.note_has_prop(n, "HarmonicType"):
        flags.append("harm")
    if score.note_has_prop(n, "Tapped"):
        flags.append("tap")
    if n.find("Trill") is not None:
        flags.append("trill")
    return flags


def is_tie_destination(n) -> bool:
    tie = n.find("Tie")
    return tie is not None and tie.get("destination") == "true"


def format_note(score: Score, track: Track, n, kit: DrumKit | None, pitch: bool = False, flats: bool = False) -> str:
    if track.kind == "drums" and kit is not None:
        art = score.note_articulation(n)
        body = kit.name(art if art is not None else -1)
    else:
        sf = score.note_string_fret(n)
        if sf is None:
            m = score.note_midi(n)
            body = pitch_name(m, flats) if m is not None else "?"
        else:
            s, f = sf
            body = f"{track.strings - s}:{f}" if track.strings else f"{s}:{f}"
            if pitch:
                m = score.note_midi(n)
                if m is not None:
                    body += f"({pitch_name(m, flats)})"
    fl = note_flags(score, n)
    return ("~" if is_tie_destination(n) else "") + body + (f"[{','.join(fl)}]" if fl else "")


def beat_options(score: Score, b, prev_dynamic: str | None) -> tuple[list[str], str]:
    opts = []
    dyn = child_text(b, "Dynamic", "MF")
    if dyn != prev_dynamic:
        opts.append(dyn.lower())
    hp = child_text(b, "Hairpin")
    if hp:
        opts.append(HAIRPIN_NAMES.get(hp, hp.lower()))
    grace = child_text(b, "GraceNotes")
    if grace:
        opts.append("grace-on" if grace == "OnBeat" else "grace")
    trem = child_text(b, "Tremolo")
    if trem:
        opts.append(f"trem={trem}")
    text = child_text(b, "FreeText")
    if text:
        opts.append(f'"{text}"')
    other = [c.tag for c in b if c.tag in ("Chord", "Arpeggio", "Fadding", "Wah", "Whammy", "Legato", "Ottavia",
                                             "Golpe", "Slashed", "DeadSlapped", "Lyrics")]
    if other:
        opts.append("+" + "/".join(sorted(set(other))))
    return opts, dyn


def format_voice(score: Score, track: Track, voice_id: str, kit: DrumKit | None, prev_dynamic: str | None,
                 pitch: bool = False, flats: bool = False) -> tuple[str, str | None]:
    parts = []
    dyn = prev_dynamic
    for bid in score.beat_ids(voice_id):
        b = score.beats[bid]
        nids = score.note_ids(bid)
        notes = "+".join(format_note(score, track, score.notes[i], kit, pitch, flats) for i in nids) or "-"
        opts, dyn = beat_options(score, b, dyn)
        parts.append(f"{score.beat_rhythm(bid).token()}({notes})" + (f"{{{','.join(opts)}}}" if opts else ""))
    return "  ".join(parts), dyn


# ---------------------------------------------------------------- writing (text -> specs)

WRITABLE_FLAGS = {">", "^", "g", "st", "ten", "pm", "lr", "x", "h", "v", "vw", "sl", "lsl"}


@dataclass
class NoteSpec:
    string: int | None = None      # GPIF index (0 = lowest)
    fret: int | None = None
    art: int | None = None         # drum articulation index
    flags: list[str] = field(default_factory=list)
    tie: bool = False              # tied from the previous note on the same string/drum


@dataclass
class BeatSpec:
    rhythm: Rhythm
    notes: list[NoteSpec]
    dynamic: str | None = None
    hairpin: str | None = None     # GPIF value
    grace: str | None = None       # GPIF value
    tremolo: str | None = None


_BEAT_RE = re.compile(r"([wWhqestxX][.]*(?:/\d+(?::\d+)?)?)\(([^()]*)\)(?:\{([^{}]*)\})?")


def _split_notes(body: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "+" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def parse_note(token: str, track: Track, kit: DrumKit | None) -> NoteSpec:
    tie = token.startswith("~")
    token = token.lstrip("~")
    flags: list[str] = []
    m = re.match(r"^([^\[\]]+)(?:\[([^\]]*)\])?$", token)
    if not m:
        raise ValueError(f"bad note '{token}'")
    body, fl = m.group(1).strip(), m.group(2)
    if fl:
        flags = [f.strip() for f in re.split(r"[,\s]+", fl) if f.strip()]
        bad = [f for f in flags if f not in WRITABLE_FLAGS]
        if bad:
            raise ValueError(f"note '{token}': can't write flag(s) {bad}; writable: {sorted(WRITABLE_FLAGS)}")
    if track.kind == "drums":
        return NoteSpec(art=kit.index(body), flags=flags, tie=tie)
    sm = re.match(r"^(\d+):(\d+)(?:\([^)]*\))?$", body)
    if not sm:
        raise ValueError(f"bad note '{token}' for {track.label()}: expected STRING:FRET like 6:0 "
                         f"(string 1 = highest)")
    ui_string, fret = int(sm.group(1)), int(sm.group(2))
    if not 1 <= ui_string <= track.strings:
        raise ValueError(f"note '{token}': {track.label()} has {track.strings} strings")
    if not 0 <= fret <= track.fret_count:
        raise ValueError(f"note '{token}': fret out of range 0-{track.fret_count}")
    return NoteSpec(string=track.strings - ui_string, fret=fret, flags=flags, tie=tie)


def parse_options(text: str | None, spec: BeatSpec):
    if not text:
        return
    for opt in (o.strip() for o in text.split(",")):
        if not opt:
            continue
        low = opt.lower()
        if low.upper() in DYNAMICS:
            spec.dynamic = low.upper()
        elif low in HAIRPINS:
            spec.hairpin = HAIRPINS[low]
        elif low == "grace":
            spec.grace = "BeforeBeat"
        elif low == "grace-on":
            spec.grace = "OnBeat"
        elif low.startswith("trem="):
            spec.tremolo = low[5:]
        elif low.startswith("+") or low.startswith('"'):
            continue  # display-only markers from the view
        else:
            raise ValueError(f"unknown beat option '{opt}' (use a dynamic, cresc, dim, grace, grace-on, trem=1/8)")


def parse_voice(text: str, track: Track, kit: DrumKit | None) -> list[BeatSpec]:
    text = text.strip()
    beats: list[BeatSpec] = []
    pos = 0
    for m in _BEAT_RE.finditer(text):
        gap = text[pos:m.start()].strip()
        if gap:
            raise ValueError(f"can't read '{gap}' in: {text}")
        pos = m.end()
        rhythm = Rhythm.parse(m.group(1))
        body = m.group(2).strip()
        notes = [] if body in ("-", "", "r") else [parse_note(t, track, kit) for t in _split_notes(body)]
        spec = BeatSpec(rhythm, notes)
        parse_options(m.group(3), spec)
        beats.append(spec)
    rest = text[pos:].strip()
    if rest:
        raise ValueError(f"can't read '{rest}' in: {text}")
    return beats


_BAR_LINE = re.compile(r"^b(\d+)\b(.*)$")
_VOICE_LINE = re.compile(r"^v([1-4])\s*:?\s*(.*)$")


def parse_bars(text: str) -> dict[int, dict[int, str]]:
    """Text in the view's layout -> {bar number: {voice 1-4: voice text}}.

    Lines starting with 'b<N>' open a bar (anything after the number on that line is ignored,
    e.g. the time signature or section copied from the view). Lines 'v<k> ...' hold a voice.
    A voice can also follow the bar number on the same line: 'b12 v1 e(K) ...'.
    '//' starts a comment; '==' / 'legend:' lines are ignored."""
    out: dict[int, dict[int, str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line or line.startswith("==") or line.lower().startswith("legend") or line.startswith("("):
            continue
        bm = _BAR_LINE.match(line)
        if bm:
            current = int(bm.group(1))
            out.setdefault(current, {})
            tail = bm.group(2).strip()
            vm = re.search(r"\bv([1-4])\s*:?\s*(.*)$", tail)
            if vm:
                out[current][int(vm.group(1))] = vm.group(2)
            continue
        vm = _VOICE_LINE.match(line)
        if vm:
            if current is None:
                raise ValueError(f"voice line before any 'b<N>' bar line: {raw}")
            out[current][int(vm.group(1))] = vm.group(2)
            continue
        raise ValueError(f"can't read line: {raw}")
    return out
