"""Integrity checks run after every edit (and on demand with `gpscore check`)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction

from .score import Score, _ids, child_text


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self, limit: int = 40) -> str:
        lines = []
        for kind, items in (("ERROR", self.errors), ("warning", self.warnings)):
            for msg in items[:limit]:
                lines.append(f"{kind}: {msg}")
            if len(items) > limit:
                lines.append(f"... and {len(items) - limit} more {kind.lower()}s")
        return "\n".join(lines) or "OK: no problems found"


def check(score: Score, strict_lengths: bool = False) -> Report:
    r = Report()
    # duplicate ids
    for pool, tag in (("Bars", "Bar"), ("Voices", "Voice"), ("Beats", "Beat"), ("Notes", "Note"), ("Rhythms", "Rhythm")):
        c = Counter(e.get("id") for e in score.pools[pool].findall(tag))
        dup = [i for i, n in c.items() if n > 1]
        if dup:
            r.errors.append(f"duplicate {tag} ids: {dup[:10]}")
    staves = sum(t.staves for t in score.tracks)
    for i, mb in enumerate(score.masterbars, 1):
        ids = _ids(child_text(mb, "Bars"))
        if len(ids) != staves:
            r.errors.append(f"bar {i}: {len(ids)} staff entries, tracks need {staves}")
        for bid in ids:
            if bid not in score.bars:
                r.errors.append(f"bar {i}: missing Bar {bid}")
                continue
            for vid in _ids(child_text(score.bars[bid], "Voices")):
                if vid != "-1" and vid not in score.voices:
                    r.errors.append(f"bar {i}: missing Voice {vid}")
                    continue
                for beat in score.beat_ids(vid) if vid != "-1" else []:
                    if beat not in score.beats:
                        r.errors.append(f"bar {i}: missing Beat {beat}")
                        continue
                    rh = score.beats[beat].find("Rhythm")
                    if rh is None or rh.get("ref") not in score.rhythms:
                        r.errors.append(f"bar {i}: Beat {beat} has no valid Rhythm")
                    for n in score.note_ids(beat):
                        if n not in score.notes:
                            r.errors.append(f"bar {i}: missing Note {n}")
        try:
            num, den = (int(x) for x in score.time(i).split("/"))
            assert num > 0 and den in (1, 2, 4, 8, 16, 32)
        except Exception:
            r.errors.append(f"bar {i}: bad time signature '{score.time(i)}'")
    if r.errors:
        return r
    # musical sanity
    for t in score.tracks:
        for st in range(1, t.staves + 1):
            for bar in range(1, score.bar_count + 1):
                want = score.bar_len(bar)
                for slot, vid in enumerate(score.voice_ids(t, bar, st)):
                    if vid == "-1" or not score.beat_ids(vid):
                        continue
                    have = score.voice_length(vid)
                    if have > want:
                        r.errors.append(f"{t.label()} bar {bar} v{slot + 1}: {have} quarters in a {score.time(bar)} bar (too long)")
                    elif have < want and strict_lengths:
                        r.warnings.append(f"{t.label()} bar {bar} v{slot + 1}: {have} of {want} quarters (incomplete)")
                    for bid in score.beat_ids(vid):
                        for nid in score.note_ids(bid):
                            _check_note(score, t, bar, slot, score.notes[nid], r)
    _check_ties(score, r)
    for a in score.root.findall("MasterTrack/Automations/Automation"):
        b = int(child_text(a, "Bar", "0") or 0)
        if not 0 <= b < score.bar_count:
            r.errors.append(f"{child_text(a, 'Type')} automation on bar {b + 1}, past the end of the score")
    return r


def _check_note(score: Score, t, bar: int, slot: int, n, r: Report):
    where = f"{t.label()} bar {bar} v{slot + 1}"
    if t.kind == "drums":
        a = score.note_articulation(n)
        if a is None or not 0 <= a < len(t.articulations):
            r.errors.append(f"{where}: drum note with unknown articulation {a}")
        return
    if t.kind != "fretted":
        return
    sf = score.note_string_fret(n)
    if sf is None:
        return
    s, f = sf
    if not 0 <= s < t.strings:
        r.errors.append(f"{where}: note on string index {s}, track has {t.strings} strings")
    elif not 0 <= f <= max(t.fret_count, 30):
        r.errors.append(f"{where}: fret {f} out of range")


def _note_key(score: Score, t, n):
    return score.note_articulation(n) if t.kind == "drums" else (score.note_string_fret(n) or (None,))[0]


def _check_ties(score: Score, r: Report):
    """A tied-to note (destination) needs a note on the same string/drum right before it with origin=true."""
    for t in score.tracks:
        if t.kind not in ("drums", "fretted"):
            continue
        for st in range(1, t.staves + 1):
            for slot in range(4):
                prev: dict = {}
                for bar in range(1, score.bar_count + 1):
                    vid = score.voice_ids(t, bar, st)[slot]
                    for bid in score.beat_ids(vid):
                        if score.is_grace(bid):
                            continue
                        cur = {}
                        for nid in score.note_ids(bid):
                            n = score.notes[nid]
                            key = _note_key(score, t, n)
                            tie = n.find("Tie")
                            if tie is not None and tie.get("destination") == "true":
                                p = prev.get(key)
                                if p is None:
                                    r.warnings.append(f"{t.label()} bar {bar} v{slot + 1}: tied note with nothing "
                                                      f"to tie from")
                                elif p.find("Tie") is None or p.find("Tie").get("origin") != "true":
                                    r.warnings.append(f"{t.label()} bar {bar} v{slot + 1}: tied note, but the note "
                                                      f"before it isn't marked as a tie start")
                            cur[key] = n
                        prev = cur
