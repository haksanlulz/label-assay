"""Generate the synthetic test-label corpus: PNGs plus two CSVs.

Programmatic so the corpus is reproducible and carries a known ground truth.
Build-time asserts pin the text, brand, and alcohol-content defects to the
engine's own matchers; the not-bold and missing-warning defects give those
matchers nothing to check at build time, so tests/test_make_labels.py verifies
every manifest verdict against the engine instead (the not-bold rows through
real OCR and the stroke-width detector on the rendered pixels). Alongside the
defects, a compliant body-caps variant renders the whole warning statement in
capitals — legal under 16.22(a)(2), which fixes only the heading's case — so
the corpus also catches case-handling false positives. The variety is the
point: classes across spirits/wine/malt, four layouts, six palettes, several
font families, four canvas sizes, and invented brands in varied casings, so the
checks are exercised on labels that do not all look alike.

Outputs (into --out):
- label_NNN.png            the labels
- applications.csv         filename,brand_name,class_type — the data FILED on
                           each application (for brand-mismatch labels this
                           deliberately differs from what is painted)
- manifest.csv             filename,defect,expected_verdict,notes — ground truth
                           for tests and evaluation; the app never reads it

Deterministic: one random.Random(--seed) drives every choice. Same seed + same
machine (same fonts) reproduces byte-identical output; fonts differ across
machines, so committed bytes are machine-specific.

Run:  uv run python tools/make_test_labels.py
Fonts: Windows families (arial/times/georgia/verdana/cour + bold) with DejaVu
fallbacks; the warning heading always gets a face with a real bold weight.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from label_assay.match.brand import BrandVerdict, match_brand
from label_assay.match.warning import WarningVerdict, compare_warning
from label_assay.rulebook.loader import load_rulebook
from label_assay.text.numbers import parse_alcohol_content

DEFAULT_SEED = 20260717
DEFAULT_COUNT = 24
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "labels"

_MARGIN = 45
# Warning render size for the not-bold defect rows, deliberately larger than the
# compliant rows' 26px: thicker strokes quantize less under Otsu + skeletonize,
# so the stroke measurement is stable across platforms, and both measured crops
# clear the detector's 14px cap-height floor with margin (>=18px measured).
_NOT_BOLD_WARN_SIZE = 34
# The 1px outline painted on the body words of the not-bold rows (see
# _wrap_warning). It widens every body stroke by exactly 2px of pixel geometry,
# independent of font file or platform, so the regular-weight heading measures
# conclusively thinner than its body and the bold check's FAIL is structural,
# not a band-edge accident. A same-face render WITHOUT the outline measures a
# stroke ratio of ~1.0 (measured 1.00-1.06 across arial/verdana/dejavu at
# 30-38px), which the detector correctly holds for review as measurement noise.
_NOT_BOLD_BODY_STROKE = 1


# --- beverage classes -----------------------------------------------------------------

@dataclass(frozen=True)
class BeverageClass:
    key: str
    family: str            # spirits | wine | malt
    class_type: str        # painted on the label AND filed on the application
    abvs: tuple[float, ...]
    nets: tuple[str, ...]


_SPIRIT_NETS = ("750 mL", "1 L", "375 mL")
_WINE_NETS = ("750 mL", "1 L", "375 mL")
_MALT_NETS = ("355 mL", "473 mL")

CLASSES: tuple[BeverageClass, ...] = (
    BeverageClass("bourbon", "spirits", "Kentucky Straight Bourbon Whiskey",
                  (40, 43, 45, 46, 47, 50), _SPIRIT_NETS),
    BeverageClass("rye", "spirits", "Straight Rye Whiskey", (40, 45, 48, 50), _SPIRIT_NETS),
    BeverageClass("vodka", "spirits", "Vodka", (40, 44, 45.5, 50), _SPIRIT_NETS),
    BeverageClass("gin", "spirits", "London Dry Gin", (40, 42, 47, 47.5), _SPIRIT_NETS),
    BeverageClass("rum", "spirits", "Gold Rum", (40, 43, 45.5, 48), _SPIRIT_NETS),
    BeverageClass("tequila", "spirits", "Tequila", (40, 42, 44, 46), _SPIRIT_NETS),
    BeverageClass("cabernet", "wine", "Cabernet Sauvignon", (12.5, 13.5, 14, 14.5), _WINE_NETS),
    BeverageClass("chardonnay", "wine", "Chardonnay", (11.5, 12.5, 13, 13.5), _WINE_NETS),
    BeverageClass("red-blend", "wine", "Red Table Wine", (12, 12.5, 13, 13.5), _WINE_NETS),
    BeverageClass("ipa", "malt", "India Pale Ale", (5.5, 6.2, 6.8, 7.2), _MALT_NETS),
    BeverageClass("lager", "malt", "Lager Beer", (4.2, 4.8, 5, 5.2), _MALT_NETS),
    BeverageClass("stout", "malt", "Stout", (4.8, 5.6, 6.5, 7), _MALT_NETS),
)

# Invented brands only — never real ones. Suffixes are applied per family below.
BRANDS: tuple[str, ...] = (
    "Copper Harrow", "Gilded Fenwick", "Hollow Crest", "Iron Meridian", "Larkspur & Sable",
    "Quarry Vane", "Marrow Gate", "Cinder Poplar", "Vestal Row", "Thornbury Vale",
    "Palisade Ember", "Windrow Atlas", "Halcyon Furrow", "Osier Bend", "Cobalt Steeple",
    "Fallow Bright", "Juniper Moraine", "Kestrel Hollow", "Lantern Shoal", "Mistral Anvil",
    "Nettle Cairn", "Ochre Pendulum", "Pewter Lark", "Quill & Ember", "Rushlight Vale",
    "Saffron Ledger", "Tallow Ridge", "Umber Sextant", "Violet Causeway", "Wicker Meridian",
    "Yarrow Spindle", "Zephyr Cask", "Alder Fathom", "Briar Compass", "Crescent Loam",
    "Dapple Forge", "Ember Sallow", "Foxglove Winch", "Garnet Trellis", "Heron Paddock",
)

_FAMILY_SUFFIXES = {
    "spirits": ("Distillery", "Distilling Co.", "Spirits"),
    "wine": ("Winery", "Cellars", "Vineyards"),
    "malt": ("Brewing Co.", "Brewery", "Beer Co."),
}

# --- palettes ---------------------------------------------------------------------------
# warning_ink is always the palette's highest-contrast ink so the statutory text
# stays OCR-legible on every background.

@dataclass(frozen=True)
class Palette:
    key: str
    bg: str
    ink: str          # primary text
    accent: str       # decoration / banner fill
    warning_ink: str
    banner_text: str  # text painted inside a banner band (fill = accent)


PALETTES: dict[str, Palette] = {
    p.key: p
    for p in (
        Palette("white", "#FFFFFF", "#1A1A1A", "#8A2431", "#111111", "#FFFFFF"),
        Palette("cream", "#F5EEDC", "#33261A", "#7A5C2E", "#26190E", "#F5EEDC"),
        Palette("kraft", "#C9A468", "#241A0C", "#52381A", "#1F1608", "#E8D9BB"),
        Palette("black", "#101010", "#F2F2F2", "#C9A55C", "#FAFAFA", "#101010"),
        Palette("navy", "#14213D", "#E7E2D3", "#D9B54A", "#F5F1E6", "#14213D"),
        Palette("burgundy", "#451522", "#F0E2C8", "#D8B47E", "#F6EBD7", "#451522"),
    )
}

# --- fonts ------------------------------------------------------------------------------

_FAMILIES: dict[str, tuple[str, str]] = {
    "arial": ("arial.ttf", "arialbd.ttf"),
    "times": ("times.ttf", "timesbd.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf"),
    "verdana": ("verdana.ttf", "verdanab.ttf"),
    "courier": ("cour.ttf", "courbd.ttf"),
    "dejavu-sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    "dejavu-serif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
}
_FALLBACK_ORDER = ("arial", "verdana", "georgia", "times", "dejavu-sans", "dejavu-serif")
# The warning heading needs a genuine weight difference for the stroke-width bold
# check. Measured on this corpus: times/georgia bold land in the detector's
# borderline band (stroke ratio ~1.1-1.2) and courier is unmeasurable at these
# sizes, so the warning block sticks to the families whose bold is unambiguous;
# the low-contrast families still appear everywhere else on the labels.
WARNING_FONTS = ("arial", "verdana", "dejavu-sans")


@lru_cache(maxsize=None)
def _family_loads(family: str) -> bool:
    """True if BOTH weights of the family load on this machine."""
    try:
        for name in _FAMILIES[family]:
            ImageFont.truetype(name, 20)
        return True
    except OSError:
        return False


@lru_cache(maxsize=None)
def _resolve_family(family: str) -> str:
    """The requested family, or the first fallback whose regular AND bold load —
    the bold check needs a real weight difference, so a family missing its bold
    face is skipped entirely rather than half-used."""
    for candidate in (family, *_FALLBACK_ORDER):
        if _family_loads(candidate):
            return candidate
    raise OSError("no usable font family found (tried Windows core fonts and DejaVu)")


@lru_cache(maxsize=None)
def _font(family: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    resolved = _resolve_family(family)
    name = _FAMILIES[resolved][0 if weight == "regular" else 1]
    return ImageFont.truetype(name, size)


# --- corpus spec ------------------------------------------------------------------------

SIZES: tuple[tuple[int, int], ...] = ((700, 900), (900, 700), (600, 1000), (1000, 1400))
LAYOUTS: tuple[str, ...] = ("classic", "rules", "banner", "framed")
DEFECTS: tuple[str, ...] = (
    "warning_title_case",
    "warning_not_bold",
    "warning_altered_text",
    "warning_missing",
    "proof_mismatch",
    "brand_mismatch",
)

# A compliant VARIANT, not a defect: the warning body painted in capitals
# (heading in capitals and bold as always). 27 CFR 16.22(a)(2) fixes the case
# of the heading words only, and TTB-approved labels routinely set the entire
# statement in capitals — these rows pin the comparator's heading/body split
# at pixel level, so a regression back to whole-statement case-sensitivity
# fails a fixture with expected_verdict pass.
N_BODY_CAPS = 2

_ALTERATIONS: tuple[tuple[str, str, str], ...] = (
    # (find, replace, note) — each produces WarningVerdict.ALTERED, asserted below.
    ("birth defects", "birth effects", '"birth defects" -> "birth effects"'),
    (", and may cause health problems", "", "dropped the health-problems clause"),
    ("impairs", "reduces", '"impairs" -> "reduces"'),
    ("According to the Surgeon General, women", "Women", "dropped the Surgeon General clause"),
)


@dataclass(frozen=True)
class LabelSpec:
    """Everything needed to render one label AND to know its ground truth."""

    filename: str
    class_key: str
    family: str
    class_type: str
    brand: str            # canonical brand (Title Case, with family suffix)
    painted_brand: str    # the exact string a perfect reader would quote
    brand_style: str      # caps | title | smallcaps
    alcohol_text: str
    net_contents: str
    palette: str
    layout: str
    size: tuple[int, int]
    display_font: str
    text_font: str
    warning_font: str
    warning_text: str | None
    warning_bold: bool
    warning_placement: str  # bottom | column | none
    ornament: str
    filed_brand: str
    defect: str
    expected_verdict: str
    notes: str


def _reference_warning() -> str:
    """The statutory text, from its single source of truth (the rulebook)."""
    rule = next(r for r in load_rulebook().rules if r.id == "health_warning_verbatim")
    assert rule.match.reference is not None
    return rule.match.reference


def _format_abv(abv: float) -> str:
    return str(int(abv)) if float(abv).is_integer() else str(abv)


def _alcohol_text(rng: random.Random, family: str, abv: float, proof: float | None) -> str:
    a = _format_abv(abv)
    if family == "spirits":
        assert proof is not None
        p = _format_abv(proof)
        template = rng.choice((
            "{a}% Alc./Vol. ({p} Proof)",
            "{a}% ALC/VOL {p} PROOF",
            "Alc. {a}% by Vol. {p} Proof",
        ))
        return template.format(a=a, p=p)
    template = rng.choice(("{a}% Alc./Vol.", "{a}% ALC/VOL", "Alc. {a}% by Vol."))
    return template.format(a=a)


def _typo_brand(rng: random.Random, brand: str) -> str:
    """A one-character slip in a real word — close enough that the matcher must
    route it to review, not auto-fail. Retries until the matcher agrees (a swap
    of identical adjacent letters, for instance, is a no-op)."""
    for _ in range(20):
        words = brand.split()
        candidates = [i for i, w in enumerate(words) if len(w) >= 5 and w[0].isalpha()]
        i = rng.choice(candidates)
        w = words[i]
        pos = rng.randrange(1, len(w) - 1)
        if rng.random() < 0.5:
            words[i] = w[:pos] + w[pos + 1:]          # drop a letter
        else:
            words[i] = w[:pos] + w[pos + 1] + w[pos] + w[pos + 2:]  # swap two letters
        mutated = " ".join(words)
        if match_brand(brand, mutated).verdict == BrandVerdict.REVIEW:
            return mutated
    raise RuntimeError(f"could not build a review-level typo for {brand!r}")


def _different_brand(rng: random.Random, painted: str, pool: list[str]) -> str:
    """A genuinely different filed brand — must be a MISMATCH for the matcher."""
    shuffled = pool[:]
    rng.shuffle(shuffled)
    for candidate in shuffled:
        if match_brand(painted, candidate).verdict == BrandVerdict.MISMATCH:
            return candidate
    raise RuntimeError("no mismatching brand found in the pool")


def _styled_brand(brand: str, style: str) -> str:
    """What actually gets painted (smallcaps renders as capitals, sized per letter)."""
    return brand.upper() if style in ("caps", "smallcaps") else brand


def build_corpus(seed: int, count: int) -> list[LabelSpec]:
    """The full deterministic plan for the corpus. Pure — no files, no fonts."""
    rng = random.Random(seed)
    reference = _reference_warning()

    class_cycle = rng.sample(CLASSES, len(CLASSES))
    palette_cycle = rng.sample(sorted(PALETTES), len(PALETTES))
    layout_cycle = rng.sample(LAYOUTS, len(LAYOUTS))
    size_cycle = rng.sample(SIZES, len(SIZES))
    brand_pool = list(BRANDS)
    picked_brands = (
        rng.sample(brand_pool, count)
        if count <= len(brand_pool)
        else [rng.choice(brand_pool) for _ in range(count)]
    )

    classes = [class_cycle[i % len(class_cycle)] for i in range(count)]

    # Defect assignment: about half the corpus is compliant; the defect types
    # cycle so each appears at least twice at the default count, and
    # proof_mismatch only ever lands on a spirits label (proof is spirits-only).
    n_defects = count - count // 2
    defect_seq = [DEFECTS[i % len(DEFECTS)] for i in range(n_defects)]
    order = list(range(count))
    rng.shuffle(order)
    assigned: dict[int, str] = {}
    for defect in defect_seq:
        for idx in order:
            if idx in assigned:
                continue
            if defect == "proof_mismatch" and classes[idx].family != "spirits":
                continue
            assigned[idx] = defect
            break
        else:
            raise RuntimeError(f"could not place defect {defect!r}")

    # The body-caps variant lands on otherwise-compliant labels. Reuses the
    # already-shuffled order and draws nothing from the rng, so every other
    # label in the corpus is byte-identical to a build without the variant.
    body_caps: set[int] = set()
    for idx in order:
        if len(body_caps) == N_BODY_CAPS:
            break
        if idx not in assigned:
            body_caps.add(idx)

    brand_mismatch_kind = 0  # alternate typo / different-name across the set
    specs: list[LabelSpec] = []
    for i in range(count):
        cls = classes[i]
        palette = palette_cycle[i % len(palette_cycle)]
        layout = layout_cycle[(i // len(size_cycle)) % len(layout_cycle)]
        size = size_cycle[i % len(size_cycle)]
        defect = "warning_body_caps" if i in body_caps else assigned.get(i, "compliant")

        suffix = rng.choice(_FAMILY_SUFFIXES[cls.family])
        brand = f"{picked_brands[i]} {suffix}"
        brand_style = rng.choice(("caps", "title", "smallcaps"))
        painted_brand = _styled_brand(brand, brand_style)

        abv = rng.choice(cls.abvs)
        proof: float | None = abv * 2 if cls.family == "spirits" else None
        if defect == "proof_mismatch":
            proof = abv * 2 + rng.choice((2, 4, 6, 8, 10))
        alcohol_text = _alcohol_text(rng, cls.family, abv, proof)
        net_contents = rng.choice(cls.nets)

        display_font = rng.choice(tuple(_FAMILIES))
        text_font = rng.choice(tuple(_FAMILIES))
        warning_font = rng.choice(WARNING_FONTS)

        # Warning column needs real width for the heading + body on one line;
        # the 600px-wide canvas can't give it, so it stays bottom-full-width.
        placement = rng.choice(("bottom", "column")) if size[0] >= 700 else "bottom"

        warning_text: str | None = reference
        warning_bold = True
        alteration_note = ""
        if defect == "warning_title_case":
            warning_text = reference.replace("GOVERNMENT WARNING:", "Government Warning:", 1)
        elif defect == "warning_body_caps":
            warning_text = reference.upper()  # heading already capital; body joins it
        elif defect == "warning_not_bold":
            warning_bold = False
            # This defect exists to exercise the stroke-width measurement, which
            # needs real body text on the heading's own OCR line. A narrow column
            # can be read with the heading as its own line — a layout the checker
            # (correctly) abstains on — so the defect pins the full-width bottom
            # placement. It also pins the measurement-friendly rendering the
            # strict corpus test depends on: the white palette (maximum
            # contrast, dark-on-light, no polarity flip) and a canvas wide
            # enough that the larger not-bold warning (see _paint_warning and
            # _NOT_BOLD_BODY_STROKE) still puts several body words on the
            # heading's line. The decisive part of the render is the body
            # outline: it makes the regular-weight heading structurally thinner
            # than its body, so the measured ratio sits deep in the detector's
            # conclusive not-bold band on every platform instead of on a band
            # edge that OCR geometry can cross. None of the pins consume the
            # rng, keeping every other label byte-identical.
            placement = "bottom"
            palette = "white"
            if size[0] < 900:
                size = (1000, 1400)
        elif defect == "warning_altered_text":
            find, replace, alteration_note = rng.choice(_ALTERATIONS)
            warning_text = reference.replace(find, replace, 1)
        elif defect == "warning_missing":
            warning_text = None
            placement = "none"

        filed_brand = brand
        if defect == "brand_mismatch":
            if brand_mismatch_kind % 2 == 0:
                filed_brand = _typo_brand(rng, brand)
                kind_note = f"filed brand {filed_brand!r} is a typo-level variant -> review"
                expected = "needs_review"
                assert match_brand(painted_brand, filed_brand).verdict == BrandVerdict.REVIEW
            else:
                filed_brand = _different_brand(
                    rng, painted_brand, [f"{b} {suffix}" for b in brand_pool if b != picked_brands[i]]
                )
                kind_note = f"filed brand {filed_brand!r} is a different brand -> fail"
                expected = "fail"
            brand_mismatch_kind += 1
        else:
            assert match_brand(painted_brand, filed_brand).verdict == BrandVerdict.MATCH

        # The data-level ground truth is asserted against the engine's own
        # matchers here; the pixel-dependent not-bold rows and the missing-
        # warning rows are asserted through the engine by the test suite.
        content = parse_alcohol_content(alcohol_text)
        assert content is not None, f"unparseable alcohol text: {alcohol_text!r}"
        if defect == "proof_mismatch":
            assert content.proof_matches_abv is False
        elif cls.family == "spirits":
            assert content.proof_matches_abv is True
        if warning_text is not None:
            got = compare_warning(warning_text, reference).verdict
            wanted = {
                "warning_title_case": WarningVerdict.CAPITALIZATION,
                "warning_altered_text": WarningVerdict.ALTERED,
            }.get(defect, WarningVerdict.MATCH)
            assert got == wanted, f"{defect}: warning comparator returned {got}"

        expected_verdict, note = {
            "compliant": ("pass", "all four checks pass"),
            "warning_body_caps": (
                "pass", "statement body painted in capitals; 16.22(a)(2) fixes only the heading case -> pass"
            ),
            "warning_title_case": (
                "fail", 'heading painted "Government Warning:" — 16.22(a)(2) capitalization -> fail'
            ),
            "warning_not_bold": (
                "fail", "heading in regular weight, thinner than its body -> bold check fail"
            ),
            "warning_altered_text": ("fail", f"statutory text altered ({alteration_note}) -> fail"),
            "warning_missing": (
                "needs_review", "no warning block painted; absence routes to review, never auto-fail"
            ),
            "proof_mismatch": (
                "fail", f"{_format_abv(proof)} proof printed against {_format_abv(abv)}% ABV -> fail"
            ) if proof is not None else ("fail", ""),
            "brand_mismatch": (expected, kind_note) if defect == "brand_mismatch" else ("", ""),
        }[defect]

        notes = (
            f"{cls.class_type}; layout={layout}; palette={palette}; "
            f"size={size[0]}x{size[1]}; warning={placement}; {note}"
        )

        specs.append(
            LabelSpec(
                filename=f"label_{i:03d}.png",
                class_key=cls.key,
                family=cls.family,
                class_type=cls.class_type,
                brand=brand,
                painted_brand=painted_brand,
                brand_style=brand_style,
                alcohol_text=alcohol_text,
                net_contents=net_contents,
                palette=palette,
                layout=layout,
                size=size,
                display_font=display_font,
                text_font=text_font,
                warning_font=warning_font,
                warning_text=warning_text,
                warning_bold=warning_bold,
                warning_placement=placement,
                ornament=rng.choice(("rule", "diamonds", "none")),
                filed_brand=filed_brand,
                defect=defect,
                expected_verdict=expected_verdict,
                notes=notes,
            )
        )
    return specs


# --- rendering --------------------------------------------------------------------------

def _wrap_warning(
    d: ImageDraw.ImageDraw, spec: LabelSpec, width: int, size: int
) -> list[list[tuple[str, ImageFont.FreeTypeFont, int]]]:
    """Word-wrapped warning lines as (word, font, stroke_width) runs; the first
    two words are the heading. The heading and the words after it share the
    first line, which is what the stroke-width bold check measures against.

    On the not-bold rows the heading and body use the same regular face at the
    same size (no bold file involved) and the body words alone carry the
    _NOT_BOLD_BODY_STROKE outline, so the heading is structurally the thinnest
    text in its own statement — the geometry the conclusive not-bold band
    describes."""
    assert spec.warning_text is not None
    regular = _font(spec.warning_font, "regular", size)
    heading = _font(spec.warning_font, "bold" if spec.warning_bold else "regular", size)
    body_stroke = 0 if spec.warning_bold else _NOT_BOLD_BODY_STROKE
    space = d.textlength(" ", font=regular)
    lines: list[list[tuple[str, ImageFont.FreeTypeFont, int]]] = [[]]
    x = 0.0
    for i, word in enumerate(spec.warning_text.split(" ")):
        is_heading = i < 2
        font = heading if is_heading else regular
        w = d.textlength(word, font=font)
        if x + w > width and lines[-1]:
            lines.append([])
            x = 0.0
        lines[-1].append((word, font, 0 if is_heading else body_stroke))
        x += w + space
    return lines


def _paint_warning(d: ImageDraw.ImageDraw, spec: LabelSpec) -> int:
    """Paint the warning block (if any); returns the y where content must stop."""
    W, H = spec.size
    pal = PALETTES[spec.palette]
    if spec.warning_text is None:
        return int(H * 0.80)

    # Larger than a true 1-2 mm warning, deliberately: the corpus exists to
    # exercise the checks, and the bold check abstains when the smaller glyphs
    # in its body sample drop under a ~14 px cap height. Column text is smaller
    # so the heading still shares its first line with real body text — the
    # geometry the stroke-width comparison needs.
    if spec.warning_placement == "column":
        size = 22
        col_w = max(400, (W - 2 * _MARGIN) // 2)
        x0 = W - _MARGIN - col_w
        width = col_w
    else:
        size = 26 if spec.warning_bold else _NOT_BOLD_WARN_SIZE
        x0 = _MARGIN
        width = W - 2 * _MARGIN

    lines = _wrap_warning(d, spec, width, size)
    line_h = size + 8
    y0 = H - 40 - len(lines) * line_h
    regular = _font(spec.warning_font, "regular", size)
    space = d.textlength(" ", font=regular)
    y = y0
    for line in lines:
        x = float(x0)
        for word, font, stroke in line:
            d.text(
                (x, y), word, font=font, fill=pal.warning_ink,
                stroke_width=stroke, stroke_fill=pal.warning_ink,
            )
            x += d.textlength(word, font=font) + space
        y += line_h
    return y0 - 30


def _fit_size(d: ImageDraw.ImageDraw, text: str, family: str, weight: str,
              start: int, max_w: int) -> int:
    size = start
    while size > 14 and d.textlength(text, font=_font(family, weight, size)) > max_w:
        size -= 2
    return size


def _paint_smallcaps(d: ImageDraw.ImageDraw, cx: float, y: float, text: str,
                     family: str, size: int, fill: str) -> None:
    """A small-caps look: word-initial capitals full-size, the rest ~3/4 size."""
    big = _font(family, "bold", size)
    small = _font(family, "bold", int(size * 0.75))
    fonts = []
    new_word = True
    for ch in text:
        fonts.append(big if new_word and ch.isalpha() else small)
        new_word = not ch.isalpha()
    total = sum(d.textlength(ch, font=f) for ch, f in zip(text, fonts))
    x = cx - total / 2
    for ch, f in zip(text, fonts):
        d.text((x, y), ch, font=f, fill=fill, anchor="ls")
        x += d.textlength(ch, font=f)


def _paint_brand(d: ImageDraw.ImageDraw, spec: LabelSpec, cx: float, y: float,
                 fill: str, max_w: int, anchor: str = "mm") -> None:
    text = spec.painted_brand
    size = _fit_size(d, text, spec.display_font, "bold", 42 if spec.size[0] >= 900 else 36, max_w)
    if spec.brand_style == "smallcaps":
        # anchor is horizontal-center by construction; y treated as baseline
        _paint_smallcaps(d, cx, y + size / 2, text, spec.display_font, size, fill)
    else:
        d.text((cx, y), text, font=_font(spec.display_font, "bold", size), fill=fill, anchor=anchor)


def _paint_ornament(d: ImageDraw.ImageDraw, spec: LabelSpec, cx: float, y: float) -> None:
    pal = PALETTES[spec.palette]
    if spec.ornament == "rule":
        d.line((cx - 70, y, cx + 70, y), fill=pal.accent, width=3)
    elif spec.ornament == "diamonds":
        for k in (-1, 0, 1):
            x = cx + k * 34
            d.polygon(
                [(x, y - 7), (x + 7, y), (x, y + 7), (x - 7, y)],
                fill=pal.accent,
            )
    # "none": nothing


def _paint_content(d: ImageDraw.ImageDraw, spec: LabelSpec, content_bottom: int) -> None:
    """Brand / class / alcohol / net contents plus decoration, all above
    content_bottom so nothing can ever overlap the warning block."""
    W, _H = spec.size
    pal = PALETTES[spec.palette]
    cb = content_bottom
    inner = W - 2 * _MARGIN
    cx = W / 2
    text_size = 24 if W < 700 else 26
    small_size = 22

    def center(text: str, y: float, size: int, weight: str = "regular") -> None:
        fitted = _fit_size(d, text, spec.text_font, weight, size, inner)
        d.text((cx, y), text, font=_font(spec.text_font, weight, fitted), fill=pal.ink, anchor="mm")

    if spec.layout == "classic":
        _paint_brand(d, spec, cx, 0.20 * cb, pal.ink, inner)
        _paint_ornament(d, spec, cx, 0.32 * cb)
        center(spec.class_type, 0.44 * cb, text_size)
        center(spec.alcohol_text, 0.60 * cb, small_size)
        center(spec.net_contents, 0.72 * cb, small_size)

    elif spec.layout == "rules":
        x = _MARGIN
        _paint_brand(d, spec, x + inner / 2, 0.16 * cb, pal.ink, inner)
        d.line((x, 0.28 * cb, W - _MARGIN, 0.28 * cb), fill=pal.accent, width=3)
        left = _fit_size(d, spec.class_type, spec.text_font, "regular", text_size, inner)
        d.text((x, 0.40 * cb), spec.class_type,
               font=_font(spec.text_font, "regular", left), fill=pal.ink, anchor="lm")
        d.text((x, 0.54 * cb), spec.alcohol_text,
               font=_font(spec.text_font, "regular", small_size), fill=pal.ink, anchor="lm")
        d.text((x, 0.64 * cb), spec.net_contents,
               font=_font(spec.text_font, "regular", small_size), fill=pal.ink, anchor="lm")
        d.line((x, 0.74 * cb, W - _MARGIN, 0.74 * cb), fill=pal.accent, width=1)

    elif spec.layout == "banner":
        band_h = int(0.24 * cb)
        d.rectangle((0, 0, W, band_h), fill=pal.accent)
        _paint_brand(d, spec, cx, band_h / 2, pal.banner_text, inner)
        center(spec.class_type, 0.44 * cb, text_size)
        _paint_ornament(d, spec, cx, 0.56 * cb)
        center(spec.alcohol_text, 0.66 * cb, small_size)
        center(spec.net_contents, 0.78 * cb, small_size)

    elif spec.layout == "framed":
        W_, H_ = spec.size
        d.rectangle((14, 14, W_ - 14, H_ - 14), outline=pal.accent, width=3)
        d.rectangle((24, 24, W_ - 24, H_ - 24), outline=pal.accent, width=1)
        _paint_brand(d, spec, cx, 0.24 * cb, pal.ink, inner - 30)
        _paint_ornament(d, spec, cx, 0.36 * cb)
        center(spec.class_type, 0.48 * cb, text_size)
        center(spec.alcohol_text, 0.62 * cb, small_size)
        center(spec.net_contents, 0.74 * cb, small_size)

    else:  # pragma: no cover - build_corpus only emits the four layouts
        raise ValueError(f"unknown layout {spec.layout!r}")


def render(spec: LabelSpec, out_dir: Path) -> Path:
    img = Image.new("RGB", spec.size, PALETTES[spec.palette].bg)
    d = ImageDraw.Draw(img)
    content_bottom = _paint_warning(d, spec)
    _paint_content(d, spec, content_bottom)
    path = out_dir / spec.filename
    img.save(path)
    return path


# --- output -----------------------------------------------------------------------------

def generate(out_dir: Path, seed: int = DEFAULT_SEED, count: int = DEFAULT_COUNT) -> list[LabelSpec]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("label_*.png"):
        stale.unlink()
    specs = build_corpus(seed, count)
    for spec in specs:
        render(spec, out_dir)

    # LF line endings, explicitly: the csv module defaults to CRLF, but the
    # committed copies are checked out with LF (.gitattributes eol=lf), and the
    # suite byte-compares generated CSVs against committed ones on fresh clones.
    with (out_dir / "applications.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["filename", "brand_name", "class_type"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(
            {"filename": s.filename, "brand_name": s.filed_brand, "class_type": s.class_type}
            for s in specs
        )

    with (out_dir / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["filename", "defect", "expected_verdict", "notes"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(
            {
                "filename": s.filename,
                "defect": s.defect,
                "expected_verdict": s.expected_verdict,
                "notes": s.notes,
            }
            for s in specs
        )
    return specs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    specs = generate(args.out, seed=args.seed, count=args.count)
    by_defect: dict[str, int] = {}
    for s in specs:
        by_defect[s.defect] = by_defect.get(s.defect, 0) + 1
    print(f"wrote {len(specs)} labels + applications.csv + manifest.csv -> {args.out}")
    for defect in sorted(by_defect):
        print(f"  {defect}: {by_defect[defect]}")


if __name__ == "__main__":
    main()
