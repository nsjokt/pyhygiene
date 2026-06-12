"""Generate a clean 1280x800 marketplace/README thumbnail for pyhygiene."""
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 800
BG = (15, 23, 42)        # slate-900
PANEL = (30, 41, 59)     # slate-800
ACCENT = (56, 189, 248)  # sky-400
GREEN = (34, 197, 94)    # green-500
WHITE = (241, 245, 249)
MUTED = (148, 163, 184)  # slate-400

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def font(paths, size):
    for p, idx in paths:
        try:
            return ImageFont.truetype(p, size, index=idx)
        except Exception:
            continue
    return ImageFont.load_default()


LAT = [("/System/Library/Fonts/HelveticaNeue.ttc", 0),
       ("/System/Library/Fonts/Helvetica.ttc", 0),
       ("/Library/Fonts/Arial.ttf", 0)]
LATB = [("/System/Library/Fonts/HelveticaNeue.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("/System/Library/Fonts/Helvetica.ttc", 1)]
KR = [("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
      ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", 0)]
MONO = [("/System/Library/Fonts/Menlo.ttc", 0),
        ("/System/Library/Fonts/SFNSMono.ttf", 0)]

f_tag = font(LATB, 28)
f_word = font(LATB, 132)
f_kr = font(KR, 50)
f_sub = font(LAT, 30)
f_chip = font(MONO, 30)


def center(draw, text, f, y, fill, cx=W // 2):
    b = draw.textbbox((0, 0), text, font=f)
    draw.text((cx - (b[2] - b[0]) // 2, y), text, font=f, fill=fill)


# top tag
center(d, "CLAUDE  SKILL  ·  CODING / AGENT", f_tag, 96, ACCENT)
# wordmark
center(d, "pyhygiene", f_word, 200, WHITE)
# KR tagline
center(d, "파이썬 환경을 안전하게 정리", f_kr, 388, WHITE)
# EN subtitle
center(d, "audit · plan · clean · guard", f_sub, 470, ACCENT)
center(d, "automation-aware  ·  backup-first  ·  never auto-sudo", f_sub, 514, MUTED)

# bottom chips
chips = ["interpreters", "venvs", "caches"]
cw, ch, gap = 280, 78, 28
total = len(chips) * cw + (len(chips) - 1) * gap
x = (W - total) // 2
y = 636
for c in chips:
    d.rounded_rectangle([x, y, x + cw, y + ch], radius=16, fill=PANEL)
    b = d.textbbox((0, 0), c, font=f_chip)
    d.text((x + (cw - (b[2] - b[0])) // 2, y + (ch - (b[3] - b[1])) // 2 - b[1]),
           c, font=f_chip, fill=WHITE)
    x += cw + gap

# accent underline bar under wordmark
d.rounded_rectangle([W // 2 - 90, 360, W // 2 + 90, 366], radius=3, fill=GREEN)

img.save("/Users/ogyutae/Desktop/claude_project/pyhygiene/assets/thumbnail.png", "PNG")
print("saved assets/thumbnail.png", img.size)
