"""
game.py — Jump game logic (Chrome-dino style). Rendering lives in main.py.

Jump model
----------
* A trigger starts a jump. Initial velocity scales with how far the activation
  is above threshold (light clench → low hop, hard clench → high jump).
* While the clench is HELD during the ascent, gravity is reduced for a few
  frames, so holding lengthens the jump (capped).
* When the clench is RELEASED early, the ascent is cut short (velocity clamped
  to DROP_VEL), exactly like releasing the key in the Chrome dinosaur game.
"""
import random

GAME_W   = 750
GAME_H   = 600
GROUND_Y = 440
FPS      = 60

# Background eras of human advancement — a new one every ERA_SCORE points.
ERAS = [
    "Stone age", "First villages", "Ancient Egypt", "Classical Rome",
    "Middle ages", "Industrial age", "Modern city", "Space age",
]
ERA_SCORE      = 400
ERA_BANNER_FR  = 150          # frames the "new era" banner stays up
SCENERY_PARALLAX = 0.4        # scenery scrolls at this fraction of game speed


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


class Player:
    W, H          = 34, 48
    GRAVITY       = 0.62
    JUMP_VEL_MIN  = -9.6      # light clench
    JUMP_VEL_MAX  = -11.6     # hard clench
    DROP_VEL      = -3.5      # ascent clamps to this when released early
    MIN_JUMP_H    = 26        # must rise this far before a release can cut the jump
    HOLD_GRAVITY  = 0.36      # gravity while held and ascending
    HOLD_FRAMES   = 14        # max frames of reduced gravity

    def __init__(self):
        self.x: float = 110.0
        self.ground: float = float(GROUND_Y - self.H)
        self.y: float = self.ground
        self.vy: float = 0.0
        self.on_ground: bool = True
        self.jump_held: bool = False
        self._reached_min: bool = False
        self._hold_left: int = 0
        self.run_phase: float = 0.0
        self.last_strength: float = 0.0

    def jump(self, strength: float = 0.0):
        if not self.on_ground:
            return
        s = _clamp(strength)
        self.last_strength = s
        self.vy = self.JUMP_VEL_MIN + (self.JUMP_VEL_MAX - self.JUMP_VEL_MIN) * s
        self.on_ground = False
        self.jump_held = True
        self._reached_min = False
        self._hold_left = self.HOLD_FRAMES

    def release(self):
        if self.on_ground or not self.jump_held:
            return
        self.jump_held = False
        if self._reached_min and self.vy < self.DROP_VEL:
            self.vy = self.DROP_VEL

    def update(self, speed: float, held: bool):
        if self.on_ground:
            self.run_phase += speed / 22.0
            return
        if self.jump_held and not held:
            self.release()
        g = self.GRAVITY
        if self.jump_held and self.vy < 0 and self._hold_left > 0:
            g = self.HOLD_GRAVITY
            self._hold_left -= 1
        self.vy += g
        self.y  += self.vy
        if self.ground - self.y >= self.MIN_JUMP_H:
            self._reached_min = True
        if self.y >= self.ground:
            self.y = self.ground
            self.vy = 0.0
            self.on_ground = True
            self.jump_held = False

    @property
    def height(self) -> float:
        return self.ground - self.y


class Obstacle:
    # (width, height, n_stems)  — stems are drawn as separate cacti
    SMALL   = (16, 34, 1)
    LARGE   = (24, 54, 1)
    SMALL2  = (36, 34, 2)
    SMALL3  = (56, 34, 3)
    LARGE2  = (52, 54, 2)
    TALL    = (14, 78, 1)

    def __init__(self, speed: float):
        pool = [self.SMALL, self.SMALL, self.LARGE, self.SMALL2]
        if speed > 7.5:
            pool += [self.SMALL3, self.LARGE2]
        if speed > 9.5:
            pool += [self.TALL]
        w, h, stems = random.choice(pool)
        self.w: int = w
        self.h: int = h
        self.stems: int = stems
        self.x: float = float(GAME_W + 10)
        self.y: float = float(GROUND_Y - h)

    def update(self, speed: float):
        self.x -= speed

    def off_screen(self) -> bool:
        return self.x + self.w < 0


class Bird:
    """
    Flying obstacle. LOW birds must be jumped over; HIGH birds pass over a
    standing player and only hit you if you jump into them.
    """
    W, H = 42, 24
    LOW  = GROUND_Y - 30
    HIGH = GROUND_Y - Player.H - 38

    def __init__(self, speed: float):
        self.w: int = self.W
        self.h: int = self.H
        self.x: float = float(GAME_W + 10)
        self.y: float = float(random.choice([self.LOW, self.LOW, self.HIGH]))
        self.high: bool = self.y == self.HIGH
        self.flap: float = random.random() * 10
        self.stems: int = 0

    def update(self, speed: float):
        self.x -= speed + 1.0
        self.flap += 0.18

    def off_screen(self) -> bool:
        return self.x + self.w < 0

    @property
    def wing_up(self) -> bool:
        return int(self.flap) % 2 == 0


class Scenery:
    """One background set piece (a campfire, a pyramid, a castle…)."""
    def __init__(self, x: float, era: int):
        self.x: float = x
        self.era: int = era
        self.phase: float = random.random() * 10
        self.variant: int = random.randint(0, 2)


class JumpGameLogic:
    SPEED_START = 6.0
    SPEED_MAX   = 13.0

    def __init__(self):
        self.high_score: int = 0
        self._reset()

    def _reset(self):
        self.state: str = 'ready'
        self.player = Player()
        self.obstacles: list = []          # Obstacle | Bird
        self.distance: float = 0.0
        self.score: int = 0
        self.speed: float = self.SPEED_START
        self.ground_offset: float = 0.0
        self._next_gap: float = 420.0
        self.clouds: list[list[float]] = [
            [random.uniform(0, GAME_W), random.uniform(60, 220), random.uniform(0.25, 0.5)]
            for _ in range(4)
        ]
        self.jumps: int = 0
        self.era: int = 0
        self.era_banner: int = 0
        self.scenery: list[Scenery] = [Scenery(380.0, 0)]
        self._scenery_gap: float = 620.0
        self._buffered_jump: int = 0        # frames left to honour a jump requested just before landing
        self._buffered_strength: float = 0.0

    # ── Input ──────────────────────────────────────────────────────────────────

    def trigger_jump(self, strength: float = 0.0):
        if self.state == 'ready':
            self.state = 'playing'
        elif self.state == 'playing':
            if self.player.on_ground:
                self.jumps += 1
                self.player.jump(strength)
            elif self.player.vy > 0 and self.player.height < 30:
                # About to land: remember the request and jump on touchdown
                self._buffered_jump = 8
                self._buffered_strength = strength
        elif self.state == 'dead':
            prev_hi = self.high_score
            self._reset()
            self.high_score = prev_hi
            self.state = 'playing'

    # ── Simulation ─────────────────────────────────────────────────────────────

    def update(self, held: bool = False):
        if self.state != 'playing':
            # idle animation only
            self.ground_offset = (self.ground_offset + 0.0) % 48
            return

        self.player.update(self.speed, held)
        if self._buffered_jump > 0:
            self._buffered_jump -= 1
            if self.player.on_ground:
                self.jumps += 1
                self.player.jump(self._buffered_strength)
                self._buffered_jump = 0

        self.distance      += self.speed
        self.score          = int(self.distance / 12)
        self.speed          = min(self.SPEED_MAX, self.SPEED_START + self.distance / 2600.0)
        self.ground_offset  = (self.ground_offset + self.speed) % 48

        # Eras + background scenery
        era = min(len(ERAS) - 1, self.score // ERA_SCORE)
        if era != self.era:
            self.era = era
            self.era_banner = ERA_BANNER_FR
        if self.era_banner > 0:
            self.era_banner -= 1
        for sc in self.scenery:
            sc.x -= self.speed * SCENERY_PARALLAX
            sc.phase += 0.12
        self.scenery = [sc for sc in self.scenery if sc.x > -320]
        last_sx = max((sc.x for sc in self.scenery), default=-1e9)
        if GAME_W - last_sx >= self._scenery_gap:
            self.scenery.append(Scenery(GAME_W + 60.0, self.era))
            self._scenery_gap = random.uniform(520, 800)

        for c in self.clouds:
            c[0] -= self.speed * c[2]
            if c[0] < -80:
                c[0] = GAME_W + random.uniform(20, 120)
                c[1] = random.uniform(60, 220)

        # Distance-based spawning (gap grows with speed so jumps stay possible)
        last_x = max((o.x + o.w for o in self.obstacles), default=-1e9)
        if GAME_W - last_x >= self._next_gap:
            if self.speed > 8.0 and random.random() < 0.3:
                self.obstacles.append(Bird(self.speed))
            else:
                self.obstacles.append(Obstacle(self.speed))
            gap_min = 210 + self.speed * 24
            self._next_gap = random.uniform(gap_min, gap_min + 320)

        for obs in self.obstacles:
            obs.update(self.speed)
        self.obstacles = [o for o in self.obstacles if not o.off_screen()]

        # Collision (inset hitboxes for forgiveness)
        px = self.player.x + 6;  pw = self.player.W - 12
        py = self.player.y + 5;  ph = self.player.H - 8
        for obs in self.obstacles:
            ox = obs.x + 3;  ow = obs.w - 6
            oy = obs.y + 4;  oh = obs.h - 6
            if px < ox + ow and px + pw > ox and py < oy + oh and py + ph > oy:
                self.state = 'dead'
                if self.score > self.high_score:
                    self.high_score = self.score
                break
