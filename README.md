# gp-score-helper

A command-line tool (`gpscore`) for reading, tidying and extending **Guitar Pro 8** scores, built
to be driven by an AI agent. Open a terminal agent (Claude Code, opencode, …) in this folder, tell it
to read `AGENTS.md`, and ask for things like:

- "Tidy the drums in `~/Music/song.gp`: dynamics per section, ghost notes in the verse groove, and
  make the hi-hat notation consistent."
- "Bars 17–18 are the chorus groove. Continue it to bar 32 with a fill every 4 bars."
- "Write a bass line for bars 33–40 that follows the kick and the guitar roots."
- "The riff in bar 9 is palm-muted. Apply that everywhere the same riff appears."

The agent edits a copy (`song.edit-1.gp`, `-2`, …). Your original is never overwritten.

## Install

```bash
git clone https://github.com/stamatiskantiloros-sk/gp-score-helper ~/tools/gp-score-helper
cd ~/tools/gp-score-helper
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cd render && npm install && cd ..      # optional: engraving previews + a second parser for checks
brew install librsvg                   # optional: PNG previews
pytest -q                              # optional: run the tests
```

## First thing to do with a new file

Check that Guitar Pro opens files written by this tool:

```bash
gpscore roundtrip ~/path/song.gp       # writes song.roundtrip.gp, unchanged
```

Open `song.roundtrip.gp` in Guitar Pro 8. It should look and play exactly like the original.

`.gpx` (Guitar Pro 6) and `.gp5` files: open them in Guitar Pro 8 and **Save As** `.gp` first.

## Commands

| command | |
|---|---|
| `gpscore info song.gp` | tracks, tunings, tempo map, one line per bar |
| `gpscore view song.gp -t 2 -b 9-16 [--pitch]` | bars in the text notation |
| `gpscore legend song.gp -t Drums` | drum abbreviations |
| `gpscore patterns song.gp -t 3` | repeated riffs and inconsistent articulations |
| `gpscore apply song.gp ops.json [--dry-run] [-o out.gp]` | apply edits, write a new file |
| `gpscore diff song.gp song.edit-1.gp` | what changed, bar by bar |
| `gpscore render song.gp -t 2 -b 9-16 -o look.png` | engraved preview (alphaTab) |
| `gpscore check song.gp` | integrity check |
| `gpscore ops` / `gpscore notation` | reference |

See `AGENTS.md` for the notation, the operations, and the tidying/autocomplete playbooks.

## How it works

A `.gp` file is a zip. The score is the XML in `Content/score.gpif`, a set of flat tables
(master bars → bars → voices → beats → notes) linked by id. `gpscore` edits that XML in place with
lxml and keeps everything it doesn't understand. Guitar Pro reuses identical beats and notes across
the song, so every edit clones the object first (copy-on-write). The other files in the zip are
copied through untouched.

Why not ApolloTab? It was tried. It reads GP7/8 files (dynamics, hairpins, accents, ghost notes
came through), but it has no writer, it needs PyQt5 even to parse, it crashed on `.gpx`, and on a
real GP8 file it reported 4/4 for every bar and the wrong tempo. alphaTab (Node) is used instead for
previews and as an independent check.
