"""In-memory model of a `score.gpif`, with safe editing primitives.

The GPIF XML is a set of flat tables that refer to each other by id:

    MasterTrack (track order, tempo automations)
    MasterBars  one per written bar: time, key, repeats, section, and one Bar id per staff
    Bars        one per staff per bar: clef + 4 voice slots ("-1" = empty)
    Voices      ordered beat ids
    Beats       rhythm ref, dynamic, hairpin, grace, ... + ordered note ids
    Notes       string/fret or drum articulation, accent, tie, palm mute, let ring, ...
    Rhythms     note value, dots, tuplet

Guitar Pro de-duplicates: the same Beat or Note id is often referenced from many places
(every identical hi-hat hit in a song can be ONE note). Editing such an object in place
would change all of them, so every edit goes through `own_beat` / `own_note`, which clone
the object first when it is shared (copy-on-write).

We keep the whole tree in lxml so that everything we don't understand is preserved.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from lxml import etree

from .container import GpContainer
from .music import DEFAULT_DYNAMIC, NOTE_VALUES, Rhythm, bar_length, spell

# InstrumentSet/Type values whose notes are written as string + fret
FRETTED_TYPES = re.compile(r"guitar|bass|ukulele|banjo|mandolin|bouzouki|sitar|viol|cello|harp|lute|koto",
                           re.IGNORECASE)
XML_HEADER = b'<?xml version="1.0" encoding="utf-8"?>\n'
_PARSER = etree.XMLParser(strip_cdata=False, remove_blank_text=False, resolve_entities=False, huge_tree=True)

# Canonical child orders, used when we have to insert an element (unknown tags keep their place)
MASTERBAR_ORDER = ["Key", "Time", "DoubleBar", "Repeat", "AlternateEndings", "Section", "Fermatas",
                   "Directions", "TripletFeel", "FreeTime", "Bars", "XProperties"]
BEAT_ORDER = ["GraceNotes", "Bank", "Dynamic", "Fadding", "Tremolo", "Chord", "Rhythm",
              "TransposedPitchStemOrientation", "UserTransposedPitchStemOrientation",
              "ConcertPitchStemOrientation", "Hairpin", "Arpeggio", "FreeText", "Ottavia", "Legato",
              "Slashed", "DeadSlapped", "Notes", "Golpe", "Wah", "Timer", "Lyrics", "Properties", "XProperties"]
NOTE_ORDER = ["Accent", "AntiAccent", "LetRing", "Tie", "Vibrato", "Trill", "LeftFingering",
              "RightFingering", "Accidental", "InstrumentArticulation", "Ornament", "Properties", "XProperties"]


def _ids(text: str | None) -> list[str]:
    return (text or "").split()


def child_text(el, tag: str, default: str = "") -> str:
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text is not None else default


def insert_ordered(parent, child, order: list[str]):
    """Insert `child` among `parent`'s children respecting `order`."""
    tag = child.tag
    if tag not in order:
        parent.append(child)
        return child
    rank = order.index(tag)
    for i, existing in enumerate(parent):
        if existing.tag in order and order.index(existing.tag) > rank:
            parent.insert(i, child)
            return child
    parent.append(child)
    return child


def set_child(parent, tag: str, text: str | None, order: list[str], cdata: bool = False):
    el = parent.find(tag)
    if el is None:
        el = insert_ordered(parent, etree.Element(tag), order)
    el.text = etree.CDATA(text) if (cdata and text is not None) else text
    return el


def remove_child(parent, tag: str) -> bool:
    found = False
    for el in parent.findall(tag):
        parent.remove(el)
        found = True
    return found


# ---------------------------------------------------------------- tracks

@dataclass
class Articulation:
    index: int
    element: str
    name: str
    midi: int


@dataclass
class Track:
    index: int              # 0-based position in the score (MasterTrack order)
    el: object
    name: str
    kind: str               # 'drums' | 'fretted' | 'other'
    staves: int
    column: int             # first column in MasterBar/Bars
    tuning: list[int] = field(default_factory=list)   # MIDI, lowest string first (GPIF string 0)
    transpose: int = 0      # semitones; written pitch = sounding - transpose
    capo: int = 0
    fret_count: int = 24
    articulations: list[Articulation] = field(default_factory=list)

    @property
    def number(self) -> int:
        return self.index + 1

    @property
    def strings(self) -> int:
        return len(self.tuning)

    def label(self) -> str:
        return f"T{self.number} {self.name}"


# ---------------------------------------------------------------- score

class Score:
    def __init__(self, gpif: bytes, container: GpContainer | None = None):
        self.container = container
        self.root = etree.fromstring(gpif, _PARSER)
        self._header = gpif[:gpif.index(b"<GPIF")] if b"<GPIF" in gpif else XML_HEADER
        self.pools = {name: self.root.find(name) for name in
                      ("MasterBars", "Bars", "Voices", "Beats", "Notes", "Rhythms")}
        for name, el in self.pools.items():
            if el is None:
                raise ValueError(f"score.gpif has no <{name}> table")
        self.bars = {e.get("id"): e for e in self.pools["Bars"].findall("Bar")}
        self.voices = {e.get("id"): e for e in self.pools["Voices"].findall("Voice")}
        self.beats = {e.get("id"): e for e in self.pools["Beats"].findall("Beat")}
        self.notes = {e.get("id"): e for e in self.pools["Notes"].findall("Note")}
        self.rhythms = {e.get("id"): e for e in self.pools["Rhythms"].findall("Rhythm")}
        self.masterbars = self.pools["MasterBars"].findall("MasterBar")
        self.tracks = self._read_tracks()
        self._next = {k: max([int(i) for i in d] + [-1]) + 1 for k, d in
                      (("Bars", self.bars), ("Voices", self.voices), ("Beats", self.beats),
                       ("Notes", self.notes), ("Rhythms", self.rhythms))}
        self._rhythm_index = {self.rhythm_of_el(el): rid for rid, el in self.rhythms.items()}
        self._count_refs()
        self.changes: list[str] = []
        self.orphans: set[tuple[str, str]] = set()

    # ---- loading ----------------------------------------------------------
    @classmethod
    def open(cls, path: str | Path) -> "Score":
        c = GpContainer.load(path)
        return cls(c.gpif, c)

    def _read_tracks(self) -> list[Track]:
        tracks_el = self.root.find("Tracks")
        by_id = {t.get("id"): t for t in tracks_el.findall("Track")}
        order = _ids(child_text(self.root, "MasterTrack/Tracks")) or list(by_id)
        out, column = [], 0
        for i, tid in enumerate(order):
            t = by_id[tid]
            staves = t.find("Staves")
            n_staves = max(1, len(staves)) if staves is not None else 1
            itype = child_text(t, "InstrumentSet/Type")
            tuning_el = t.find(".//Staff/Properties/Property[@name='Tuning']/Pitches")
            tuning = [int(x) for x in _ids(tuning_el.text if tuning_el is not None else "")]
            if itype == "drumKit":
                kind = "drums"
            elif tuning and any(tuning) and FRETTED_TYPES.search(itype or "guitar"):
                kind = "fretted"
            else:
                kind = "other"
            capo_el = t.find(".//Staff/Properties/Property[@name='CapoFret']/Fret")
            frets_el = t.find(".//Staff/Properties/Property[@name='FretCount']/Number")
            transpose = int(child_text(t, "Transpose/Chromatic", "0") or 0) + \
                12 * int(child_text(t, "Transpose/Octave", "0") or 0)
            arts = []
            for el in t.findall("InstrumentSet/Elements/Element"):
                for a in el.findall("Articulations/Articulation"):
                    arts.append(Articulation(len(arts), child_text(el, "Name"), child_text(a, "Name"),
                                             int(child_text(a, "OutputMidiNumber", "0") or 0)))
            out.append(Track(i, t, child_text(t, "Name"), kind, n_staves, column,
                             tuning if kind == "fretted" else [], transpose,
                             int(capo_el.text) if capo_el is not None and capo_el.text else 0,
                             int(frets_el.text) if frets_el is not None and frets_el.text else 24,
                             arts if kind == "drums" else []))
            column += n_staves
        return out

    def _count_refs(self):
        self.refs = {"Bars": Counter(), "Voices": Counter(), "Beats": Counter(), "Notes": Counter(),
                     "Rhythms": Counter()}
        for mb in self.masterbars:
            self.refs["Bars"].update(_ids(child_text(mb, "Bars")))
        for b in self.bars.values():
            self.refs["Voices"].update(v for v in _ids(child_text(b, "Voices")) if v != "-1")
        for v in self.voices.values():
            self.refs["Beats"].update(_ids(child_text(v, "Beats")))
        for b in self.beats.values():
            self.refs["Notes"].update(_ids(child_text(b, "Notes")))
            r = b.find("Rhythm")
            if r is not None:
                self.refs["Rhythms"][r.get("ref")] += 1

    # ---- saving -----------------------------------------------------------
    def to_gpif(self) -> bytes:
        self._drop_orphans()
        return self._header + etree.tostring(self.root, encoding="utf-8", xml_declaration=False)

    def save(self, out: str | Path, overwrite: bool = False) -> Path:
        if self.container is None:
            raise ValueError("score was not loaded from a file")
        return self.container.save(self.to_gpif(), out, overwrite)

    def _drop_orphans(self):
        """Remove objects that *we* detached and that nothing references any more."""
        changed = True
        while changed:
            changed = False
            for pool, oid in list(self.orphans):
                if self.refs[pool][oid] > 0:
                    self.orphans.discard((pool, oid))
                    continue
                table = getattr(self, pool.lower())
                el = table.pop(oid, None)
                self.orphans.discard((pool, oid))
                if el is None:
                    continue
                self.pools[pool].remove(el)
                changed = True
                if pool == "Voices":
                    self._release("Beats", _ids(child_text(el, "Beats")))
                elif pool == "Beats":
                    self._release("Notes", _ids(child_text(el, "Notes")))
                    r = el.find("Rhythm")
                    if r is not None:
                        self._release("Rhythms", [r.get("ref")])

    def _release(self, pool: str, ids: list[str]):
        for i in ids:
            if i == "-1":
                continue
            self.refs[pool][i] -= 1
            if self.refs[pool][i] <= 0:
                self.orphans.add((pool, i))

    def _retain(self, pool: str, ids: list[str]):
        for i in ids:
            if i != "-1":
                self.refs[pool][i] += 1

    def new_id(self, pool: str) -> str:
        i = self._next[pool]
        self._next[pool] += 1
        return str(i)

    # ---- lookup -------------------------------------------------------------
    def track(self, ref) -> Track:
        """1-based number, or a (case-insensitive) name / unique name prefix, or 'T3'."""
        if isinstance(ref, Track):
            return ref
        if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().lstrip("Tt").isdigit()):
            n = int(str(ref).strip().lstrip("Tt"))
            if not 1 <= n <= len(self.tracks):
                raise KeyError(f"no track {n} (score has {len(self.tracks)})")
            return self.tracks[n - 1]
        want = str(ref).strip().lower()
        exact = [t for t in self.tracks if t.name.lower() == want]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise KeyError(f"several tracks are named '{ref}'; use the number: "
                           + ", ".join(t.label() for t in exact))
        prefix = [t for t in self.tracks if t.name.lower().startswith(want)]
        if len(prefix) == 1:
            return prefix[0]
        raise KeyError(f"no track '{ref}'. Tracks: " + ", ".join(t.label() for t in self.tracks))

    @property
    def bar_count(self) -> int:
        return len(self.masterbars)

    def mb(self, bar: int):
        """1-based bar number -> MasterBar element"""
        if not 1 <= bar <= len(self.masterbars):
            raise IndexError(f"no bar {bar} (score has {len(self.masterbars)} bars)")
        return self.masterbars[bar - 1]

    def time(self, bar: int) -> str:
        return child_text(self.mb(bar), "Time", "4/4")

    def bar_len(self, bar: int) -> Fraction:
        return bar_length(self.time(bar))

    def key_flats(self, bar: int) -> bool:
        return int(child_text(self.mb(bar), "Key/AccidentalCount", "0") or 0) < 0

    def bar_el(self, track: Track, bar: int, staff: int = 1):
        ids = _ids(child_text(self.mb(bar), "Bars"))
        col = track.column + staff - 1
        if col >= len(ids):
            raise IndexError(f"bar {bar} has no staff {staff} for {track.label()}")
        return self.bars[ids[col]]

    def voice_ids(self, track: Track, bar: int, staff: int = 1) -> list[str]:
        ids = _ids(child_text(self.bar_el(track, bar, staff), "Voices"))
        return ids + ["-1"] * (4 - len(ids))

    def beat_ids(self, voice_id: str) -> list[str]:
        return _ids(child_text(self.voices[voice_id], "Beats")) if voice_id != "-1" else []

    def note_ids(self, beat_id: str) -> list[str]:
        return _ids(child_text(self.beats[beat_id], "Notes"))

    def rhythm_of_el(self, el) -> Rhythm:
        dot = el.find("AugmentationDot")
        tup = el.find("PrimaryTuplet")
        return Rhythm(child_text(el, "NoteValue", "Quarter"),
                      int(dot.get("count") or 1) if dot is not None else 0,
                      (int(tup.get("num")), int(tup.get("den"))) if tup is not None else None)

    def beat_rhythm(self, beat_id: str) -> Rhythm:
        r = self.beats[beat_id].find("Rhythm")
        return self.rhythm_of_el(self.rhythms[r.get("ref")]) if r is not None else Rhythm()

    def is_grace(self, beat_id: str) -> bool:
        return self.beats[beat_id].find("GraceNotes") is not None

    def positioned_beats(self, voice_id: str) -> list[tuple[str, Fraction, Fraction]]:
        """[(beat id, start in quarters, length)] ; grace beats get length 0 and the start of the next beat."""
        out, pos = [], Fraction(0)
        for bid in self.beat_ids(voice_id):
            if self.is_grace(bid):
                out.append((bid, pos, Fraction(0)))
                continue
            d = self.beat_rhythm(bid).length
            out.append((bid, pos, d))
            pos += d
        return out

    def voice_length(self, voice_id: str) -> Fraction:
        return sum((d for _, _, d in self.positioned_beats(voice_id)), Fraction(0))

    # ---- note facts ---------------------------------------------------------
    def note_prop(self, note_el, name: str, sub: str) -> str | None:
        el = note_el.find(f"Properties/Property[@name='{name}']/{sub}")
        return el.text if el is not None else None

    def note_has_prop(self, note_el, name: str) -> bool:
        return note_el.find(f"Properties/Property[@name='{name}']") is not None

    def note_string_fret(self, note_el) -> tuple[int, int] | None:
        s, f = self.note_prop(note_el, "String", "String"), self.note_prop(note_el, "Fret", "Fret")
        if s is None or f is None:
            return None
        return int(s), int(f)

    def note_midi(self, note_el) -> int | None:
        m = self.note_prop(note_el, "Midi", "Number")
        return int(m) if m is not None else None

    def note_articulation(self, note_el) -> int | None:
        t = child_text(note_el, "InstrumentArticulation")
        return int(t) if t.lstrip("-").isdigit() else None

    # ---- copy-on-write --------------------------------------------------------
    def own_voice(self, track: Track, bar: int, slot: int, staff: int = 1, create: bool = True) -> str | None:
        """Voice id in `slot` (0-3) of the bar, private to that bar. Creates an empty voice if asked."""
        bar_el = self.own_bar(track, bar, staff)
        ids = _ids(child_text(bar_el, "Voices"))
        ids += ["-1"] * (4 - len(ids))
        old = ids[slot]
        if old == "-1":
            if not create:
                return None
            new = self.new_id("Voices")
            v = etree.SubElement(self.pools["Voices"], "Voice", id=new)
            etree.SubElement(v, "Beats").text = ""
            self.voices[new] = v
        elif self.refs["Voices"][old] > 1:
            clone = copy.deepcopy(self.voices[old])
            new = self.new_id("Voices")
            clone.set("id", new)
            self.pools["Voices"].append(clone)
            self.voices[new] = clone
            self._retain("Beats", self.beat_ids(new))
            self._release("Voices", [old])
        else:
            return old
        ids[slot] = new
        self._retain("Voices", [new])
        bar_el.find("Voices").text = " ".join(ids)
        return new

    def own_bar(self, track: Track, bar: int, staff: int = 1):
        mb = self.mb(bar)
        ids = _ids(child_text(mb, "Bars"))
        col = track.column + staff - 1
        bid = ids[col]
        if self.refs["Bars"][bid] <= 1:
            return self.bars[bid]
        clone = copy.deepcopy(self.bars[bid])
        new = self.new_id("Bars")
        clone.set("id", new)
        self.pools["Bars"].append(clone)
        self.bars[new] = clone
        self._retain("Voices", [v for v in _ids(child_text(clone, "Voices")) if v != "-1"])
        self._release("Bars", [bid])
        self._retain("Bars", [new])
        ids[col] = new
        mb.find("Bars").text = " ".join(ids)
        return clone

    def own_beat(self, voice_id: str, index: int) -> str:
        """Beat at `index` of a voice, cloned first if it's referenced anywhere else."""
        v = self.voices[voice_id]
        ids = self.beat_ids(voice_id)
        bid = ids[index]
        if self.refs["Beats"][bid] <= 1:
            return bid
        clone = copy.deepcopy(self.beats[bid])
        new = self.new_id("Beats")
        clone.set("id", new)
        self.pools["Beats"].append(clone)
        self.beats[new] = clone
        self._retain("Notes", self.note_ids(new))
        r = clone.find("Rhythm")
        if r is not None:
            self._retain("Rhythms", [r.get("ref")])
        self._release("Beats", [bid])
        self._retain("Beats", [new])
        ids[index] = new
        v.find("Beats").text = " ".join(ids)
        return new

    def own_note(self, beat_id: str, index: int) -> str:
        """Note at `index` of a beat (which must already be owned), cloned if shared."""
        b = self.beats[beat_id]
        ids = self.note_ids(beat_id)
        nid = ids[index]
        if self.refs["Notes"][nid] <= 1:
            return nid
        clone = copy.deepcopy(self.notes[nid])
        new = self.new_id("Notes")
        clone.set("id", new)
        self.pools["Notes"].append(clone)
        self.notes[new] = clone
        self._release("Notes", [nid])
        self._retain("Notes", [new])
        ids[index] = new
        b.find("Notes").text = " ".join(ids)
        return new

    # ---- building new objects -------------------------------------------------
    def rhythm_id(self, rhythm: Rhythm) -> str:
        if rhythm in self._rhythm_index:
            return self._rhythm_index[rhythm]
        rid = self.new_id("Rhythms")
        el = etree.SubElement(self.pools["Rhythms"], "Rhythm", id=rid)
        etree.SubElement(el, "NoteValue").text = rhythm.value
        if rhythm.dots:
            etree.SubElement(el, "AugmentationDot", count=str(rhythm.dots))
        if rhythm.tuplet:
            etree.SubElement(el, "PrimaryTuplet", num=str(rhythm.tuplet[0]), den=str(rhythm.tuplet[1]))
        self.rhythms[rid] = el
        self._rhythm_index[rhythm] = rid
        return rid

    def set_beat_rhythm(self, beat_id: str, rhythm: Rhythm):
        b = self.beats[beat_id]
        r = b.find("Rhythm")
        new = self.rhythm_id(rhythm)
        if r is None:
            r = insert_ordered(b, etree.Element("Rhythm"), BEAT_ORDER)
        else:
            self._release("Rhythms", [r.get("ref")])
        r.set("ref", new)
        self._retain("Rhythms", [new])

    def template_beat(self, track: Track):
        """Some existing beat of this track, to copy house-keeping children (Properties) from."""
        for bar in range(1, self.bar_count + 1):
            for vid in self.voice_ids(track, bar):
                for bid in self.beat_ids(vid):
                    return self.beats[bid]
        return None

    def new_beat(self, track: Track, rhythm: Rhythm, dynamic: str = DEFAULT_DYNAMIC) -> str:
        bid = self.new_id("Beats")
        b = etree.SubElement(self.pools["Beats"], "Beat", id=bid)
        etree.SubElement(b, "Dynamic").text = dynamic
        rid = self.rhythm_id(rhythm)
        etree.SubElement(b, "Rhythm", ref=rid)
        self._retain("Rhythms", [rid])
        etree.SubElement(b, "TransposedPitchStemOrientation").text = "Undefined"
        etree.SubElement(b, "ConcertPitchStemOrientation").text = "Undefined"
        tpl = self.template_beat(track)
        if tpl is not None and tpl.find("Properties") is not None:
            b.append(copy.deepcopy(tpl.find("Properties")))
        self.beats[bid] = b
        return bid

    def set_beat_notes(self, beat_id: str, note_ids: list[str]):
        b = self.beats[beat_id]
        old = self.note_ids(beat_id)
        self._release("Notes", old)
        self._retain("Notes", note_ids)
        if note_ids:
            set_child(b, "Notes", " ".join(note_ids), BEAT_ORDER)
        else:
            remove_child(b, "Notes")

    def set_voice_beats(self, voice_id: str, beat_ids: list[str]):
        v = self.voices[voice_id]
        self._release("Beats", self.beat_ids(voice_id))
        self._retain("Beats", beat_ids)
        el = v.find("Beats")
        if el is None:
            el = etree.SubElement(v, "Beats")
        el.text = " ".join(beat_ids)

    def clear_voice(self, track: Track, bar: int, slot: int, staff: int = 1):
        bar_el = self.own_bar(track, bar, staff)
        ids = _ids(child_text(bar_el, "Voices"))
        ids += ["-1"] * (4 - len(ids))
        if ids[slot] != "-1":
            self._release("Voices", [ids[slot]])
            ids[slot] = "-1"
            bar_el.find("Voices").text = " ".join(ids)

    def _drum_string(self, art: int) -> str:
        counts = Counter()
        fallback = Counter()
        for n in self.notes.values():
            s = self.note_prop(n, "String", "String")
            if s is None or self.note_articulation(n) is None or self.note_prop(n, "Midi", "Number") is None:
                continue
            if self.note_articulation(n) == art:
                counts[s] += 1
            fallback[s] += 1
        if counts:
            return counts.most_common(1)[0][0]
        return fallback.most_common(1)[0][0] if fallback else "0"

    def _fret_offset(self, track: Track) -> int:
        """Midi - tuning[string] - fret as found on existing notes of the track (normally 0, or the capo)."""
        seen = Counter()
        for bar in range(1, self.bar_count + 1):
            for vid in self.voice_ids(track, bar):
                for bid in self.beat_ids(vid):
                    for nid in self.note_ids(bid):
                        n = self.notes[nid]
                        sf, m = self.note_string_fret(n), self.note_midi(n)
                        if sf and m is not None and sf[0] < track.strings:
                            seen[m - track.tuning[sf[0]] - sf[1]] += 1
                if len(seen) and sum(seen.values()) > 20:
                    return seen.most_common(1)[0][0]
        return seen.most_common(1)[0][0] if seen else track.capo

    def _pitch_property(self, props, name: str, midi: int | None, flats: bool):
        p = etree.SubElement(props, "Property", name=name)
        pitch = etree.SubElement(p, "Pitch")
        if midi is None:
            step, acc, octave = "C", "", -1
        else:
            step, acc, octave = spell(midi, flats)
        etree.SubElement(pitch, "Step").text = step
        etree.SubElement(pitch, "Accidental").text = acc or None
        etree.SubElement(pitch, "Octave").text = str(octave)

    def new_note(self, track: Track, bar: int, *, string: int | None = None, fret: int | None = None,
                 articulation: int | None = None) -> str:
        """A plain note. Fretted: `string` is the GPIF index (0 = lowest string). Drums: `articulation` index."""
        nid = self.new_id("Notes")
        n = etree.SubElement(self.pools["Notes"], "Note", id=nid)
        props = etree.Element("Properties")
        flats = self.key_flats(bar)
        if track.kind == "drums":
            art = track.articulations[articulation]
            etree.SubElement(n, "InstrumentArticulation").text = str(articulation)
            self._pitch_property(props, "ConcertPitch", None, flats)
            _simple(props, "Fret", "Fret", str(art.midi))
            _simple(props, "Midi", "Number", str(art.midi))
            _simple(props, "String", "String", self._drum_string(articulation))
            self._pitch_property(props, "TransposedPitch", None, flats)
        else:
            midi = track.tuning[string] + self._fret_offset(track) + fret
            etree.SubElement(n, "InstrumentArticulation").text = "0"
            self._pitch_property(props, "ConcertPitch", midi, flats)
            _simple(props, "Fret", "Fret", str(fret))
            _simple(props, "Midi", "Number", str(midi))
            _simple(props, "String", "String", str(string))
            self._pitch_property(props, "TransposedPitch", midi - track.transpose, flats)
        n.append(props)
        self.notes[nid] = n
        return nid

    def log(self, message: str):
        self.changes.append(message)


def _simple(props, name: str, tag: str, value: str | None):
    p = etree.SubElement(props, "Property", name=name)
    etree.SubElement(p, tag).text = value
    return p
