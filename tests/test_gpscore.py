from pathlib import Path

import pytest
from lxml import etree

from gpscore import view
from gpscore.check import check
from gpscore.music import Rhythm
from gpscore.notation import DrumKit, parse_bars, parse_voice
from gpscore.ops import OpError, apply_ops, patterns
from gpscore.score import Score

FIX = Path(__file__).parent / "fixtures"
SONG = FIX / "statued_pain.gp"
CAL = FIX / "calibration.gp"


def c14n(score_or_bytes):
    raw = score_or_bytes if isinstance(score_or_bytes, bytes) else score_or_bytes.to_gpif()
    return etree.tostring(etree.fromstring(raw, etree.XMLParser(strip_cdata=False)), method="c14n")


def reload(score, tmp_path, name="out.gp"):
    out = score.save(tmp_path / name)
    return Score.open(out)


def test_roundtrip_is_lossless(tmp_path):
    for f in (SONG, CAL):
        s = Score.open(f)
        s2 = reload(s, tmp_path, f.name)
        assert c14n(s.container.gpif) == c14n(s2.container.gpif)
        names = [i.filename for i, _ in s.container.entries]
        assert names == [i.filename for i, _ in s2.container.entries]


def test_refuses_to_overwrite_input(tmp_path):
    s = Score.open(SONG)
    with pytest.raises(ValueError):
        s.save(SONG)


def test_rhythm_tokens():
    for tok in ["q", "e.", "s/3", "e/5", "e/5:2", "h..", "t", "e/6"]:
        assert Rhythm.parse(tok).token() == tok
    assert Rhythm.parse("e/3").length * 3 == 1


def test_view_reads_drums_and_guitar():
    s = Score.open(SONG)
    d = view.bars_view(s, s.track(2), [13])
    assert "e(K+SPL){f}" in d
    b = view.bars_view(s, s.track("Electric Bass"), [13], pitch=True)
    assert "3:7(E2)" in b


def test_copy_on_write_keeps_shared_beats_independent(tmp_path):
    s = Score.open(SONG)
    before = view.bars_view(s, s.track(2), [15], legend=False)
    apply_ops(s, [{"op": "flags", "track": 2, "bars": "13", "on": "1", "drums": "K", "add": [">"]}])
    assert "K[>]" in view.bars_view(s, s.track(2), [13])
    assert view.bars_view(s, s.track(2), [15], legend=False) == before
    s2 = reload(s, tmp_path)
    assert "K[>]" in view.bars_view(s2, s2.track(2), [13])
    assert check(s2).ok


def test_write_drums_and_bass_with_ties_and_flags(tmp_path):
    s = Score.open(SONG)
    apply_ops(s, [
        {"op": "write", "track": 2, "text": "b21 v1 e(K+HHc){f} e(HHc) e(S[>]+HHc) e(HHc) e(K+HHc) e(K+HHc) "
                                            "s(S) s(S[g]) e(S+CR1[^])"},
        {"op": "write", "track": 3, "text": "b21\n v1 q(3:7[pm]) q(~3:7) e(3:7[h]) e(3:9) q(2:5[lr,v])"},
    ])
    s2 = reload(s, tmp_path)
    d = view.bars_view(s2, s2.track(2), [21])
    assert "s(S[g])" in d and "CR1[^]" in d
    b = view.bars_view(s2, s2.track(3), [21])
    assert "q(~3:7)" in b and "3:7[h]" in b and "2:5[lr,v]" in b
    r = check(s2)
    assert r.ok and not r.warnings


def test_write_rejects_wrong_bar_length():
    s = Score.open(SONG)
    with pytest.raises(OpError, match="needs"):
        apply_ops(s, [{"op": "write", "track": 2, "text": "b21 v1 q(K) q(S)"}])


def test_write_7_8_bar():
    s = Score.open(SONG)
    assert s.time(14) == "7/8"
    apply_ops(s, [{"op": "write", "track": 2, "text": "b14 v1 e(K) e(HHc) e(S) e(HHc) e(K) e(S) e(HHc)"}])
    assert check(s).ok


def test_copy_and_append(tmp_path):
    s = Score.open(SONG)
    n = s.bar_count
    apply_ops(s, [{"op": "append_bars", "count": 4},
                  {"op": "copy", "tracks": "all", "from": "15-16", "to": n + 1, "times": 2}])
    s2 = reload(s, tmp_path)
    assert s2.bar_count == n + 4
    for t in (2, 3):
        assert view.bars_view(s2, s2.track(t), [n + 3], legend=False).splitlines()[2:] == \
            view.bars_view(s2, s2.track(t), [15], legend=False).splitlines()[2:]
    assert check(s2).ok


def test_structure_ops(tmp_path):
    s = Score.open(SONG)
    apply_ops(s, [
        {"op": "section", "bar": 13, "letter": "A", "text": "Verse"},
        {"op": "repeat", "start": 21, "end": 22, "count": 3},
        {"op": "tempo_ramp", "from": 30, "to": 32, "from_bpm": 114, "to_bpm": 140},
        {"op": "dynamic", "track": 2, "bars": "30-31", "value": "ff"},
        {"op": "hairpin", "track": 2, "bars": "29", "value": "cresc"},
    ])
    s2 = reload(s, tmp_path)
    st = view.structure(s2)
    assert "[A: Verse]" in st and "|:" in st and ":|x3" in st and "q=114~" in st and "q=140" in st
    assert "{ff}" in view.bars_view(s2, s2.track(2), [30])
    assert "cresc" in view.bars_view(s2, s2.track(2), [29])


def test_propagate_and_patterns():
    s = Score.open(SONG)
    groups = patterns(s, s.track(2))
    g = next(g for g in groups if 14 in g["bars"])
    apply_ops(s, [{"op": "flags", "track": 2, "bars": "14", "drums": "S", "add": [">"]}])
    g2 = next(g for g in patterns(s, s.track(2)) if 14 in g["bars"])
    assert not g2["consistent"]
    apply_ops(s, [{"op": "propagate", "track": 2, "from": 14, "to": "matching"}])
    g3 = next(g for g in patterns(s, s.track(2)) if 14 in g["bars"])
    assert g3["consistent"] and g3["bars"] == g["bars"]


def test_drum_swap():
    s = Score.open(SONG)
    apply_ops(s, [{"op": "drum_swap", "track": 2, "bars": "13", "from": "HHc", "to": "RD"}])
    assert "HHc" not in view.bars_view(s, s.track(2), [13], legend=False)


def test_parse_bars_layout():
    bars = parse_bars("== T2 ==\nlegend: K=Kick\nb3 4/4 [A: x]\n  v1  q(K) q(S)  // comment\nb4 v2 w(-)")
    assert bars == {3: {1: "q(K) q(S)"}, 4: {2: "w(-)"}}


def test_parse_voice_errors():
    s = Score.open(SONG)
    kit = DrumKit(s.track(2))
    with pytest.raises(ValueError):
        parse_voice("q(K) q(NOPE)", s.track(2), kit)
    with pytest.raises(ValueError):
        parse_voice("q(9:3)", s.track(3), None)


def test_gpx_is_refused(tmp_path):
    p = tmp_path / "x.gpx"
    p.write_bytes(b"BCFZ" + b"\0" * 64)
    with pytest.raises(ValueError, match="Save As"):
        Score.open(p)
