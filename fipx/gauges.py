"""The six instruments, drawn for a 320x240 screen.

Pillow has no antialiasing, so everything is drawn at SS times final size and
reduced at the end; on a dial full of thin needles and tick marks that is the
difference between "looks like an instrument" and "looks like a screenshot of
a spreadsheet". The static parts of each dial -- bezel, ticks, numerals, arcs
-- are rendered once and cached, because the only thing that changes at 25 Hz
is a needle.

Angles here are degrees clockwise from 12 o'clock, which is how instrument
markings are actually laid out. Pillow measures from 3 o'clock, hence the -90
in pol() and in every arc call.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

W, H = 320, 240
SS = 3                                   # supersample factor
CW, CH = W * SS, H * SS
CX, CY = CW // 2, CH // 2
R = int(115 * SS)                        # dial radius

BLACK = (8, 8, 10)
FACE = (18, 18, 20)
BEZEL = (60, 62, 68)
WHITE = (235, 235, 235)
DIM = (150, 150, 155)
AMBER = (255, 176, 0)
RED = (220, 45, 40)
GREEN = (40, 170, 70)
YELLOW = (225, 200, 40)
SKY = (58, 130, 205)
GROUND = (128, 78, 40)
CYAN = (60, 200, 235)

FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]
_fonts: dict = {}


def font(size: int):
    size = max(6, int(size))
    if size not in _fonts:
        for path in FONT_PATHS:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def pol(cx, cy, r, deg):
    """Point at radius r and deg clockwise from 12 o'clock."""
    a = math.radians(deg - 90.0)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def rot(px, py, cx, cy, deg):
    """Rotate a point clockwise about a centre."""
    a = math.radians(deg)
    dx, dy = px - cx, py - cy
    return cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)


def arc_band(d, cx, cy, radius, width, a0, a1, colour):
    box = [cx - radius, cy - radius, cx + radius, cy + radius]
    d.arc(box, a0 - 90, a1 - 90, fill=colour, width=int(width))


def needle(d, cx, cy, deg, length, base_w, colour, tail=0.0, tip_w=None):
    """A tapered needle: wide at the hub, narrow at the tip."""
    tip_w = base_w * 0.25 if tip_w is None else tip_w
    pts = [(-base_w / 2, tail), (-tip_w / 2, -length), (tip_w / 2, -length), (base_w / 2, tail)]
    out = []
    a = math.radians(deg)
    for x, y in pts:
        out.append((cx + x * math.cos(a) - y * math.sin(a),
                    cy + x * math.sin(a) + y * math.cos(a)))
    d.polygon(out, fill=colour)


def nice_step(span, target=14):
    """A tick interval that gives roughly `target` major ticks."""
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000, 2000, 5000):
        if span / step <= target:
            return step
    return 10000


def blank_face():
    img = Image.new("RGB", (CW, CH), BLACK)
    d = ImageDraw.Draw(img)
    d.ellipse([CX - R, CY - R, CX + R, CY + R], fill=FACE, outline=BEZEL, width=int(2.5 * SS))
    return img, d


def corner_text(d, name, right=None):
    d.text((6 * SS, 5 * SS), name, fill=DIM, font=font(13 * SS))
    if right:
        d.text((CW - 6 * SS, 5 * SS), right, fill=DIM, font=font(13 * SS), anchor="ra")


def readout(d, text, colour=WHITE, size=17, y=None):
    """The digital value in the bottom margin."""
    y = CH - 6 * SS if y is None else y
    d.text((CX, y), text, fill=colour, font=font(size * SS), anchor="ms")


class Gauge:
    """One instrument. Subclasses build a cached face and draw the moving bits."""

    name = "GAUGE"
    key = "gauge"

    def __init__(self):
        self._face = None
        self._sig = None

    def face_sig(self, data) -> str:
        return ""                      # static dial; rebuild never needed

    def build_face(self, data) -> Image.Image:
        img, d = blank_face()
        corner_text(d, self.name)
        return img

    def draw_dynamic(self, d, img, data):
        raise NotImplementedError

    def render(self, data) -> Image.Image:
        sig = self.face_sig(data)
        if self._face is None or sig != self._sig:
            self._face, self._sig = self.build_face(data), sig
        img = self._face.copy()
        d = ImageDraw.Draw(img)
        self.draw_dynamic(d, img, data)
        return img.resize((W, H), Image.LANCZOS)


# --------------------------------------------------------------------------
class AirspeedGauge(Gauge):
    """Airspeed indicator, with the arcs laid out from the aircraft's V-speeds."""

    name = "AIRSPEED"
    key = "asi"
    A0, A1 = 30.0, 330.0

    def limits(self, data):
        acf = data.get("acf", {})
        vne = acf.get("vne") or 0.0
        vno = acf.get("vno") or 0.0
        vso = acf.get("vso") or 0.0
        top = vne or vno or 200.0
        vmax = math.ceil(top * 1.08 / 10.0) * 10.0
        vmin = max(0.0, math.floor((vso * 0.55 if vso else 20.0) / 10.0) * 10.0)
        if vmax - vmin < 60:
            vmax = vmin + 60
        return vmin, vmax

    def face_sig(self, data):
        return data.get("acf", {}).get("sig", "")

    def angle(self, v, vmin, vmax):
        v = min(max(v, vmin), vmax)
        return self.A0 + (v - vmin) / (vmax - vmin) * (self.A1 - self.A0)

    def build_face(self, data):
        img, d = blank_face()
        acf = data.get("acf", {})
        vmin, vmax = self.limits(data)
        ang = lambda v: self.angle(v, vmin, vmax)

        # V-speed arcs: white flap range inside, green/yellow/red outside.
        r_out, w_out = R * 0.90, R * 0.075
        r_in, w_in = R * 0.76, R * 0.065
        vso, vs1, vfe = acf.get("vso") or 0, acf.get("vs1") or 0, acf.get("vfe") or 0
        vno, vne = acf.get("vno") or 0, acf.get("vne") or 0
        if vso and vfe:
            arc_band(d, CX, CY, r_in, w_in, ang(vso), ang(vfe), WHITE)
        if vs1 and (vno or vne):
            arc_band(d, CX, CY, r_out, w_out, ang(vs1), ang(vno or vne), GREEN)
        if vno and vne:
            arc_band(d, CX, CY, r_out, w_out, ang(vno), ang(vne), YELLOW)
        if vne:
            arc_band(d, CX, CY, r_out, w_out, ang(vne), ang(vmax), RED)

        step = nice_step(vmax - vmin, 14)
        minor = step / 2.0
        v = math.ceil(vmin / minor) * minor
        while v <= vmax + 0.01:
            a = ang(v)
            major = abs(v / step - round(v / step)) < 1e-6
            r1 = R * (0.68 if major else 0.72)
            p1, p2 = pol(CX, CY, r1, a), pol(CX, CY, R * 0.80, a)
            d.line([p1, p2], fill=WHITE, width=int((3.0 if major else 1.8) * SS))
            if major:
                tx, ty = pol(CX, CY, R * 0.55, a)
                d.text((tx, ty), f"{int(v)}", fill=WHITE, font=font(15 * SS), anchor="mm")
            v += minor

        corner_text(d, self.name, "KIAS")
        return img

    def draw_dynamic(self, d, img, data):
        vmin, vmax = self.limits(data)
        ias = data["ias"]
        needle(d, CX, CY, self.angle(ias, vmin, vmax), R * 0.80, 9 * SS, WHITE, tail=14 * SS)
        d.ellipse([CX - 9 * SS, CY - 9 * SS, CX + 9 * SS, CY + 9 * SS], fill=(210, 210, 210))
        readout(d, f"{ias:5.0f} kt")


# --------------------------------------------------------------------------
class AttitudeGauge(Gauge):
    """Artificial horizon.

    The horizon and pitch ladder live on one tall pre-rendered card. Each frame
    crops the slice of it the current pitch puts in view, rotates that by the
    bank angle, and drops the fixed marks on top -- which is exactly what the
    real instrument does mechanically.
    """

    name = "ATTITUDE"
    key = "ai"
    PPD = R / 38.0                      # pixels per degree of pitch

    def __init__(self):
        super().__init__()
        self._card = None
        self._overlay = None

    def build_card(self):
        cw = int(3.0 * R)
        pitch_px = int(95 * self.PPD)
        ch = int(2 * pitch_px + 3.0 * R)
        card = Image.new("RGB", (cw, ch), SKY)
        d = ImageDraw.Draw(card)
        y0 = ch // 2
        d.rectangle([0, y0, cw, ch], fill=GROUND)
        d.line([0, y0, cw, y0], fill=WHITE, width=int(3 * SS))
        cx = cw // 2
        for deg in range(-90, 95, 5):
            if deg == 0:
                continue
            y = y0 - deg * self.PPD
            if not (0 < y < ch):
                continue
            if deg % 10 == 0:
                half = R * 0.34
                d.line([cx - half, y, cx + half, y], fill=WHITE, width=int(2.2 * SS))
                for side in (-1, 1):
                    d.text((cx + side * (half + 9 * SS), y), f"{abs(deg)}",
                           fill=WHITE, font=font(12 * SS), anchor="mm")
            else:
                half = R * 0.17
                d.line([cx - half, y, cx + half, y], fill=WHITE, width=int(1.6 * SS))
        return card

    def build_overlay(self):
        """Everything fixed to the case: bezel mask, bank scale, aircraft symbol."""
        ov = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        # Mask everything outside the dial, then redraw the bezel ring.
        mask = Image.new("L", (CW, CH), 255)
        ImageDraw.Draw(mask).ellipse([CX - R, CY - R, CX + R, CY + R], fill=0)
        ov.paste(Image.new("RGB", (CW, CH), BLACK), (0, 0), mask)
        d.ellipse([CX - R, CY - R, CX + R, CY + R], outline=BEZEL, width=int(2.5 * SS))

        for deg in (-60, -30, -20, -10, 10, 20, 30, 60):
            long_mark = abs(deg) in (30, 60)
            r1 = R * (0.86 if long_mark else 0.90)
            d.line([pol(CX, CY, r1, deg), pol(CX, CY, R * 0.97, deg)],
                   fill=WHITE, width=int((2.6 if long_mark else 1.8) * SS))
        # sky pointer at the top
        tip = pol(CX, CY, R * 0.84, 0)
        left = pol(CX, CY, R * 0.72, -5)
        right = pol(CX, CY, R * 0.72, 5)
        d.polygon([tip, left, right], fill=AMBER)

        # fixed miniature aircraft
        wing, gap, thick = R * 0.62, R * 0.10, 4.0 * SS
        for sign in (-1, 1):
            d.line([CX + sign * gap, CY, CX + sign * wing, CY], fill=AMBER, width=int(thick))
            d.line([CX + sign * wing, CY, CX + sign * wing, CY + 9 * SS],
                   fill=AMBER, width=int(thick))
        d.rectangle([CX - 3 * SS, CY - 3 * SS, CX + 3 * SS, CY + 3 * SS], fill=AMBER)
        d.text((6 * SS, 5 * SS), self.name, fill=DIM, font=font(13 * SS))
        return ov

    def render(self, data):
        if self._card is None:
            self._card = self.build_card()
            self._overlay = self.build_overlay()
        pitch = max(-85.0, min(85.0, data["pitch_deg"]))
        roll = data["roll_deg"]

        cw, ch = self._card.size
        half = int(1.5 * R)
        centre = ch // 2 - pitch * self.PPD
        box = (cw // 2 - half, int(centre) - half, cw // 2 + half, int(centre) + half)
        # Bank right and the world tilts left, so the card turns with +roll.
        view = self._card.crop(box).rotate(roll, resample=Image.BILINEAR)

        img = Image.new("RGB", (CW, CH), BLACK)
        img.paste(view, (CX - view.width // 2, CY - view.height // 2))
        img.paste(self._overlay, (0, 0), self._overlay)
        d = ImageDraw.Draw(img)
        bank = "" if abs(roll) < 1.0 else f"  {abs(roll):.0f}°{'R' if roll > 0 else 'L'}"
        readout(d, f"{pitch:+.0f}°{bank}")
        return img.resize((W, H), Image.LANCZOS)


# --------------------------------------------------------------------------
class AltimeterGauge(Gauge):
    """Three-pointer sensitive altimeter with a Kollsman window."""

    name = "ALTITUDE"
    key = "alt"

    def build_face(self, data):
        img, d = blank_face()
        for i in range(50):
            v = i / 5.0
            a = v * 36.0
            major = i % 5 == 0
            r1 = R * (0.70 if major else 0.80)
            d.line([pol(CX, CY, r1, a), pol(CX, CY, R * 0.90, a)],
                   fill=WHITE, width=int((3.2 if major else 1.6) * SS))
            if major:
                tx, ty = pol(CX, CY, R * 0.57, a)
                d.text((tx, ty), f"{i // 5}", fill=WHITE, font=font(20 * SS), anchor="mm")
        corner_text(d, self.name, "FT")
        d.text((CX, CY + R * 0.30), "100 FT", fill=DIM, font=font(11 * SS), anchor="mm")
        return img

    def draw_dynamic(self, d, img, data):
        alt = data["alt_ft"]
        # Kollsman window, in the 3 o'clock position like the real instrument.
        bw, bh = 44 * SS, 17 * SS
        bx, by = CX + R * 0.46, CY - bh / 2
        d.rectangle([bx, by, bx + bw, by + bh], fill=(0, 0, 0), outline=DIM, width=int(1.5 * SS))
        d.text((bx + bw / 2, by + bh / 2), f"{data['baro_inhg']:.2f}",
               fill=WHITE, font=font(12 * SS), anchor="mm")

        needle(d, CX, CY, (alt % 100000) / 100000.0 * 360.0, R * 0.42, 7 * SS, WHITE, tail=10 * SS)
        needle(d, CX, CY, (alt % 10000) / 10000.0 * 360.0, R * 0.60, 11 * SS, WHITE, tail=12 * SS)
        needle(d, CX, CY, (alt % 1000) / 1000.0 * 360.0, R * 0.88, 7 * SS, WHITE, tail=14 * SS)
        d.ellipse([CX - 8 * SS, CY - 8 * SS, CX + 8 * SS, CY + 8 * SS], fill=(210, 210, 210))
        readout(d, f"{alt:6.0f} ft")


# --------------------------------------------------------------------------
class TurnGauge(Gauge):
    """Turn coordinator: rate of turn from the gyro, slip from the ball."""

    name = "TURN COORD"
    key = "tc"
    STD_RATE_BANK = 20.0                # symbol bank shown at 3 deg/sec
    TUBE_R = R * 1.35                   # inclinometer tube curvature
    TUBE_HALF = 14.0                    # half its angular length, degrees

    def build_face(self, data):
        img, d = blank_face()
        # A standard-rate turn banks the symbol 20 degrees, and a right bank puts
        # the RIGHT wingtip down -- so the R index sits below the horizontal, not
        # above it. Marked the other way round, a right turn would rest the
        # raised left wingtip on the mark labelled L.
        for deg, label in ((90.0 + self.STD_RATE_BANK, "R"), (270.0 - self.STD_RATE_BANK, "L")):
            d.line([pol(CX, CY, R * 0.78, deg), pol(CX, CY, R * 0.96, deg)],
                   fill=WHITE, width=int(3.5 * SS))
            tx, ty = pol(CX, CY, R * 0.74, 90.0 if label == "R" else 270.0)
            d.text((tx, ty), label, fill=WHITE, font=font(15 * SS), anchor="mm")
        for deg in (90.0, 270.0):                      # wings-level marks
            d.line([pol(CX, CY, R * 0.86, deg), pol(CX, CY, R * 0.96, deg)],
                   fill=DIM, width=int(2.2 * SS))
        d.text((CX, CY + R * 0.26), "2 MIN TURN", fill=DIM, font=font(10 * SS), anchor="mm")

        # inclinometer: a shallow curved tube along the bottom of the dial
        cyt = self.tube_centre()
        d.arc([CX - self.TUBE_R, cyt - self.TUBE_R, CX + self.TUBE_R, cyt + self.TUBE_R],
              90 - self.TUBE_HALF, 90 + self.TUBE_HALF, fill=(46, 46, 52), width=int(22 * SS))
        for side in (-1, 1):
            a = 180.0 - side * 3.4
            d.line([pol(CX, cyt, self.TUBE_R - 12 * SS, a), pol(CX, cyt, self.TUBE_R + 12 * SS, a)],
                   fill=WHITE, width=int(2.2 * SS))
        corner_text(d, self.name)
        return img

    def tube_centre(self):
        return (CY + R * 0.62) - self.TUBE_R

    def draw_dynamic(self, d, img, data):
        bank = max(-35.0, min(35.0, data["turn_rate_dps"] / 3.0 * self.STD_RATE_BANK))
        wing, gap = R * 0.66, R * 0.11
        thick = int(4.5 * SS)
        pts = lambda x, y: rot(CX + x, CY + y, CX, CY, bank)
        d.line([pts(-wing, 0), pts(-gap, 0)], fill=WHITE, width=thick)
        d.line([pts(gap, 0), pts(wing, 0)], fill=WHITE, width=thick)
        d.line([pts(0, -R * 0.28), pts(0, 0)], fill=WHITE, width=thick)
        d.line([pts(-R * 0.12, -R * 0.28), pts(R * 0.12, -R * 0.28)], fill=WHITE, width=thick)
        d.ellipse([CX - 7 * SS, CY - 7 * SS, CX + 7 * SS, CY + 7 * SS], fill=WHITE)

        cyt = self.tube_centre()
        slip = max(-5.0, min(5.0, data["slip_deg"]))
        bx, by = pol(CX, cyt, self.TUBE_R, 180.0 - slip * 2.4)
        r = 8.5 * SS
        d.ellipse([bx - r, by - r, bx + r, by + r], fill=(232, 232, 236),
                  outline=(70, 70, 76), width=int(1.4 * SS))
        readout(d, f"{data['turn_rate_dps']:+.1f}°/s")


# --------------------------------------------------------------------------
class HeadingGauge(Gauge):
    """Directional gyro with a heading bug."""

    name = "HEADING"
    key = "hi"
    CARDINALS = {0: "N", 90: "E", 180: "S", 270: "W"}

    def __init__(self):
        super().__init__()
        self._card = None

    def build_card(self):
        size = int(2 * R + 4)
        card = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        cx = cy = size // 2
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=FACE)
        for deg in range(0, 360, 5):
            major = deg % 10 == 0
            r1 = R * (0.80 if major else 0.85)
            d.line([pol(cx, cy, r1, deg), pol(cx, cy, R * 0.94, deg)],
                   fill=WHITE, width=int((3.0 if major else 1.8) * SS))
        for deg in range(0, 360, 30):
            label = self.CARDINALS.get(deg) or f"{deg // 10}"
            size_pt = 22 if deg in self.CARDINALS else 17
            tx, ty = pol(cx, cy, R * 0.66, deg)
            txt = Image.new("RGBA", (60 * SS, 40 * SS), (0, 0, 0, 0))
            ImageDraw.Draw(txt).text((30 * SS, 20 * SS), label, fill=WHITE,
                                     font=font(size_pt * SS), anchor="mm")
            txt = txt.rotate(-deg, resample=Image.BICUBIC)   # numerals face outward
            card.paste(txt, (int(tx) - 30 * SS, int(ty) - 20 * SS), txt)
        return card

    def build_face(self, data):
        img = Image.new("RGB", (CW, CH), BLACK)
        d = ImageDraw.Draw(img)
        corner_text(d, self.name, "MAG")
        return img

    def draw_dynamic(self, d, img, data):
        if self._card is None:
            self._card = self.build_card()
        hdg = data["hdg_deg"] % 360.0
        bug = data["hdg_bug_deg"] % 360.0
        card = self._card.rotate(hdg, resample=Image.BILINEAR)
        img.paste(card, (CX - card.width // 2, CY - card.height // 2), card)
        d.ellipse([CX - R, CY - R, CX + R, CY + R], outline=BEZEL, width=int(2.5 * SS))

        rel = (bug - hdg) % 360.0
        b1, b2 = pol(CX, CY, R * 0.94, rel - 4.5), pol(CX, CY, R * 0.94, rel + 4.5)
        b3, b4 = pol(CX, CY, R * 0.78, rel + 4.5), pol(CX, CY, R * 0.78, rel - 4.5)
        d.polygon([b1, b2, b3, b4], fill=CYAN)

        # lubber line and fixed aircraft symbol
        d.polygon([pol(CX, CY, R * 0.99, 0), pol(CX, CY, R * 0.86, -5),
                   pol(CX, CY, R * 0.86, 5)], fill=AMBER)
        wing, thick = R * 0.32, int(4.0 * SS)
        d.line([CX - wing, CY, CX + wing, CY], fill=AMBER, width=thick)
        d.line([CX, CY - R * 0.24, CX, CY + R * 0.20], fill=AMBER, width=thick)
        d.line([CX - R * 0.11, CY + R * 0.20, CX + R * 0.11, CY + R * 0.20],
               fill=AMBER, width=thick)
        readout(d, f"{hdg:03.0f}°   bug {bug:03.0f}°")


# --------------------------------------------------------------------------
class VerticalSpeedGauge(Gauge):
    """Vertical speed, zero at 9 o'clock with the scale open on the right."""

    name = "VERT SPEED"
    key = "vsi"
    SWEEP = 160.0

    def scale_max(self, data):
        vne = data.get("acf", {}).get("vne") or 0.0
        return 6000.0 if vne > 250 else 2000.0

    def face_sig(self, data):
        return f"{self.scale_max(data):.0f}"

    def angle(self, fpm, vmax):
        fpm = max(-vmax, min(vmax, fpm))
        return (270.0 + fpm / vmax * self.SWEEP) % 360.0

    def build_face(self, data):
        img, d = blank_face()
        vmax = self.scale_max(data)
        major_step = 500.0 if vmax <= 2000 else 2000.0
        minor = major_step / 5.0
        n = int(round(vmax / minor))
        for i in range(-n, n + 1):
            v = i * minor
            a = self.angle(v, vmax)
            major = abs(v / major_step - round(v / major_step)) < 1e-6
            r1 = R * (0.70 if major else 0.80)
            d.line([pol(CX, CY, r1, a), pol(CX, CY, R * 0.90, a)],
                   fill=WHITE, width=int((3.0 if major else 1.6) * SS))
            if major:
                tx, ty = pol(CX, CY, R * 0.56, a)
                d.text((tx, ty), f"{abs(int(v / 100))}", fill=WHITE,
                       font=font(17 * SS), anchor="mm")
        d.text((CX, CY - R * 0.36), "UP", fill=DIM, font=font(12 * SS), anchor="mm")
        d.text((CX, CY + R * 0.32), "DOWN", fill=DIM, font=font(12 * SS), anchor="mm")
        corner_text(d, self.name, "FPM x100")
        return img

    def draw_dynamic(self, d, img, data):
        vmax = self.scale_max(data)
        fpm = data["vsi_fpm"]
        needle(d, CX, CY, self.angle(fpm, vmax), R * 0.86, 9 * SS, WHITE, tail=12 * SS)
        d.ellipse([CX - 8 * SS, CY - 8 * SS, CX + 8 * SS, CY + 8 * SS], fill=(210, 210, 210))
        readout(d, f"{fpm:+5.0f} fpm")


ALL_GAUGES = [AirspeedGauge, AttitudeGauge, AltimeterGauge,
              TurnGauge, HeadingGauge, VerticalSpeedGauge]


def overlay_message(img, title, detail=""):
    """A banner across a live gauge -- used when the sim stops talking."""
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([0, H // 2 - 26, W, H // 2 + 26], fill=(0, 0, 0, 205))
    d.text((W // 2, H // 2 - 8), title, fill=(235, 70, 60), font=font(20), anchor="mm")
    if detail:
        d.text((W // 2, H // 2 + 13), detail, fill=(200, 200, 200), font=font(12), anchor="mm")
    return img


def toast(img, text):
    """Brief label shown when the displayed instrument changes."""
    d = ImageDraw.Draw(img, "RGBA")
    w = d.textlength(text, font=font(15)) + 18
    d.rounded_rectangle([(W - w) / 2, 8, (W + w) / 2, 32], radius=6, fill=(0, 0, 0, 200),
                        outline=(120, 120, 125))
    d.text((W / 2, 20), text, fill=(240, 240, 240), font=font(15), anchor="mm")
    return img
