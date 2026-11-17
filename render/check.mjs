// node check.mjs <file.gp>  -> loads the file with alphaTab (an independent GPIF parser) and prints a summary
import * as alphaTab from '@coderline/alphatab';
import fs from 'fs';
const settings = new alphaTab.Settings();
try {
  const s = alphaTab.importer.ScoreLoader.loadScoreFromBytes(new Uint8Array(fs.readFileSync(process.argv[2])), settings);
  let notes = 0;
  for (const t of s.tracks) for (const st of t.staves) for (const b of st.bars) for (const v of b.voices)
    for (const be of v.beats) notes += be.notes.length;
  console.log(JSON.stringify({ ok: true, title: s.title, tracks: s.tracks.length, bars: s.masterBars.length, notes }));
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e && e.message ? e.message : e) }));
  process.exit(1);
}
