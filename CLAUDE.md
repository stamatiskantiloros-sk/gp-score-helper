@AGENTS.md

## Working on the tool itself (not on a score)

- Code lives in `gpscore/`: `container.py` (zip), `score.py` (GPIF model, copy-on-write), `notation.py`
  (text notation), `view.py`, `ops.py` (operations), `check.py`, `cli.py`. `render/` is the Node/alphaTab
  helper.
- Run `pytest -q` after every change. Tests use `tests/fixtures/*.gp` (real Guitar Pro 8.1.3 files).
- Preserve everything in the XML you don't understand. Never rebuild a file from a parsed model.
- Any new edit primitive must go through `own_voice` / `own_beat` / `own_note` (Guitar Pro shares objects).
- New Guitar Pro encodings: confirm them on a file saved by Guitar Pro 8 itself. alphaTab's GpifWriter
  (`render/node_modules/@coderline/alphatab/dist/alphaTab.core.mjs`, class GpifWriter) is a good reference.
