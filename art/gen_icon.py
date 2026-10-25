"""HOSHIYOMI LOG のアイコン(32x32 ドット → 整数倍 NEAREST で 128px)。三日月+星+ノート"""
from pathlib import Path
from PIL import Image

BG, RIM = (7, 7, 15, 255), (58, 74, 102, 255)
CYAN, MAG, YEL, PAPER, INK = (0, 240, 255, 255), (255, 43, 214, 255), (248, 255, 74, 255), (216, 247, 255, 255), (40, 52, 80, 255)
S = 32
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
px = im.load()
# 角丸の地
for y in range(S):
    for x in range(S):
        cx, cy = min(x, S - 1 - x), min(y, S - 1 - y)
        if cx + cy < 3:
            continue
        px[x, y] = RIM if (x in (0, S - 1) or y in (0, S - 1) or cx + cy == 3) else BG
# 三日月(大円 - ずらした円)
for y in range(S):
    for x in range(S):
        if (x - 11) ** 2 + (y - 11) ** 2 <= 49 and (x - 14) ** 2 + (y - 9) ** 2 > 36:
            px[x, y] = CYAN
# 星(十字のきらめき)
for (sx, sy, c) in ((23, 7, YEL), (26, 14, MAG)):
    px[sx, sy] = c
    for d in (1, 2) if c == YEL else (1,):
        for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d)):
            px[sx + dx, sy + dy] = c
# 日記ノート(右下)
for y in range(19, 28):
    for x in range(14, 27):
        px[x, y] = MAG if x == 14 else PAPER
for y in (21, 23, 25):
    for x in range(17, 25):
        px[x, y] = INK
out = Path(__file__).with_name("icon_hoshiyomi.png")
im.resize((128, 128), Image.NEAREST).save(out)
print(out)
