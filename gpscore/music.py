"""Musical vocabulary shared by the reader, the text notation and the editor.

Durations are Fractions of a quarter note. Pitches are MIDI numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

# ---------------------------------------------------------------- durations

NOTE_VALUES: dict[str, Fraction] = {  # GPIF <NoteValue> -> length in quarters
    "DoubleWhole": Fraction(8), "Whole": Fraction(4), "Half": Fraction(2), "Quarter": Fraction(1),
    "Eighth": Fraction(1, 2), "16th": Fraction(1, 4), "32nd": Fraction(1, 8), "64th": Fraction(1, 16),
    "128th": Fraction(1, 32),
}
# Text-notation letter <-> GPIF NoteValue
LETTER_TO_VALUE = {"w": "Whole", "h": "Half", "q": "Quarter", "e": "Eighth", "s": "16th",
                   "t": "32nd", "x": "64th"}
VALUE_TO_LETTER = {v: k for k, v in LETTER_TO_VALUE.items()}
VALUE_TO_LETTER["DoubleWhole"] = "W"
VALUE_TO_LETTER["128th"] = "X"
LETTER_TO_VALUE["W"] = "DoubleWhole"
LETTER_TO_VALUE["X"] = "128th"

# Usual "den" of a tuplet written as /n (3 in the time of 2, 5 in the time of 4, ...)
DEFAULT_TUPLET_DEN = {2: 3, 3: 2, 4: 3, 5: 4, 6: 4, 7: 4, 9: 8, 10: 8, 11: 8, 12: 8, 13: 8}


@dataclass(frozen=True)
class Rhythm:
    value: str = "Quarter"        # GPIF NoteValue
    dots: int = 0
    tuplet: tuple[int, int] | None = None  # (num, den): num notes in the time of den

    @property
    def length(self) -> Fraction:
        d = NOTE_VALUES[self.value] * (2 - Fraction(1, 2 ** self.dots))
        if self.tuplet:
            num, den = self.tuplet
            d = d * den / num
        return d

    def token(self) -> str:
        s = VALUE_TO_LETTER[self.value] + "." * self.dots
        if self.tuplet:
            num, den = self.tuplet
            s += f"/{num}" if DEFAULT_TUPLET_DEN.get(num) == den else f"/{num}:{den}"
        return s

    @classmethod
    def parse(cls, token: str) -> "Rhythm":
        """'q', 'e.', 's/3', 'e/5:4', 'h..'"""
        tok = token.strip()
        if not tok or tok[0] not in LETTER_TO_VALUE:
            raise ValueError(f"bad duration '{token}' (use w h q e s t x, dots '.', tuplets '/3' or '/5:4')")
        value = LETTER_TO_VALUE[tok[0]]
        rest = tok[1:]
        dots = 0
        while rest.startswith("."):
            dots += 1
            rest = rest[1:]
        tuplet = None
        if rest:
            if not rest.startswith("/"):
                raise ValueError(f"bad duration '{token}'")
            spec = rest[1:]
            if ":" in spec:
                num, den = (int(x) for x in spec.split(":"))
            else:
                num = int(spec)
                if num not in DEFAULT_TUPLET_DEN:
                    raise ValueError(f"tuplet /{num} needs an explicit ratio, e.g. /{num}:4")
                den = DEFAULT_TUPLET_DEN[num]
            tuplet = (num, den)
        return cls(value, dots, tuplet)


def bar_length(time: str) -> Fraction:
    """'7/8' -> 3.5 quarters"""
    num, den = (int(x) for x in time.split("/"))
    return Fraction(4 * num, den)


# ---------------------------------------------------------------- dynamics

DYNAMICS = ["PPP", "PP", "P", "MP", "MF", "F", "FF", "FFF"]
DEFAULT_DYNAMIC = "MF"
HAIRPINS = {"cresc": "Crescendo", "dim": "Decrescendo"}
HAIRPIN_NAMES = {v: k for k, v in HAIRPINS.items()}

# ---------------------------------------------------------------- note flags
# Note-level <Accent> is a bit mask
ACCENT_BITS = {"st": 1, "^": 4, ">": 8, "ten": 16}

# Flag tokens used in the text notation, in display order
NOTE_FLAGS = [">", "^", "g", "st", "ten", "pm", "lr", "x", "h", "v", "vw", "sl"]
FLAG_HELP = {
    ">": "accent", "^": "heavy accent (marcato)", "g": "ghost note", "st": "staccato",
    "ten": "tenuto", "pm": "palm mute", "lr": "let ring", "x": "dead/muted note",
    "h": "hammer-on/pull-off to the next note", "v": "vibrato (slight)", "vw": "vibrato (wide)",
    "sl": "slide to the next note (shift slide)",
}

# ---------------------------------------------------------------- pitch

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def spell(midi: int, flats: bool = False) -> tuple[str, str, int]:
    """MIDI -> (step, accidental, octave) the way GPIF stores it (octave = midi // 12, so E2=40 is 'E3')."""
    name = (FLAT_NAMES if flats else SHARP_NAMES)[midi % 12]
    return name[0], name[1:], midi // 12


def pitch_name(midi: int, flats: bool = False) -> str:
    """Scientific pitch name (E2 = 40, middle C = C4 = 60)."""
    name = (FLAT_NAMES if flats else SHARP_NAMES)[midi % 12]
    return f"{name}{midi // 12 - 1}"
