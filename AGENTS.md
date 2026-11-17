# gp-score-helper — manual for the agent

You are helping Stamatis with his Guitar Pro 8 scores (mostly progressive metal: 7-string guitars,
5-string bass, odd meters, a lot of drum writing). There are two kinds of jobs:

1. **Tidying**: adding the detail a finished score needs. That means consistent dynamics, accents and
   ghost notes, palm mutes and let-rings across repeated riffs, section markers, repeat structure,
   tempo changes, and clean drum notation.
2. **Autocomplete**: he writes the first instance of a pattern (or the first bars of a section) and you
   complete the rest. That can mean continuing a groove, varying it sensibly, adding fills at phrase
   ends, writing a bass line that follows the kick and the guitar roots, or extending a part across a section.

You do this with the `gpscore` command-line tool in this repository. It reads a `.gp` file, shows it
to you as compact text, applies edits you describe as JSON operations, checks the result, and
writes a **new** `.gp` file that he opens in Guitar Pro 8.

Read this whole file before your first edit. `gpscore ops` and `gpscore notation` print the
reference parts on demand.

---

## 0. Ground rules

- **Never overwrite his file.** `apply` always writes a new file (`song.edit-1.gp`, `-2`, …). It
  refuses to write over its input. Don't work around that.
- **Only `.gp` files (Guitar Pro 7/8).** For `.gpx` (GP6) or `.gp5`/`.gp4`/`.gp3`, ask him to open the
  file in Guitar Pro 8 and **File › Save As** a `.gp` copy first. `gpscore` tells you this too.
- **Do what was asked, no more.** Tidying means adding markings. It never means changing his notes,
  unless he asks. For autocomplete, write only the bars he points to. If a request is vague ("tidy
  the verse"), propose the concrete list of changes first and apply it after he agrees.
- **Musical choices are his.** When there are real alternatives (where a fill goes, how busy the bass
  is), show one or two options as text before writing them. Once he has said "go", don't ask again
  for every bar.
- **One request, one `apply`.** Put all the operations for a request in one ops file. It is checked
  as a whole, and nothing is written if any op fails.
- **Always verify** (section 5) before telling him the file is ready, and tell him the exact output
  path plus a short list of what changed.

## 1. Setup (once per machine)

```bash
cd ~/tools/gp-score-helper
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                 # installs the `gpscore` command (needs lxml)
cd render && npm install && cd .. # optional but recommended: alphaTab for render + second-opinion check
brew install librsvg              # optional: SVG→PNG for `gpscore render` (else Quick Look is used)
```

If `gpscore` isn't on PATH, use `python3 -m gpscore` from the repo folder.

## 2. Orient yourself

```bash
gpscore info song.gp                          # tracks, tunings, tempo map, one line per bar
gpscore view song.gp -t 2 -b 9-16             # bars 9-16 of track 2 in text notation
gpscore view song.gp -t Bass -t Guitar -b 9-12 --pitch   # several tracks; pitch names (harmony work)
gpscore legend song.gp -t Drums               # every drum abbreviation of a drum track
gpscore patterns song.gp -t 3                 # repeated bars, and whether their articulations agree
gpscore render song.gp -t 2 -t 3 -b 9-16 -o look.png   # engraved picture; open it to look
```

Tracks are addressed by number (`3`, `T3`) or by name or name prefix (`Drumkit`, `"Electric Bass"`).
Bars, tracks, voices, strings and beat positions are **1-based**, the same numbers Guitar Pro shows.

Read the bars around what you'll change, not just the bars themselves. You need the previous
dynamic, the groove you're continuing, and what the other instruments do.

## 3. The text notation

```
b13 4/4  [A: Verse]
  v1  q.(3:7[pm]){f}  e(2:9)  q(3:7[pm])  q(2:5)
b14 7/8
  v1  e(K+HHc)  e(S[>]+HHo)  e(HHc)  e(K+RD)  e(S[g])  e(HHc)  e(K+HHc)
```

- A bar is `b<N> <time>` plus its marks. Each non-empty voice is a line `v1`…`v4`.
- A beat is `DURATION(NOTES){OPTIONS}`, and beats are separated by spaces.
- **Durations**: `w h q e s t x` = whole, half, quarter, eighth, 16th, 32nd, 64th. Dots: `q.`, `e..`.
  Tuplets: `e/3` (triplet eighth), `s/5:4` (quintuplet 16th), `s/6`.
- **Notes**: fretted = `STRING:FRET`, where **string 1 is the highest string**, like in tab (on a 7-string,
  7 is the low B). Drums use the abbreviations from the legend (`K` kick, `S` snare, `HHc`/`HHo`/`HHh`/`HHp`
  hi-hat closed/open/half/pedal, `RD` ride, `RDb` ride bell, `CR1`/`CR2` crashes, `CH` china, `SPL` splash,
  `tom1`…`tom6` high→low, `SS` side stick, `SR` rim shot…), or `#index`.
  `+` joins notes struck together, `-` is a rest, and a `~` prefix means *tied from the previous note
  on the same string/drum*.
- **Note flags** in `[...]`, comma-separated: `>` accent, `^` heavy accent, `g` ghost, `st` staccato,
  `ten` tenuto, `pm` palm mute, `lr` let ring, `x` dead note, `h` hammer-on/pull-off to the next note,
  `v`/`vw` vibrato, `sl`/`lsl` shift/legato slide. You will also *see* `bend harm tap trill`. These are
  kept when you edit other things, but you can't write them.
- **Beat options** in `{...}`: a dynamic `ppp…fff` (the view shows it only where it changes, and when
  writing, a beat without one inherits the previous beat's), `cresc`/`dim` hairpin (put it on every
  beat the hairpin spans), `grace`/`grace-on`, `trem=1/8`. `+Chord`, `"text"` etc. are display-only.
- **Bar marks** in the view: `|:` and `:|x3` repeats, `[A: Verse]` section, `tempo q=120` (a trailing
  `~` means it glides to the next tempo point), `ending=1,2`, `fermata`, `key=…`.
- `!! voice is 3 of 4 quarters` in the view means the bar is incomplete in his file.

Every voice you write must fill the bar exactly. In 7/8, that's 3.5 quarters (seven `e`).

## 4. Operations

Write them as a JSON list in a file (e.g. `scratch/ops.json`) and run:

```bash
gpscore apply song.gp scratch/ops.json --dry-run   # see the change log + new problems, writes nothing
gpscore apply song.gp scratch/ops.json             # writes song.edit-N.gp
```

Selections shared by several ops: `track`, `bars` ("9-16", "1,5,9-12", "all"), optional `voices`
([1] or "1-2"), `staff` (2 for the second staff of a grand-staff track), and `on` = beat positions in the bar's
own beat unit, 1-based ("2,4" = beats 2 and 4. "2.5" = the "and" of 2; in 7/8 the unit is the eighth).
Note filters: `strings` ("6-7"), `drums` (["S","HHc"]), `frets`, `has` (only notes already carrying a flag).

| op | what it does | example |
|---|---|---|
| `write` | replace voices of bars with notation text (the view's layout) | `{"op":"write","track":2,"text":"b17\n v1 e(K+HHc){f} e(HHc) …"}` |
| `copy` | copy bars to another place (`times` to repeat); `tracks: "all"` for every track | `{"op":"copy","track":2,"from":"9-12","to":17,"times":2}` |
| `clear` | empty voices | `{"op":"clear","track":3,"bars":"20","voices":[2]}` |
| `flags` | add/remove note flags | `{"op":"flags","track":"Gtr","bars":"9-16","strings":"6-7","add":["pm"]}` |
| `dynamic` | set the dynamic of selected beats | `{"op":"dynamic","track":2,"bars":"17-24","value":"ff"}` |
| `hairpin` | cresc / dim / none on selected beats | `{"op":"hairpin","track":2,"bars":"31-32","value":"cresc"}` |
| `drum_swap` | change one drum piece to another | `{"op":"drum_swap","track":2,"bars":"25-32","from":"HHc","to":"RD"}` |
| `propagate` | copy articulations from one bar to all bars with the same notes (or a list) | `{"op":"propagate","track":3,"from":9,"to":"matching"}` |
| `section` | section marker (or `remove: true`) | `{"op":"section","bar":17,"letter":"B","text":"Chorus"}` |
| `repeat` | repeat bars `start`…`end`, `count` = total plays (or `remove: true`) | `{"op":"repeat","start":17,"end":20,"count":2}` |
| `ending` | alternate ending numbers on a bar | `{"op":"ending","bar":20,"numbers":[1]}` |
| `tempo` | tempo point (quarter-note bpm); `ramp: true` glides to the next point; `pos` 0–1 inside the bar | `{"op":"tempo","bar":33,"bpm":140}` |
| `tempo_ramp` | accel/rit from the start of bar `from` to reach `to_bpm` at the start of bar `to` | `{"op":"tempo_ramp","from":45,"to":49,"from_bpm":140,"to_bpm":100}` |
| `time` | time signature of bars (rewrite their content to fit) | `{"op":"time","bars":"40","value":"7/8"}` |
| `append_bars` | empty bars at the end (same time/key as the last bar, or `time`) | `{"op":"append_bars","count":8}` |

`gpscore ops` prints the full parameter list of each op.

`write` replaces only the voices you give. The other voices of those bars stay as they are. A bare
`v2` line clears voice 2. Ties into the bar after a rewritten bar are re-linked when the same
string/drum is there, and removed otherwise (the change log says which).

Guitar Pro shares identical objects between bars (one hi-hat note can be used by hundreds of beats).
`gpscore` clones before it edits, so changing bar 13 never changes bar 15. `copy` is cheap, and copies
stay independent.

## 5. Verify, every time

1. `apply` already refuses to write if your edit introduces errors (references, bars that are
   too long, strings/frets out of range, broken ties). Read its warnings too.
2. `gpscore diff song.gp song.edit-1.gp` shows exactly which bars changed, old vs new. Check it
   matches what he asked for and nothing else.
3. `gpscore render song.edit-1.gp -t <tracks> -b <bars> -o scratch/check.png` and **look at the
   picture**. Check the groove reads right, accents/ghosts are where you meant, and fills land at phrase ends.
4. Tell him: the output path, a short list of changes, and anything you were unsure about.

`gpscore check song.gp` also loads the file with alphaTab (an independent parser) when Node is set
up. Guitar Pro itself is the final judge. If he reports that GP8 complains about a file, that is a
bug in this tool: note what you did and stop editing that file.

## 6. Tidying playbook

Work section by section. Use `info` for the map and `patterns` to find repeated material.

- **Dynamics.** Every track should have a dynamic at its first bar and at each section start. Real
  changes (verse→chorus, breakdowns) get a new dynamic. Builds get a `cresc` hairpin over the last
  1–2 bars, plus the new dynamic on the downbeat after. Keep drums and bass consistent with the
  guitars' intensity.
- **Accents and ghost notes (drums).** Backbeat snares are normal hits. Accent only what is meant to
  stand out: crash downbeats, stabs, unison hits with the band. Ghost notes are quiet in-between
  snare strokes in grooves (`S[g]`). Never ghost a backbeat. Heavy accent (`^`) is rare: big stabs,
  ends of fills.
- **Palm mutes and let-rings across repeated riffs.** Run `gpscore patterns song.gp -t <gtr>`.
  "INCONSISTENT" groups are the same riff with different articulations. Ask which bar is right (or use
  the most common variant) and `propagate` it. Chugs on the low strings are usually `pm`. Sustained
  chords and arpeggios are usually `lr`.
- **Section markers.** Derive them from the structure: a change of meter or tempo, an entry/exit of
  instruments, a repeat start. Propose names (Intro, Verse, Pre-Chorus, Chorus, Bridge, Solo,
  Breakdown, Outro) with letters A, B, C…, and let him correct them.
- **Repeat structure.** Only replace written-out identical bars with repeat marks if he asks. That
  changes his layout. Adding `count` to existing repeats or fixing a missing start/end is fine.
- **Tempo.** Put sudden changes on the downbeat. Use `tempo_ramp` for accel/rit, and name the ramp
  in the section text if he uses words like "rit.".
- **Clean drum notation.** Watch for: the same groove written with different pieces (`K` vs `K2`,
  `HHc` vs `HHh`), open hi-hats that never close, crashes without a kick under them on section
  downbeats, hits split weirdly across voices. Fix with `drum_swap`, or `write` when the voices need
  re-splitting. Hands go in v1 and feet in v2 only if his score already does that. Follow his convention.

## 7. Autocomplete playbook

- **Start from his material.** `view` the bars he wrote, plus what the other tracks do in the bars
  you'll fill (`--pitch` for harmony). Name the pattern to yourself: meter, subdivision, kick
  pattern, snare placement, cymbal, phrase length.
- **Continue.** Repeat the pattern with `copy`, then vary it with `write` on specific bars. The usual
  shape is a 4- or 8-bar phrase, where the last bar of a phrase carries the variation or fill and the
  first bar of the next phrase lands with a crash (+kick) on beat 1.
- **Fills.** Keep them in the style: 16ths around the toms (high→low) for straight rock/metal,
  sextuplets or 32nds for prog flourishes, snare/tom unisons with the guitars when the riff has
  stabs. A fill replaces the end of the bar (last beat, last two beats, or the whole bar for big
  transitions). Leave the downbeat after it to the crash.
- **Odd meters.** Keep his grouping (for 7/8, 2+2+3 vs 3+2+2: look at where his snares/accents
  fall). Don't turn an odd bar into a 4/4 feel.
- **Bass that follows kick and guitar roots.** For each bar, take the kick rhythm as the bass
  rhythm. Take the guitar's lowest sounding note on that beat as the pitch (use `--pitch`), or the chord
  root if the guitar has a chord, and stay in a playable position (frets 0–12, few string jumps). Tie
  or hold where the kick holds. Add a passing note into the next root on the last 8th/16th of a
  phrase, only if that's his style. Check range against the bass tuning shown in `info`.
- **Write in chunks** (≤ 8 bars per `write` op), then `diff` and `render`. Show him the result before
  continuing into the next section.

## 8. Things the tool doesn't do (yet)

- Insert or delete bars in the middle of a score (only `append_bars` at the end). Ask him to
  insert empty bars in Guitar Pro, then fill them.
- Write bends, harmonics, taps, trills, lyrics, chord diagrams, or anything for keyboard/vocal tracks.
  These are preserved, just not editable.
- Change instruments, sounds, tunings or mixer settings.

## 9. Related project

The drum MIDI humanizer (`drumhum`, repo MIDI_Humanizer) reads the same `.gp` files and turns dynamics,
accents, ghost notes and hairpins into velocity. A score tidied with this tool gives the humanizer
better input. Keep the drum markings meaningful: ghost = quiet, accent = louder. Don't encode accents
as beat dynamics.
