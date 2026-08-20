"""Skeletal rig + clip player. Pure data, no GTK — see test_rig.py."""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

# Animated values are deltas: 0 for x/y/rotation, and 0 around 1 for scale* and alpha.


def _linear(t):
    return t


def _ease_in_out(t):
    return t * t * (3.0 - 2.0 * t)


def _ease_out(t):
    return 1.0 - (1.0 - t) ** 2


def _step(t):
    return 0.0


EASINGS = {
    "linear": _linear,
    "easeInOut": _ease_in_out,
    "easeOut": _ease_out,
    "step": _step,
}


@dataclass
class Part:
    id: str
    texture: str | None = None
    z: int = 0
    parent: str | None = None
    anchor: tuple = (0.5, 0.5)
    position: tuple = (0.0, 0.0)


@dataclass
class Skin:
    id: str
    dir: Path
    parts: list
    ppu: float
    reference_height: float
    gaze: dict = field(default_factory=dict)
    eyelids: list = field(default_factory=list)
    attachments: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)

    @classmethod
    def load(cls, d: Path) -> "Skin":
        raw = json.loads((d / "skin.json").read_text())
        rig = raw["rig"]
        parts = [
            Part(
                id=p["id"],
                texture=p.get("texture"),
                z=p.get("z", 0),
                parent=p.get("parent"),
                anchor=tuple(p.get("anchor", (0.5, 0.5))),
                position=tuple(p.get("position", (0.0, 0.0))),
            )
            for p in rig["parts"]
        ]
        return cls(
            id=raw["id"],
            dir=d,
            parts=parts,
            ppu=rig["pixelsPerUnit"],
            reference_height=rig.get("referenceHeight", 100.0),
            gaze=rig.get("gaze", {}),
            eyelids=rig.get("eyelids", []),
            attachments=rig.get("attachments", {}),
            names=raw.get("displayName", {}),
        )


@dataclass
class Track:
    part: str
    property: str | None = None
    keys: list = field(default_factory=list)
    easing: str = "linear"
    frames: list | None = None
    fps: float = 1.0

    def sample(self, t: float) -> float:
        keys = self.keys
        if not keys:
            return 0.0
        if t <= keys[0][0]:
            return keys[0][1]
        for a, b in zip(keys, keys[1:]):
            if t <= b[0]:
                span = b[0] - a[0]
                u = 0.0 if span <= 0 else (t - a[0]) / span
                ease = EASINGS.get(a[2] if len(a) > 2 else self.easing, _linear)
                return a[1] + (b[1] - a[1]) * ease(u)
        return keys[-1][1]

    def frame(self, t: float, loop: bool) -> str:
        i = int(t * self.fps)
        return self.frames[i % len(self.frames) if loop else min(i, len(self.frames) - 1)]


@dataclass
class Clip:
    id: str
    duration: float
    loop: bool
    tracks: list
    blend_in: float = 0.0
    effect: str | None = None
    sound: str | None = None

    @classmethod
    def load(cls, f: Path) -> "Clip":
        raw = json.loads(f.read_text())
        tracks = [
            Track(
                part=t["part"],
                property=t.get("property"),
                keys=t.get("keys", []),
                easing=t.get("easing", "linear"),
                frames=t.get("frames"),
                fps=t.get("fps", 1.0),
            )
            for t in raw["tracks"]
        ]
        return cls(
            id=raw["id"],
            duration=raw["duration"],
            loop=raw.get("loop", False),
            tracks=tracks,
            blend_in=raw.get("blendIn", 0.0),
            effect=raw.get("effect"),
            sound=raw.get("sound"),
        )

    def apply(self, t: float, out: dict, weight: float = 1.0):
        """Accumulate this clip's deltas at time t into `out` scaled by weight."""
        tt = t % self.duration if self.loop and self.duration > 0 else min(t, self.duration)
        for tr in self.tracks:
            slot = out.setdefault(tr.part, {})
            if tr.frames:
                if weight >= 0.5:
                    slot["texture"] = tr.frame(tt, self.loop)
            else:
                slot[tr.property] = slot.get(tr.property, 0.0) + tr.sample(tt) * weight


class Library:
    """All clips + the state machine description."""

    def __init__(self, animation_dir: Path):
        self.clips = {f.stem: Clip.load(f) for f in (animation_dir / "clips").glob("*.json")}
        meta = json.loads((animation_dir / "animations.json").read_text())
        self.states = meta["states"]
        self.mood_map = meta.get("moodMap", {})
        self.reactions = meta.get("reactions", {})
        self.effects = meta.get("effects", {})


def _pick(weighted, rng):
    total = sum(c.get("weight", 1) for c in weighted)
    r = rng.uniform(0, total)
    for c in weighted:
        r -= c.get("weight", 1)
        if r <= 0:
            return c["clip"]
    return weighted[-1]["clip"]


class Animator:
    """Plays a state: a weighted base clip, additive overlays, and idle breaks."""

    def __init__(self, lib: Library, state: str = "idle", rng=None):
        self.lib = lib
        self.rng = rng or random.Random()
        self.state = None
        self.base = None
        self.base_t = 0.0
        self.prev = None  # (clip, t, remaining_blend, blend_len)
        self.overlays = []
        self.extra = []  # overlays that outlive state changes, e.g. the mouth
        self.overlay_t = 0.0
        self.oneshot = None  # (clip, t) — break or reaction, outranks base
        self.next_break = math.inf
        self.set_state(state)

    # -- state control -------------------------------------------------
    def set_state(self, name: str, force: bool = False):
        if name == self.state and not force:
            return
        spec = self.lib.states.get(name)
        if spec is None:
            return
        self.state = name
        new = self.lib.clips.get(_pick(spec["base"], self.rng))
        if self.base is not None and new is not None:
            self.prev = [self.base, self.base_t, new.blend_in, new.blend_in]
        self.base, self.base_t = new, 0.0
        self.overlays = [self.lib.clips[c] for c in spec.get("overlays", []) if c in self.lib.clips]
        self.oneshot = None
        self._arm_break()

    def _arm_break(self):
        br = (self.lib.states.get(self.state) or {}).get("breaks")
        if br:
            lo, hi = br["afterSeconds"]
            self.next_break = self.rng.uniform(lo, hi)
        else:
            self.next_break = math.inf

    def play_once(self, clip_id: str):
        """Interrupt with a one-shot clip (reaction, break); base resumes after."""
        clip = self.lib.clips.get(clip_id)
        if clip:
            self.oneshot = [clip, 0.0]

    def talk(self, on: bool):
        """Layer the mouth animation over whatever the cat is otherwise doing.

        `talk` belongs to no state — it swaps head.png for head_talk.png, and
        must keep running while the cat walks, sits or reacts.
        """
        clip = self.lib.clips.get("talk")
        if clip is None:
            return
        if on and clip not in self.extra:
            self.extra.append(clip)
        elif not on and clip in self.extra:
            self.extra.remove(clip)

    def react(self, name: str) -> bool:
        r = self.lib.reactions.get(name)
        if not r:
            return False
        self.play_once(r["clip"])
        return True

    @property
    def busy(self) -> bool:
        return self.oneshot is not None

    # -- per-frame -----------------------------------------------------
    def update(self, dt: float) -> dict:
        self.overlay_t += dt

        if self.oneshot:
            self.oneshot[1] += dt
            if self.oneshot[1] >= self.oneshot[0].duration and not self.oneshot[0].loop:
                self.oneshot = None
                self._arm_break()
        else:
            self.base_t += dt
            self.next_break -= dt
            if self.next_break <= 0:
                spec = self.lib.states.get(self.state) or {}
                self.play_once(_pick(spec["breaks"]["clips"], self.rng))
                self.next_break = math.inf
            elif self.base and not self.base.loop and self.base_t >= self.base.duration:
                self.set_state(self.state, force=True)

        pose: dict = {}
        if self.prev:
            self.prev[2] -= dt
            self.prev[1] += dt
            if self.prev[2] <= 0:
                self.prev = None
        w = 1.0
        if self.prev:
            w = 1.0 - self.prev[2] / self.prev[3]
            self.prev[0].apply(self.prev[1], pose, 1.0 - w)

        active = self.oneshot or (self.base, self.base_t)
        if active[0]:
            active[0].apply(active[1], pose, w if active is not self.oneshot else 1.0)
        for ov in self.overlays + self.extra:
            ov.apply(self.overlay_t, pose, 1.0)
        return pose
