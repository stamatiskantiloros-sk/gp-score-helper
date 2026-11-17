// node render.mjs <file.gp> <out.svg> <tracks e.g. "0,2"> <startBar 1-based> <barCount> [width]
import * as alphaTab from '@coderline/alphatab';
import fs from 'fs';
import path from 'path';
import { createRequire } from 'module';

const [, , file, out, tracksArg = '0', start = '1', count = '8', widthArg = '1400'] = process.argv;
const settings = new alphaTab.Settings();
settings.core.engine = 'svg';
settings.core.enableLazyLoading = false;
settings.core.useWorkers = false;
settings.display.startBar = parseInt(start);
settings.display.barCount = parseInt(count);
settings.display.layoutMode = alphaTab.LayoutMode.Page;
for (const el of ['ScoreTitle', 'ScoreSubTitle', 'ScoreArtist', 'ScoreAlbum', 'ScoreWords', 'ScoreMusic',
                  'ScoreWordsAndMusic', 'ScoreCopyright', 'GuitarTuning']) {
  if (alphaTab.NotationElement[el] !== undefined) settings.notation.elements.set(alphaTab.NotationElement[el], false);
}
const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(new Uint8Array(fs.readFileSync(file)), settings);
const tracks = tracksArg.split(',').map(x => parseInt(x));
const renderer = new alphaTab.rendering.ScoreRenderer(settings);
renderer.width = parseInt(widthArg);
const parts = [];
renderer.partialRenderFinished.on(r => parts.push(r));
renderer.error.on(e => { console.error('render error:', e && e.message ? e.message : e); process.exit(2); });
renderer.renderFinished.on(r => {
  const require = createRequire(import.meta.url);
  const fontPath = path.join(path.dirname(require.resolve('@coderline/alphatab')), 'font', 'Bravura.woff2');
  const font = fs.readFileSync(fontPath).toString('base64');
  let body = '';
  for (const p of parts) if (p.renderResult) body += `<g transform="translate(${p.x},${p.y})">${p.renderResult}</g>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${r.totalWidth}" height="${r.totalHeight}">` +
    `<style>@font-face{font-family:'alphaTab';src:url(data:font/woff2;base64,${font}) format('woff2');}` +
    `.at{font-family:'alphaTab',Bravura;font-size:34px;}</style>` +
    `<rect width="100%" height="100%" fill="white"/>${body}</svg>`;
  fs.writeFileSync(out, svg);
  console.log(JSON.stringify({ out, width: r.totalWidth, height: r.totalHeight,
    tracks: score.tracks.length, bars: score.masterBars.length }));
});
renderer.renderScore(score, tracks);
