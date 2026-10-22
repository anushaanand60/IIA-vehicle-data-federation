"""Photo of a vehicle -> its make, model and colour, to check against what the sources hold.

PHASE3-ADDED (C2: make, model and colour from the photo) -- the whole module is new in phase 3.

A plate photo used to give the matcher only the plate text. A photo that shows the car (a front or rear
shot, like a road camera's) also shows the badge, the body shape and the paint: the same make / model /
colour evidence the CAM source records for every sighting, which the matcher's context features and the
phase-2 clone veto already use. This module reads that evidence from the photo.

    1. close-up gate    a photo where the plate fills the frame shows no car: no guess is made, and the
                        plate-only path works exactly as before. Decided from the plate's box (EasyOCR's
                        polygon), so a close-up never even loads the vision model.
    2. zero-shot CLIP   one image embedding scored against text prompts ("a photo of a white car").
                        Zero-shot, so no training data is needed and the vocabulary is the sources' own:
                        every make and model REG registers, plus the common Indian makes it does not, so
                        an unfamiliar car is not forced onto a registered make.
    3. abstain          a guess is reported only when CLIP is clearly sure (MIN_P); otherwise "not sure",
                        which the matcher treats as no evidence at all (agreement feature 0).

Optional, like EasyOCR: open_clip and its weights may be missing on a laptop, and then every caller gets
`VisionUnavailable` and carries on with the plate alone.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

INSTALL_HINT = "pip install open_clip_torch"
CLIP_ARCH, CLIP_WEIGHTS = "ViT-B-32", "laion2b_s34b_b79k"

# Close-up gate: plate box area / image area, or plate width / image width, above these = plate only.
# A plate on a whole car (a front shot) fills ~1-5 % of the frame and ~20-35 % of its width.
CLOSEUP_AREA = 0.12
CLOSEUP_WIDTH = 0.6
# PHASE3-REPLACED (C2 dataset: thresholds chosen on the 8 training cars by scripts/evaluate_vision.py; the
# CLIP close-up gate and its "vehicle" threshold are gone, see guesses()):
# MIN_P = {"make": 0.5, "model": 0.5, "colour": 0.5, "vehicle": 0.5}
MIN_P = {"make": 0.7, "model": 0.7, "colour": 0.6}

# REG's makes and models (VEHICLE_REGISTRATION), plus makes common on Indian roads that REG does not hold.
MODELS: Dict[str, Tuple[str, ...]] = {
    "Maruti Suzuki": ("Swift", "Baleno"), "Hyundai": ("Creta", "i20"), "Tata": ("Nexon", "Punch"),
    "Mahindra": ("XUV700",), "Kia": ("Seltos",), "Toyota": ("Innova",), "Honda": ("City",),
    "Renault": (), "Skoda": (), "Volkswagen": (), "MG": (), "Nissan": (), "Ford": (),
}
# REG's six colours first; the rest exist so that a brown car is not called grey.
COLOURS = ("White", "Black", "Silver", "Grey", "Red", "Blue", "Brown", "Green", "Yellow", "Orange", "Beige")
# PHASE3-REPLACED (C2 dataset: no CLIP close-up gate any more):
# KIND_PROMPTS = ("a photo of a car on a road or in a driveway",
#                 "a close-up photo of a vehicle number plate")


class VisionUnavailable(RuntimeError):
    """open_clip (or its weights) is not available on this machine."""


@dataclass
class VehicleView:
    kind: str                         # "vehicle", "plate_closeup"
    make: Optional[str] = None
    model: Optional[str] = None
    colour: Optional[str] = None
    p: Dict[str, float] = field(default_factory=dict)   # attribute -> CLIP's probability for its top label
    notes: List[str] = field(default_factory=list)

    def words(self) -> str:
        if self.kind != "vehicle":
            return "close-up of the plate: no car visible, so no make or colour is inferred"
        seen = [self.colour.lower() if self.colour else None, self.make, self.model]
        return " ".join(s for s in seen if s) or "a car, but make and colour are not clear enough to say"


# ------------------------------------------------------------------ close-up gate (no model needed)

def plate_fraction(image_size: Tuple[int, int], box: Sequence[Sequence[float]]) -> Tuple[float, float]:
    """(area fraction, width fraction) of the plate's polygon in the image."""
    if not box:
        return 0.0, 0.0
    w, h = image_size
    xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
    bw, bh = max(xs) - min(xs), max(ys) - min(ys)
    return (bw * bh) / float(w * h or 1), bw / float(w or 1)


def is_closeup(image_size: Tuple[int, int], box: Sequence[Sequence[float]]) -> bool:
    area, width = plate_fraction(image_size, box)
    return area >= CLOSEUP_AREA or width >= CLOSEUP_WIDTH


# ------------------------------------------------------------------ CLIP

Scorer = Callable[[Any, Sequence[str]], List[float]]  # (PIL image, prompts) -> softmax probabilities

_CLIP: Any = None


def _clip_scorer() -> Scorer:
    """Lazy singleton: the model loads once per process (a few seconds on CPU)."""
    global _CLIP
    if _CLIP is None:
        try:
            import open_clip  # noqa: PLC0415 - deliberately lazy: torch import costs seconds
            import torch  # noqa: PLC0415
        except Exception as exc:
            raise VisionUnavailable(f"open_clip is not installed ({exc}). Install it with: {INSTALL_HINT}") from exc
        try:
            model, _, preprocess = open_clip.create_model_and_transforms(CLIP_ARCH, pretrained=CLIP_WEIGHTS)
            tokenizer = open_clip.get_tokenizer(CLIP_ARCH)
        except Exception as exc:  # weights missing and no network to fetch them
            raise VisionUnavailable(f"CLIP weights could not be loaded ({exc}).") from exc
        model.eval()
        text_cache: Dict[Tuple[str, ...], Any] = {}

        def score(image: Any, prompts: Sequence[str]) -> List[float]:
            with torch.no_grad():
                key = tuple(prompts)
                if key not in text_cache:
                    t = model.encode_text(tokenizer(list(prompts)))
                    text_cache[key] = t / t.norm(dim=-1, keepdim=True)
                img = model.encode_image(preprocess(image).unsqueeze(0))
                img = img / img.norm(dim=-1, keepdim=True)
                return (100.0 * img @ text_cache[key].T).softmax(dim=-1)[0].tolist()
        _CLIP = score
    return _CLIP


def _top(probs: Sequence[float], labels: Sequence[str]) -> Tuple[str, float]:
    i = max(range(len(labels)), key=lambda k: probs[k])
    return labels[i], float(probs[i])


# The scoring and the decision are separate (PHASE3-ADDED, C2 dataset: scripts/evaluate_vision.py tunes the
# abstention thresholds on the training photos from the raw guesses, without running CLIP again per threshold).
Guesses = Dict[str, Any]


def _vehicle_labels() -> List[Tuple[str, Optional[str]]]:
    """One label per REG model, plus one per make REG has no model of ("a Renault car")."""
    return [(m, md) for m, mds in MODELS.items() for md in mds] + [(m, None) for m, mds in MODELS.items() if not mds]


def guesses(image_bytes: bytes, plate_box: Sequence[Sequence[float]] = (), *,
            scorer: Optional[Scorer] = None) -> Guesses:
    """CLIP's top label and probability per attribute, before any threshold. A close-up by geometry returns
    {"closeup": (area, width)} without running CLIP at all."""
    from PIL import Image  # noqa: PLC0415 - Pillow comes with streamlit / easyocr

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if plate_box and is_closeup(image.size, plate_box):
        return {"closeup": plate_fraction(image.size, plate_box)}
    score = scorer or _clip_scorer()
    # PHASE3-REPLACED (C2 dataset: on the 39 labelled photos CLIP called 13 of 13 bumper crops and 5 of 13 whole
    # cars "a close-up of a number plate" whenever the plate was prominent, then read nothing from them -- yet on
    # those crops it named make and colour right with p >= 0.9. The plate-box geometry gate was right on 3 of 3
    # close-ups, so it decides alone; and "a photo of a Tata car" said Mahindra (0.83) where "a photo of a Tata
    # Nexon" said Nexon (0.97), so make and model are one softmax and the make is the sum over its models):
    # out: Guesses = {"kind": _top(score(image, KIND_PROMPTS), ("vehicle", "plate_closeup"))}
    # makes = list(MODELS)
    # out["make"] = _top(score(image, [f"a photo of a {m} car" for m in makes]), makes)
    # pairs = [(m, md) for m, mds in MODELS.items() for md in mds]
    # labels = [f"{m} {md}" for m, md in pairs]
    # best, p_model = _top(score(image, [f"a photo of a {label}" for label in labels]), labels)
    # out["model"] = (pairs[labels.index(best)], p_model)
    labels = _vehicle_labels()
    probs = score(image, [f"a photo of a {m} {md}" if md else f"a photo of a {m} car" for m, md in labels])
    by_make: Dict[str, float] = {}
    for (m, _), p in zip(labels, probs):
        by_make[m] = by_make.get(m, 0.0) + p
    out: Guesses = {"make": max(by_make.items(), key=lambda kv: kv[1])}
    i = max(range(len(labels)), key=lambda k: probs[k])
    out["model"] = (labels[i], float(probs[i])) if labels[i][1] else ((labels[i][0], None), 0.0)
    out["colour"] = _top(score(image, [f"a photo of a {c.lower()} car" for c in COLOURS]), COLOURS)
    return out


def decide(g: Guesses, min_p: Optional[Dict[str, float]] = None) -> VehicleView:
    """Apply the abstention thresholds: report a guess only when CLIP is sure enough."""
    min_p = {**MIN_P, **(min_p or {})}
    if "closeup" in g:
        area, width = g["closeup"]
        return VehicleView("plate_closeup", notes=[f"the plate fills {area:.0%} of the photo ({width:.0%} of its "
                                                   "width): a close-up, so only the plate is read"])
    view = VehicleView("vehicle")
    make, p_make = g["make"]
    view.p["make"] = p_make
    if p_make >= min_p["make"]:
        view.make = make
    else:
        view.notes.append(f"make not clear (best guess {make}, p={p_make:.2f})")
    (best_make, best_model), p_model = g["model"]
    view.p["model"] = p_model
    # a model is reported only under a make it agrees with: "Kia Creta" is a contradiction, not a guess
    if best_model and p_model >= min_p["model"] and view.make in (None, best_make):
        view.make, view.model = best_make, best_model
    elif best_model and p_model >= min_p["model"]:
        view.notes.append(f"model guess {best_make} {best_model} disagrees with the make guess; model left out")
    colour, p_colour = g["colour"]
    view.p["colour"] = p_colour
    if p_colour >= min_p["colour"]:
        view.colour = colour
    else:
        view.notes.append(f"colour not clear (best guess {colour}, p={p_colour:.2f})")
    return view


def analyse(image_bytes: bytes, plate_box: Sequence[Sequence[float]] = (), *,
            scorer: Optional[Scorer] = None) -> VehicleView:
    """What the photo shows of the vehicle. Raises VisionUnavailable only when a car is visible and CLIP
    is needed but missing; a close-up never needs it."""
    return decide(guesses(image_bytes, plate_box, scorer=scorer))
