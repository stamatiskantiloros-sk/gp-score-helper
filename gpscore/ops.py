"""Editing operations. Every operation takes a Score and a dict of parameters (the same dicts
that go into an ops file for `gpscore apply`), changes the score and logs what it did.

Bars, tracks, voices, strings and beat positions are all 1-based, as in Guitar Pro.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from fractions import Fraction

from lxml import etree

from .music import ACCENT_BITS, DEFAULT_DYNAMIC, DYNAMICS, HAIRPINS, Rhythm, bar_length
from .notation import (BeatSpec, DrumKit, NoteSpec, WRITABLE_FLAGS, note_flags, parse_bars, parse_voice)
from .score import (BEAT_ORDER, MASTERBAR_ORDER, NOTE_ORDER, Score, Track, _ids, child_text, insert_ordered,
                    remove_child, set_child)
from .view import parse_range, tempo_points


class OpError(ValueError):
    pass


# ---------------------------------------------------------------- selection

def _int_list(v, lo: int | None = None, hi: int | None = None) -> list[int] | None:
    if v is None:
        return None
    if isinstance(v, int):
        return [v]
    if isinstance(v, list):
        return [int(x) for x in v]
    return parse_range(str(v), hi or 10_000)


def _positions(v) -> list[Fraction] | None:
    """Beat positions in the bar's own beat unit, 1-based: '1,3', '2.5' (the 'and' of 2), [2, 4]."""
    if v is None:
        return None
    items = v if isinstance(v, list) else str(v).replace(" ", "").split(",")
    return [Fraction(str(x)) for x in items if str(x)]


@dataclass
class BeatRef:
    bar: int
    slot: int          # 0-3
    index: int         # position in the voice's beat list
    beat_id: str
    pos: Fraction      # in quarters from the start of the bar


def select_beats(score: Score, p: dict) -> tuple[Track, list[BeatRef]]:
    track = score.track(p["track"])
    bars = parse_range(p.get("bars"), score.bar_count)
    voices = _int_list(p.get("voices") or p.get("voice")) or [1, 2, 3, 4]
    staff = int(p.get("staff", 1))
    on = _positions(p.get("on"))
    out = []
    for bar in bars:
        num, den = (int(x) for x in score.time(bar).split("/"))
        unit = Fraction(4, den)
        for slot, vid in enumerate(score.voice_ids(track, bar, staff)):
            if slot + 1 not in voices or vid == "-1":
                continue
            for i, (bid, pos, _) in enumerate(score.positioned_beats(vid)):
                if on is not None and (pos / unit + 1) not in on:
                    continue
                out.append(BeatRef(bar, slot, i, bid, pos))
    return track, out


def _note_matches(score: Score, track: Track, n, p: dict, kit: DrumKit | None) -> bool:
    strings = _int_list(p.get("strings") or p.get("string"))
    if strings is not None and track.kind == "fretted":
        sf = score.note_string_fret(n)
        if sf is None or (track.strings - sf[0]) not in strings:
            return False
    drums = p.get("drums") or p.get("drum")
    if drums is not None and track.kind == "drums":
        wanted = {kit.index(d) for d in (drums if isinstance(drums, list) else str(drums).split(","))}
        if score.note_articulation(n) not in wanted:
            return False
    frets = _int_list(p.get("frets") or p.get("fret"))
    if frets is not None:
        sf = score.note_string_fret(n)
        if sf is None or sf[1] not in frets:
            return False
    has = p.get("has")   # only notes that already carry this flag
    if has is not None and has not in note_flags(score, n):
        return False
    return True


def _owned_beat(score: Score, track: Track, ref: BeatRef, staff: int) -> str:
    vid = score.own_voice(track, ref.bar, ref.slot, staff, create=False)
    return score.own_beat(vid, ref.index)


# ---------------------------------------------------------------- note flags

def _set_enable_prop(n, name: str, on: bool):
    props = n.find("Properties")
    if props is None:
        props = insert_ordered(n, etree.Element("Properties"), NOTE_ORDER)
    existing = props.find(f"Property[@name='{name}']")
    if on and existing is None:
        new = etree.Element("Property", name=name)
        etree.SubElement(new, "Enable")
        for i, p in enumerate(props):
            if (p.get("name") or "") > name:
                props.insert(i, new)
                break
        else:
            props.append(new)
    elif not on and existing is not None:
        props.remove(existing)


def _set_value_prop(n, name: str, tag: str, value: str | None):
    props = n.find("Properties")
    existing = props.find(f"Property[@name='{name}']")
    if existing is not None:
        props.remove(existing)
    if value is None:
        return
    new = etree.Element("Property", name=name)
    etree.SubElement(new, tag).text = value
    for i, p in enumerate(props):
        if (p.get("name") or "") > name:
            props.insert(i, new)
            break
    else:
        props.append(new)


def set_flag(n, flag: str, on: bool):
    if flag in ACCENT_BITS:
        bits = int(child_text(n, "Accent", "0") or 0)
        if on:
            if flag == ">":
                bits &= ~4
            if flag == "^":
                bits &= ~8
            bits |= ACCENT_BITS[flag]
            if flag in (">", "^"):
                remove_child(n, "AntiAccent")
        else:
            bits &= ~ACCENT_BITS[flag]
        if bits:
            set_child(n, "Accent", str(bits), NOTE_ORDER)
        else:
            remove_child(n, "Accent")
    elif flag == "g":
        if on:
            set_child(n, "AntiAccent", "Normal", NOTE_ORDER)
            bits = int(child_text(n, "Accent", "0") or 0) & ~(8 | 4)
            if bits:
                set_child(n, "Accent", str(bits), NOTE_ORDER)
            else:
                remove_child(n, "Accent")
        else:
            remove_child(n, "AntiAccent")
    elif flag == "lr":
        if on and n.find("LetRing") is None:
            insert_ordered(n, etree.Element("LetRing"), NOTE_ORDER)
        elif not on:
            remove_child(n, "LetRing")
    elif flag in ("v", "vw"):
        if on:
            set_child(n, "Vibrato", "Wide" if flag == "vw" else "Slight", NOTE_ORDER)
        else:
            remove_child(n, "Vibrato")
    elif flag == "pm":
        _set_enable_prop(n, "PalmMuted", on)
    elif flag == "x":
        _set_enable_prop(n, "Muted", on)
    elif flag == "h":
        _set_enable_prop(n, "HopoOrigin", on)
    elif flag in ("sl", "lsl"):
        _set_value_prop(n, "Slide", "Flags", ("1" if flag == "sl" else "2") if on else None)
    else:
        raise OpError(f"can't set flag '{flag}'")


def _hopo_destination(score: Score, track: Track, bar: int, slot: int, index: int, string: int,
                      on: bool, staff: int):
    """Mark the next note on the same string (same voice, possibly next bar) as hammer/pull destination."""
    vid = score.voice_ids(track, bar, staff)[slot]
    bids = score.beat_ids(vid)
    targets = [(bar, slot, i) for i in range(index + 1, len(bids))]
    if bar < score.bar_count:
        nv = score.voice_ids(track, bar + 1, staff)[slot]
        if nv != "-1" and score.beat_ids(nv):
            targets.append((bar + 1, slot, 0))
    for b, s, i in targets[:1]:
        v = score.own_voice(track, b, s, staff, create=False)
        bid = score.beat_ids(v)[i]
        for j, nid in enumerate(score.note_ids(bid)):
            sf = score.note_string_fret(score.notes[nid])
            if sf and sf[0] == string:
                bid = score.own_beat(v, i)
                nid = score.own_note(bid, j)
                _set_enable_prop(score.notes[nid], "HopoDestination", on)
                return


def op_flags(score: Score, p: dict):
    """Add/remove note flags on selected notes.
    {op: flags, track, bars, [voices], [on: "2,4"], [strings|drums|frets|has], add: [..], remove: [..]}"""
    add = p.get("add") or []
    remove = p.get("remove") or []
    add = add if isinstance(add, list) else [add]
    remove = remove if isinstance(remove, list) else [remove]
    for f in add + remove:
        if f not in WRITABLE_FLAGS:
            raise OpError(f"unknown flag '{f}'; use one of {sorted(WRITABLE_FLAGS)}")
    track, refs = select_beats(score, p)
    kit = DrumKit(track) if track.kind == "drums" else None
    staff = int(p.get("staff", 1))
    count = 0
    for ref in refs:
        bid = score.beat_ids(score.voice_ids(track, ref.bar, staff)[ref.slot])[ref.index]
        matching = [j for j, nid in enumerate(score.note_ids(bid))
                    if _note_matches(score, track, score.notes[nid], p, kit)]
        if not matching:
            continue
        bid = _owned_beat(score, track, ref, staff)
        for j in matching:
            nid = score.own_note(bid, j)
            n = score.notes[nid]
            for f in remove:
                set_flag(n, f, False)
                if f == "h" and score.note_string_fret(n):
                    _hopo_destination(score, track, ref.bar, ref.slot, ref.index, score.note_string_fret(n)[0],
                                      False, staff)
            for f in add:
                set_flag(n, f, True)
                if f == "h" and score.note_string_fret(n):
                    _hopo_destination(score, track, ref.bar, ref.slot, ref.index, score.note_string_fret(n)[0],
                                      True, staff)
            count += 1
    score.log(f"flags {track.label()} bars {p.get('bars', 'all')}: +{add} -{remove} on {count} notes")
    return count


# ---------------------------------------------------------------- beat-level

def op_dynamic(score: Score, p: dict):
    """{op: dynamic, track, bars, [voices], [on], value: "mf"}  - sets the dynamic of every selected beat."""
    value = str(p["value"]).upper()
    if value not in DYNAMICS:
        raise OpError(f"dynamic must be one of {[d.lower() for d in DYNAMICS]}")
    track, refs = select_beats(score, p)
    staff = int(p.get("staff", 1))
    n = 0
    for ref in refs:
        bid = score.beat_ids(score.voice_ids(track, ref.bar, staff)[ref.slot])[ref.index]
        if child_text(score.beats[bid], "Dynamic") == value:
            continue
        bid = _owned_beat(score, track, ref, staff)
        set_child(score.beats[bid], "Dynamic", value, BEAT_ORDER)
        n += 1
    score.log(f"dynamic {value.lower()} on {track.label()} bars {p.get('bars')}: {n} beats changed")
    return n


def op_hairpin(score: Score, p: dict):
    """{op: hairpin, track, bars, [voices], [on], value: cresc|dim|none}"""
    value = str(p["value"]).lower()
    if value not in (*HAIRPINS, "none"):
        raise OpError("hairpin value must be cresc, dim or none")
    track, refs = select_beats(score, p)
    staff = int(p.get("staff", 1))
    for ref in refs:
        bid = _owned_beat(score, track, ref, staff)
        if value == "none":
            remove_child(score.beats[bid], "Hairpin")
        else:
            set_child(score.beats[bid], "Hairpin", HAIRPINS[value], BEAT_ORDER)
    score.log(f"hairpin {value} on {track.label()} bars {p.get('bars')} ({len(refs)} beats)")
    return len(refs)


def op_drum_swap(score: Score, p: dict):
    """{op: drum_swap, track, bars, [voices], [on], from: "HHc", to: "RD"} - change the drum piece."""
    track, refs = select_beats(score, p)
    if track.kind != "drums":
        raise OpError(f"{track.label()} is not a drum track")
    kit = DrumKit(track)
    src, dst = kit.index(p["from"]), kit.index(p["to"])
    art = track.articulations[dst]
    staff = int(p.get("staff", 1))
    n = 0
    for ref in refs:
        bid = score.beat_ids(score.voice_ids(track, ref.bar, staff)[ref.slot])[ref.index]
        idx = [j for j, nid in enumerate(score.note_ids(bid)) if score.note_articulation(score.notes[nid]) == src]
        if not idx:
            continue
        bid = _owned_beat(score, track, ref, staff)
        for j in idx:
            note = score.notes[score.own_note(bid, j)]
            note.find("InstrumentArticulation").text = str(dst)
            for name, tag in (("Fret", "Fret"), ("Midi", "Number")):
                el = note.find(f"Properties/Property[@name='{name}']/{tag}")
                if el is not None:
                    el.text = str(art.midi)
            n += 1
    score.log(f"drum_swap {p['from']}→{p['to']} on {track.label()} bars {p.get('bars')}: {n} notes")
    return n


# ---------------------------------------------------------------- writing bars

def _prev_beat_dynamic(score: Score, track: Track, bar: int, slot: int, staff: int) -> str:
    for b in range(bar - 1, 0, -1):
        bids = score.beat_ids(score.voice_ids(track, b, staff)[slot])
        if bids:
            return child_text(score.beats[bids[-1]], "Dynamic", DEFAULT_DYNAMIC)
    return DEFAULT_DYNAMIC


def _apply_new_note_flags(n, flags: list[str]):
    for f in flags:
        set_flag(n, f, True)


def _link_tie(score: Score, track: Track, bar: int, slot: int, staff: int, beat_index: int, note_id: str,
              spec: NoteSpec):
    """Set Tie origin on the previous matching note and Tie destination on this one."""
    vid = score.voice_ids(track, bar, staff)[slot]
    candidates = []
    if beat_index > 0:
        candidates.append((bar, beat_index - 1))
    elif bar > 1:
        pv = score.voice_ids(track, bar - 1, staff)[slot]
        if pv != "-1" and score.beat_ids(pv):
            candidates.append((bar - 1, len(score.beat_ids(pv)) - 1))
    for b, i in candidates:
        v = score.own_voice(track, b, slot, staff, create=False)
        bid = score.beat_ids(v)[i]
        for j, nid in enumerate(score.note_ids(bid)):
            n = score.notes[nid]
            same = (score.note_articulation(n) == spec.art) if track.kind == "drums" else \
                (score.note_string_fret(n) or (None,))[0] == spec.string
            if same:
                bid = score.own_beat(v, i)
                prev = score.notes[score.own_note(bid, j)]
                tie = prev.find("Tie")
                dest = tie.get("destination") if tie is not None else "false"
                if tie is None:
                    tie = insert_ordered(prev, etree.Element("Tie"), NOTE_ORDER)
                tie.set("origin", "true")
                tie.set("destination", dest or "false")
                me = score.notes[note_id]
                t2 = insert_ordered(me, etree.Element("Tie"), NOTE_ORDER)
                t2.set("origin", "false")
                t2.set("destination", "true")
                return True
    raise OpError(f"bar {bar}: tied note '~' has no matching note right before it")


def _key(score: Score, track: Track, n):
    return score.note_articulation(n) if track.kind == "drums" else (score.note_string_fret(n) or (None,))[0]


def _fix_tie_into_next_bar(score: Score, track: Track, bar: int, slot: int, staff: int):
    """After rewriting a bar, notes in the next bar that were tied from it either get re-linked to a
    matching new note (same string/drum) or stop being tied."""
    if bar >= score.bar_count:
        return
    nv = score.voice_ids(track, bar + 1, staff)[slot]
    nbeats = score.beat_ids(nv) if nv != "-1" else []
    if not nbeats:
        return
    mine = score.beat_ids(score.voice_ids(track, bar, staff)[slot])
    last = mine[-1] if mine else None
    last_notes = {(_key(score, track, score.notes[n])): (j, n) for j, n in enumerate(score.note_ids(last))} \
        if last else {}
    first = nbeats[0]
    for j, nid in enumerate(score.note_ids(first)):
        n = score.notes[nid]
        tie = n.find("Tie")
        if tie is None or tie.get("destination") != "true":
            continue
        k = _key(score, track, n)
        if k in last_notes:
            v = score.own_voice(track, bar, slot, staff, create=False)
            lb = score.own_beat(v, len(mine) - 1)
            prev = score.notes[score.own_note(lb, last_notes[k][0])]
            t = prev.find("Tie")
            if t is None:
                t = insert_ordered(prev, etree.Element("Tie"), NOTE_ORDER)
                t.set("destination", "false")
            t.set("origin", "true")
            score.log(f"bar {bar + 1}: kept the tie from bar {bar} on {track.label()}")
        else:
            v = score.own_voice(track, bar + 1, slot, staff, create=False)
            b = score.own_beat(v, 0)
            n = score.notes[score.own_note(b, j)]
            t = n.find("Tie")
            if t.get("origin") == "true":
                t.set("destination", "false")
            else:
                n.remove(t)
            score.log(f"bar {bar + 1}: removed a tie from bar {bar} on {track.label()} "
                      f"(the note it came from was rewritten)")


def write_voice(score: Score, track: Track, bar: int, slot: int, specs: list[BeatSpec], staff: int = 1,
                allow_incomplete: bool = False):
    total = sum((s.rhythm.length for s in specs if not s.grace), Fraction(0))
    want = score.bar_len(bar)
    if specs and total != want and not allow_incomplete:
        raise OpError(f"bar {bar} v{slot + 1}: beats add up to {total} quarters but {score.time(bar)} needs {want}")
    if not specs:
        score.clear_voice(track, bar, slot, staff)
        return
    vid = score.own_voice(track, bar, slot, staff)
    dyn = _prev_beat_dynamic(score, track, bar, slot, staff)
    new_beats = []
    pending_ties = []
    for bi, spec in enumerate(specs):
        dyn = spec.dynamic or dyn
        bid = score.new_beat(track, spec.rhythm, dyn)
        b = score.beats[bid]
        if spec.hairpin:
            set_child(b, "Hairpin", spec.hairpin, BEAT_ORDER)
        if spec.grace:
            set_child(b, "GraceNotes", spec.grace, BEAT_ORDER)
        if spec.tremolo:
            set_child(b, "Tremolo", spec.tremolo, BEAT_ORDER)
        nids = []
        for ns in spec.notes:
            nid = score.new_note(track, bar, string=ns.string, fret=ns.fret, articulation=ns.art)
            _apply_new_note_flags(score.notes[nid], [f for f in ns.flags if f != "h"])
            nids.append(nid)
            if ns.tie:
                pending_ties.append((bi, nid, ns))
        score.set_beat_notes(bid, nids)
        new_beats.append(bid)
    score.set_voice_beats(vid, new_beats)
    for bi, nid, ns in pending_ties:
        _link_tie(score, track, bar, slot, staff, bi, nid, ns)
    _fix_tie_into_next_bar(score, track, bar, slot, staff)
    # hammer-ons need the destination note to exist, so do them last
    for bi, spec in enumerate(specs):
        for ns in spec.notes:
            if "h" in ns.flags and ns.string is not None:
                bid = score.beat_ids(score.voice_ids(track, bar, staff)[slot])[bi]
                for j, nid in enumerate(score.note_ids(bid)):
                    sf = score.note_string_fret(score.notes[nid])
                    if sf and sf[0] == ns.string:
                        set_flag(score.notes[nid], "h", True)
                _hopo_destination(score, track, bar, slot, bi, ns.string, True, staff)


def op_write(score: Score, p: dict):
    """{op: write, track, text: "<bars in view layout>", [staff], [allow_incomplete]}
    Replaces the voices given in the text; other voices of those bars are left alone.
    An empty voice line ('v2') clears that voice."""
    track = score.track(p["track"])
    kit = DrumKit(track) if track.kind == "drums" else None
    staff = int(p.get("staff", 1))
    bars = parse_bars(p["text"])
    if not bars:
        raise OpError("write: no 'b<N>' bars found in text")
    # parse everything first so that a typo doesn't leave a half-written score
    parsed = {}
    for bar, voices in bars.items():
        score.mb(bar)
        for v, text in voices.items():
            try:
                parsed[(bar, v)] = parse_voice(text, track, kit)
            except ValueError as e:
                raise OpError(f"bar {bar} v{v}: {e}") from None
    for (bar, v) in sorted(parsed):
        write_voice(score, track, bar, v - 1, parsed[(bar, v)], staff, bool(p.get("allow_incomplete")))
    score.log(f"write {track.label()} bars {sorted(bars)}")
    return len(parsed)


def op_copy(score: Score, p: dict):
    """{op: copy, track | tracks: [..] | "all", from: "9-12", to: 17, [times: 1], [voices]}
    Copies the content of bars `from` to the bars starting at `to` (repeated `times` times).
    The copy shares Guitar Pro objects with the original (like GP's own copy/paste);
    later edits on either side are copy-on-write, so they stay independent."""
    if p.get("tracks") in ("all", "*"):
        tracks = score.tracks
    elif p.get("tracks"):
        tracks = [score.track(t) for t in p["tracks"]]
    else:
        tracks = [score.track(p["track"])]
    src = parse_range(p["from"], score.bar_count)
    times = int(p.get("times", 1))
    start = int(p["to"])
    voices = _int_list(p.get("voices")) or [1, 2, 3, 4]
    dest = [start + i for i in range(len(src) * times)]
    if dest[-1] > score.bar_count:
        raise OpError(f"copy needs bars up to {dest[-1]} but the score has {score.bar_count}; "
                      f"use append_bars first")
    for i, d in enumerate(dest):
        s = src[i % len(src)]
        if score.time(s) != score.time(d):
            raise OpError(f"copy: bar {s} is {score.time(s)} but bar {d} is {score.time(d)}")
    for track in tracks:
        for st in range(1, track.staves + 1):
            for i, d in enumerate(dest):
                s = src[i % len(src)]
                src_ids = score.voice_ids(track, s, st)
                for slot in range(4):
                    if slot + 1 not in voices:
                        continue
                    if src_ids[slot] == "-1" or not score.beat_ids(src_ids[slot]):
                        score.clear_voice(track, d, slot, st)
                        continue
                    vid = score.own_voice(track, d, slot, st)
                    score.set_voice_beats(vid, score.beat_ids(src_ids[slot]))
    score.log(f"copy bars {p['from']} → {start}" + (f" x{times}" if times > 1 else "")
              + f" on {', '.join(t.label() for t in tracks)}")
    return len(dest)


def op_clear(score: Score, p: dict):
    """{op: clear, track, bars, [voices]}"""
    track = score.track(p["track"])
    voices = _int_list(p.get("voices")) or [1, 2, 3, 4]
    for bar in parse_range(p["bars"], score.bar_count):
        for st in range(1, track.staves + 1):
            for v in voices:
                score.clear_voice(track, bar, v - 1, st)
    score.log(f"clear {track.label()} bars {p['bars']} voices {voices}")


# ---------------------------------------------------------------- repeated riffs

def bar_signature(score: Score, track: Track, bar: int, with_flags: bool = False, staff: int = 1) -> tuple:
    """Content of a bar as a hashable value: per voice, per beat (rhythm, sorted notes)."""
    sig = []
    for vid in score.voice_ids(track, bar, staff):
        beats = []
        for bid in score.beat_ids(vid):
            notes = []
            for nid in score.note_ids(bid):
                n = score.notes[nid]
                key = (score.note_articulation(n) if track.kind == "drums" else score.note_string_fret(n),
                       child_text(n, "Tie") == "" and n.find("Tie") is not None and n.find("Tie").get("destination"))
                if with_flags:
                    key = key + (tuple(note_flags(score, n)),)
                notes.append(key)
            beats.append((score.beat_rhythm(bid), tuple(sorted(notes, key=repr))))
        sig.append(tuple(beats))
    return tuple(sig)


def patterns(score: Score, track: Track, staff: int = 1) -> list[dict]:
    """Groups of bars with the same notes and rhythm (ignoring articulations).
    'consistent' is False when members of a group differ in their articulations."""
    groups: dict[tuple, list[int]] = {}
    for bar in range(1, score.bar_count + 1):
        sig = bar_signature(score, track, bar, staff=staff)
        if not any(sig):
            continue
        groups.setdefault(sig, []).append(bar)
    out = []
    for bars in groups.values():
        if len(bars) < 2:
            continue
        variants: dict[tuple, list[int]] = {}
        for b in bars:
            variants.setdefault(bar_signature(score, track, b, True, staff), []).append(b)
        out.append({"bars": bars, "consistent": len(variants) == 1,
                    "variants": sorted(variants.values(), key=len, reverse=True)})
    out.sort(key=lambda g: g["bars"][0])
    return out


def op_propagate(score: Score, p: dict):
    """{op: propagate, track, from: 9, to: "matching" | "17-20,33", [flags: [...]], [staff]}
    Copies note flags (articulations) from one bar onto bars with the same notes and rhythm.
    Only the flags listed are copied (default: all writable ones); other flags are left alone."""
    track = score.track(p["track"])
    staff = int(p.get("staff", 1))
    src = int(p["from"])
    flags = p.get("flags") or sorted(WRITABLE_FLAGS - {"h"})
    sig = bar_signature(score, track, src, staff=staff)
    if p.get("to", "matching") == "matching":
        targets = [b for b in range(1, score.bar_count + 1)
                   if b != src and bar_signature(score, track, b, staff=staff) == sig]
    else:
        targets = parse_range(p["to"], score.bar_count)
        bad = [b for b in targets if bar_signature(score, track, b, staff=staff) != sig]
        if bad:
            raise OpError(f"propagate: bars {bad} don't have the same notes/rhythm as bar {src}")
    src_ids = score.voice_ids(track, src, staff)
    changed = 0
    for t in targets:
        for slot in range(4):
            sv = src_ids[slot]
            if sv == "-1":
                continue
            for i, sbid in enumerate(score.beat_ids(sv)):
                src_notes = [score.notes[n] for n in score.note_ids(sbid)]
                tv = score.voice_ids(track, t, staff)[slot]
                tbid = score.beat_ids(tv)[i]
                for j, tnid in enumerate(score.note_ids(tbid)):
                    tn = score.notes[tnid]
                    match = next((sn for sn in src_notes if
                                  (score.note_articulation(sn), score.note_string_fret(sn)) ==
                                  (score.note_articulation(tn), score.note_string_fret(tn))), None)
                    if match is None:
                        continue
                    want = set(note_flags(score, match)) & set(flags)
                    have = set(note_flags(score, tn)) & set(flags)
                    if want == have:
                        continue
                    v = score.own_voice(track, t, slot, staff, create=False)
                    b2 = score.own_beat(v, i)
                    tn = score.notes[score.own_note(b2, j)]
                    for f in have - want:
                        set_flag(tn, f, False)
                    for f in want - have:
                        set_flag(tn, f, True)
                    changed += 1
    score.log(f"propagate {track.label()} articulations of bar {src} → bars {targets}: {changed} notes changed")
    return targets


# ---------------------------------------------------------------- master bars

def op_section(score: Score, p: dict):
    """{op: section, bar, text: "Verse", [letter: "A"]}  or  {op: section, bar, remove: true}"""
    mb = score.mb(int(p["bar"]))
    remove_child(mb, "Section")
    if p.get("remove"):
        score.log(f"section removed at bar {p['bar']}")
        return
    sec = insert_ordered(mb, etree.Element("Section"), MASTERBAR_ORDER)
    etree.SubElement(sec, "Letter").text = etree.CDATA(str(p.get("letter", "")))
    etree.SubElement(sec, "Text").text = etree.CDATA(str(p.get("text", "")))
    score.log(f"section [{p.get('letter', '')}: {p.get('text', '')}] at bar {p['bar']}")


def op_repeat(score: Score, p: dict):
    """{op: repeat, start: 17, end: 20, [count: 2]}  (count = total plays)
    {op: repeat, start: 17, end: 20, remove: true} removes both marks."""
    start, end = int(p["start"]), int(p["end"])
    if end < start:
        raise OpError("repeat: end before start")
    for bar, which in ((start, "start"), (end, "end")):
        mb = score.mb(bar)
        rep = mb.find("Repeat")
        if rep is None:
            if p.get("remove"):
                continue
            rep = insert_ordered(mb, etree.Element("Repeat", start="false", end="false", count="0"),
                                 MASTERBAR_ORDER)
        rep.set(which, "false" if p.get("remove") else "true")
        if which == "end":
            rep.set("count", "0" if p.get("remove") else str(int(p.get("count", 2))))
        if rep.get("start") != "true" and rep.get("end") != "true":
            mb.remove(rep)
    score.log(("remove repeat " if p.get("remove") else "repeat ") + f"bars {start}-{end}"
              + ("" if p.get("remove") else f" x{p.get('count', 2)}"))


def op_ending(score: Score, p: dict):
    """{op: ending, bar, numbers: [1, 2]}  ([] removes)"""
    mb = score.mb(int(p["bar"]))
    nums = p.get("numbers") or []
    remove_child(mb, "AlternateEndings")
    if nums:
        set_child(mb, "AlternateEndings", " ".join(str(int(n)) for n in nums), MASTERBAR_ORDER)
    score.log(f"ending {nums} at bar {p['bar']}")


def _automations(score: Score):
    mt = score.root.find("MasterTrack")
    au = mt.find("Automations")
    if au is None:
        au = etree.SubElement(mt, "Automations")
    return au


def _tempo_el(bar: int, pos: float, bpm: float, linear: bool, text: str | None):
    a = etree.Element("Automation")
    for tag, val in (("Type", "Tempo"), ("Linear", "true" if linear else "false"), ("Bar", str(bar - 1)),
                     ("Position", f"{pos:g}"), ("Visible", "true")):
        etree.SubElement(a, tag).text = val
    if text:
        etree.SubElement(a, "Text").text = text
    v = etree.SubElement(a, "Value")
    v.text = f"{bpm:g} 2"
    return a


def _place_tempo(score: Score, el):
    au = _automations(score)
    key = (int(child_text(el, "Bar")), float(child_text(el, "Position")))
    for i, a in enumerate(au.findall("Automation")):
        if child_text(a, "Type") != "Tempo":
            continue
        if (int(child_text(a, "Bar")), float(child_text(a, "Position") or 0)) > key:
            a.addprevious(el)
            return
    au.append(el)


def op_tempo(score: Score, p: dict):
    """{op: tempo, bar, bpm, [pos: 0.0-1.0 within the bar], [ramp: true = glide to the next tempo point],
        [text: "Moderate"]}   or {op: tempo, bar, [pos], remove: true}. bpm counts quarter notes."""
    bar, pos = int(p["bar"]), float(p.get("pos", 0))
    score.mb(bar)
    for t in tempo_points(score):
        if t["bar"] == bar and abs(t["pos"] - pos) < 1e-9:
            t["el"].getparent().remove(t["el"])
    if p.get("remove"):
        score.log(f"tempo removed at bar {bar}")
        return
    _place_tempo(score, _tempo_el(bar, pos, float(p["bpm"]), bool(p.get("ramp")), p.get("text")))
    score.log(f"tempo q={p['bpm']} at bar {bar}" + (" (ramp to next)" if p.get("ramp") else ""))


def op_tempo_ramp(score: Score, p: dict):
    """{op: tempo_ramp, from: 19, to: 21, from_bpm: 120, to_bpm: 160}
    accelerando/ritardando: glides from the start of bar `from` to reach to_bpm at the start of bar `to`."""
    op_tempo(score, {"bar": p["from"], "bpm": p["from_bpm"], "ramp": True, "text": p.get("text")})
    op_tempo(score, {"bar": p["to"], "bpm": p["to_bpm"]})


def op_time(score: Score, p: dict):
    """{op: time, bars, value: "7/8"} - only changes the signature; rewrite the content to fit (check reports misfits)."""
    num, den = (int(x) for x in str(p["value"]).split("/"))
    if den not in (1, 2, 4, 8, 16, 32):
        raise OpError("time: denominator must be a power of 2")
    for bar in parse_range(p["bars"], score.bar_count):
        set_child(score.mb(bar), "Time", f"{num}/{den}", MASTERBAR_ORDER)
    score.log(f"time {num}/{den} on bars {p['bars']}")


def op_append_bars(score: Score, p: dict):
    """{op: append_bars, count: 8, [time: "4/4"]} - empty bars at the end of the score."""
    count = int(p.get("count", 1))
    last = score.masterbars[-1]
    first_new = score.bar_count + 1
    for _ in range(count):
        mb = etree.Element("MasterBar")
        for tag in ("Key", "Time"):
            el = last.find(tag)
            if el is not None:
                mb.append(copy.deepcopy(el))
        if p.get("time"):
            set_child(mb, "Time", str(p["time"]), MASTERBAR_ORDER)
        ids = []
        for old_id in _ids(child_text(last, "Bars")):
            old = score.bars[old_id]
            bid = score.new_id("Bars")
            b = etree.SubElement(score.pools["Bars"], "Bar", id=bid)
            clef = old.find("Clef")
            if clef is not None:
                b.append(copy.deepcopy(clef))
            etree.SubElement(b, "Voices").text = "-1 -1 -1 -1"
            score.bars[bid] = b
            score.refs["Bars"][bid] += 1
            ids.append(bid)
        etree.SubElement(mb, "Bars").text = " ".join(ids)
        xp = last.find("XProperties")
        if xp is not None:
            mb.append(copy.deepcopy(xp))
        last.addnext(mb)
        score.masterbars.append(mb)
        last = mb
    score.log(f"append {count} bars ({first_new}-{score.bar_count})")


# ---------------------------------------------------------------- registry

OPS = {
    "write": op_write, "copy": op_copy, "clear": op_clear,
    "flags": op_flags, "dynamic": op_dynamic, "hairpin": op_hairpin, "drum_swap": op_drum_swap,
    "propagate": op_propagate,
    "section": op_section, "repeat": op_repeat, "ending": op_ending,
    "tempo": op_tempo, "tempo_ramp": op_tempo_ramp, "time": op_time, "append_bars": op_append_bars,
}


def apply_ops(score: Score, ops: list[dict]):
    for i, p in enumerate(ops, 1):
        name = p.get("op")
        if name not in OPS:
            raise OpError(f"op #{i}: unknown op '{name}'. Known: {', '.join(OPS)}")
        try:
            OPS[name](score, p)
        except (KeyError, IndexError, ValueError) as e:
            msg = e.args[0] if e.args else repr(e)
            raise OpError(f"op #{i} ({name}): {msg}") from None
