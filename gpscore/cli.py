"""gpscore - read, tidy and extend Guitar Pro 8 scores from the command line.

Run `gpscore <command> -h` for the options of each command. See AGENTS.md for the workflow.
"""
from __future__ import annotations

import argparse
import difflib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import view
from .check import check
from .container import FormatError
from .notation import DrumKit
from .ops import OPS, OpError, apply_ops, patterns
from .score import Score

RENDER_DIR = Path(__file__).resolve().parent.parent / "render"


def _open(path: str) -> Score:
    try:
        return Score.open(path)
    except FormatError as e:
        sys.exit(f"error: {e}")


def _default_out(path: Path) -> Path:
    stem = path.stem
    if ".edit-" in stem:
        stem = stem.rsplit(".edit-", 1)[0]
    n = 1
    while True:
        cand = path.with_name(f"{stem}.edit-{n}.gp")
        if not cand.exists():
            return cand
        n += 1


# ---------------------------------------------------------------- commands

def cmd_info(a):
    s = _open(a.file)
    print(view.overview(s))
    print()
    print(view.structure(s))


def cmd_structure(a):
    print(view.structure(_open(a.file)))


def cmd_view(a):
    s = _open(a.file)
    tracks = [s.track(t) for t in a.track] if a.track else [t for t in s.tracks]
    bars = view.parse_range(a.bars, s.bar_count)
    out = [view.bars_view(s, t, bars, pitch=a.pitch, staff=a.staff) for t in tracks]
    if a.help_notation:
        out.insert(0, view.NOTATION_HELP)
    print("\n\n".join(out))


def cmd_legend(a):
    s = _open(a.file)
    t = s.track(a.track)
    if t.kind != "drums":
        sys.exit(f"{t.label()} is not a drum track")
    kit = DrumKit(t)
    for i, art in enumerate(t.articulations):
        print(f"{kit.abbr[i]:<8} #{i:<3} {art.name:<28} [{art.element}]  midi {art.midi}")


def cmd_patterns(a):
    s = _open(a.file)
    t = s.track(a.track)
    groups = patterns(s, t, a.staff or 1)
    if not groups:
        print(f"{t.label()}: no repeated bars")
        return
    print(f"{t.label()}: bars with identical notes and rhythm (articulations ignored)")
    for g in groups:
        flag = "consistent" if g["consistent"] else "INCONSISTENT articulations: " + \
            " vs ".join("{" + ",".join(map(str, v)) + "}" for v in g["variants"])
        print(f"  bars {','.join(map(str, g['bars']))}: {flag}")


def _load_ops(spec: str) -> list[dict]:
    p = Path(spec)
    text = p.read_text() if p.exists() else spec
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("ops", [data])
    if not isinstance(data, list):
        sys.exit("error: ops must be a JSON list, or an object with an 'ops' list")
    return data


def _new_problems(before, after) -> tuple[list[str], list[str]]:
    return ([e for e in after.errors if e not in set(before.errors)],
            [w for w in after.warnings if w not in set(before.warnings)])


def cmd_apply(a):
    src = Path(a.file)
    s = _open(a.file)
    before = check(s, strict_lengths=True)
    try:
        apply_ops(s, _load_ops(a.ops))
    except OpError as e:
        sys.exit(f"error: {e}\nNothing was written.")
    after = check(s, strict_lengths=True)
    errs, warns = _new_problems(before, after)
    print("Changes:")
    for c in s.changes:
        print(f"  - {c}")
    for w in warns:
        print(f"warning: {w}")
    if errs:
        for e in errs:
            print(f"ERROR: {e}")
        sys.exit("The edit introduced errors; nothing was written.")
    if a.dry_run:
        print("(dry run: nothing written)")
        return
    out = Path(a.out) if a.out else _default_out(src)
    try:
        s.save(out, overwrite=a.force)
    except FormatError as e:
        sys.exit(f"error: {e}")
    print(f"Wrote {out}")
    ext = _alphatab_check(out)
    if ext is not None:
        print("alphaTab check: " + ("OK" if ext.get("ok") else "FAILED " + ext.get("error", "")))
        if not ext.get("ok"):
            sys.exit(1)


def cmd_check(a):
    s = _open(a.file)
    r = check(s, strict_lengths=a.strict)
    print(r.text())
    ext = _alphatab_check(Path(a.file))
    if ext is not None:
        print("alphaTab: " + (f"loads OK ({ext['tracks']} tracks, {ext['bars']} bars, {ext['notes']} notes)"
                              if ext.get("ok") else "FAILED to load: " + ext.get("error", "")))
    sys.exit(0 if r.ok and (ext is None or ext.get("ok")) else 1)


def cmd_roundtrip(a):
    """Write an unchanged copy through the tool: the first thing to test with a new file in Guitar Pro."""
    s = _open(a.file)
    out = Path(a.out) if a.out else Path(a.file).with_name(Path(a.file).stem + ".roundtrip.gp")
    s.save(out, overwrite=a.force)
    from lxml import etree
    orig = etree.fromstring(s.container.gpif, etree.XMLParser(strip_cdata=False))
    new = etree.fromstring(Score.open(out).container.gpif, etree.XMLParser(strip_cdata=False))
    same = etree.tostring(orig, method="c14n") == etree.tostring(new, method="c14n")
    print(f"Wrote {out}; XML identical after canonicalisation: {same}")


def cmd_diff(a):
    sa, sb = _open(a.a), _open(a.b)
    out = []
    sa_struct = view.structure(sa).splitlines()
    sb_struct = view.structure(sb).splitlines()
    d = [l for l in difflib.unified_diff(sa_struct, sb_struct, "a", "b", n=0, lineterm="") if not l.startswith("@@")]
    if len(d) > 2:
        out.append("Structure:\n" + "\n".join(d[2:]))
    ta = {t.number: t for t in sa.tracks}
    for t in sb.tracks:
        if a.track and str(t.number) not in a.track and t.name not in a.track:
            continue
        other = ta.get(t.number)
        changed = []
        for bar in range(1, sb.bar_count + 1):
            vb = view.bars_view(sb, t, [bar], legend=False).splitlines()[1:]
            va = view.bars_view(sa, other, [bar], legend=False).splitlines()[1:] if other and bar <= sa.bar_count else []
            if va != vb:
                changed.append((bar, va, vb))
        if changed:
            out.append(f"== {t.label()}: {len(changed)} bar(s) changed ==")
            for bar, va, vb in changed:
                for l in va:
                    out.append(f"- {l}")
                for l in vb:
                    out.append(f"+ {l}")
    print("\n".join(out) if out else "No differences.")


def _node() -> str | None:
    node = shutil.which("node")
    if not node or not (RENDER_DIR / "node_modules" / "@coderline" / "alphatab").exists():
        return None
    return node


def _alphatab_check(path: Path) -> dict | None:
    node = _node()
    if not node:
        return None
    p = subprocess.run([node, str(RENDER_DIR / "check.mjs"), str(path)], capture_output=True, text=True)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": (p.stderr or p.stdout).strip()[:300]}


def cmd_render(a):
    node = _node()
    if not node:
        sys.exit("error: rendering needs Node.js and `npm install` in the render/ folder (see README)")
    s = _open(a.file)
    tracks = ",".join(str(s.track(t).index) for t in (a.track or ["1"]))
    bars = view.parse_range(a.bars, s.bar_count)
    start, count = bars[0], bars[-1] - bars[0] + 1
    out = Path(a.out or Path(a.file).with_suffix("").name + f"_b{start}-{bars[-1]}.png")
    svg = out.with_suffix(".svg")
    p = subprocess.run([node, str(RENDER_DIR / "render.mjs"), a.file, str(svg), tracks, str(start), str(count),
                        str(a.width)], capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"render failed: {p.stderr.strip()}")
    if out.suffix.lower() == ".svg":
        print(f"Wrote {svg}")
        return
    if _svg_to_png(svg, out):
        svg.unlink(missing_ok=True)
        print(f"Wrote {out}")
    else:
        print(f"Wrote {svg} (no SVG→PNG converter found; install librsvg: `brew install librsvg`)")


def _svg_to_png(svg: Path, png: Path) -> bool:
    if shutil.which("rsvg-convert"):
        return subprocess.run(["rsvg-convert", str(svg), "-o", str(png)]).returncode == 0
    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(url=str(svg), write_to=str(png))
        return True
    except Exception:
        pass
    if shutil.which("qlmanage"):  # macOS Quick Look
        r = subprocess.run(["qlmanage", "-t", "-s", "2000", "-o", str(png.parent), str(svg)], capture_output=True)
        made = png.parent / (svg.name + ".png")
        if r.returncode == 0 and made.exists():
            made.rename(png)
            return True
    return False


def cmd_ops(a):
    """List the operations with their documentation."""
    for name, fn in OPS.items():
        doc = (fn.__doc__ or "").strip()
        print(f"{name}\n    " + doc.replace("\n", "\n    ") + "\n")


def cmd_notation(a):
    print(view.NOTATION_HELP)


# ---------------------------------------------------------------- parser

def main(argv=None):
    ap = argparse.ArgumentParser(prog="gpscore", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="overview: tracks, tunings, tempo, bar-by-bar structure")
    p.add_argument("file")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("structure", help="one line per bar: time, sections, repeats, tempo, which tracks play")
    p.add_argument("file")
    p.set_defaults(fn=cmd_structure)

    p = sub.add_parser("view", help="bars of one or more tracks in the text notation")
    p.add_argument("file")
    p.add_argument("-t", "--track", action="append", help="track number or name (repeatable; default all)")
    p.add_argument("-b", "--bars", default=None, help="e.g. 9-16 or 1,5,9-12 (default all)")
    p.add_argument("--pitch", action="store_true", help="show pitch names next to string:fret")
    p.add_argument("--staff", type=int, default=None)
    p.add_argument("--help-notation", action="store_true", help="print the notation key first")
    p.set_defaults(fn=cmd_view)

    p = sub.add_parser("legend", help="all drum abbreviations of a drum track")
    p.add_argument("file")
    p.add_argument("-t", "--track", required=True)
    p.set_defaults(fn=cmd_legend)

    p = sub.add_parser("patterns", help="repeated bars of a track, and whether their articulations agree")
    p.add_argument("file")
    p.add_argument("-t", "--track", required=True)
    p.add_argument("--staff", type=int, default=None)
    p.set_defaults(fn=cmd_patterns)

    p = sub.add_parser("apply", help="apply a list of operations (JSON) and write a NEW file")
    p.add_argument("file")
    p.add_argument("ops", help="path to a JSON file, or inline JSON")
    p.add_argument("-o", "--out", help="output path (default: <name>.edit-N.gp next to the input)")
    p.add_argument("--force", action="store_true", help="overwrite the output if it exists (never the input)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("check", help="integrity check (+ independent load with alphaTab if installed)")
    p.add_argument("file")
    p.add_argument("--strict", action="store_true", help="also warn about incomplete bars")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("roundtrip", help="write an unchanged copy (test that Guitar Pro opens our output)")
    p.add_argument("file")
    p.add_argument("-o", "--out")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_roundtrip)

    p = sub.add_parser("diff", help="what changed between two versions, bar by bar")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("-t", "--track", action="append")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("render", help="engrave bars to PNG/SVG with alphaTab (needs Node)")
    p.add_argument("file")
    p.add_argument("-t", "--track", action="append", help="track number or name (repeatable)")
    p.add_argument("-b", "--bars", default="1-8")
    p.add_argument("-o", "--out", help="output .png or .svg")
    p.add_argument("--width", type=int, default=1400)
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("ops", help="list the operations usable in `apply`")
    p.set_defaults(fn=cmd_ops)

    p = sub.add_parser("notation", help="print the text notation key")
    p.set_defaults(fn=cmd_notation)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (KeyError, IndexError) as e:
        sys.exit(f"error: {e.args[0] if e.args else e}")


if __name__ == "__main__":
    main()
