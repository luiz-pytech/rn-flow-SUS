"""Miniatura do YouTube (1280x720): título, mapa Natal x Mossoró e a webcam."""
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

AQUI = Path(__file__).resolve().parent
VID = AQUI.parent
FUNDO, TINTA, TINTA_2 = "#fcfcfb", "#0b0b0b", "#52514e"
AZUL, LARANJA = "#2a78d6", "#eb6834"
NEGRITO = "C:/Windows/Fonts/segoeuib.ttf"
NORMAL = "C:/Windows/Fonts/segoeui.ttf"

W, H = 1280, 720
img = Image.new("RGB", (W, H), FUNDO)

# mapa: Natal e Mossoró com os fluxos (sem o título da figura original)
mapa = Image.open(VID / "01_intro/07_enfase_natal_e_mossoro.png").convert("RGB").crop((0, 140, 1530, 1000))
mapa = mapa.resize((int(mapa.width * 0.5), int(mapa.height * 0.5)), Image.LANCZOS)
img.paste(mapa, (20, H - mapa.height - 15))

# rosto: quadro da câmera em que ele olha para a lente
rosto_png = AQUI / "out" / "rosto_miniatura.png"
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-ss", "2",
                "-i", str(AQUI / "public/cam/021b_natal.mp4"), "-frames:v", "1", str(rosto_png)], check=True)
lado = 330
rosto = Image.open(rosto_png).convert("RGB").resize((lado, lado), Image.LANCZOS)
mascara = Image.new("L", (lado * 4, lado * 4), 0)
ImageDraw.Draw(mascara).ellipse((0, 0, lado * 4 - 1, lado * 4 - 1), fill=255)
mascara = mascara.resize((lado, lado), Image.LANCZOS)
cx, cy = 1040, 230
anel = 9
d = ImageDraw.Draw(img)
d.ellipse((cx - lado // 2 - anel, cy - lado // 2 - anel, cx + lado // 2 + anel, cy + lado // 2 + anel), fill=AZUL)
img.paste(rosto, (cx - lado // 2, cy - lado // 2), mascara)

# título
f_tit = ImageFont.truetype(NEGRITO, 76)
d.text((40, 24), "QUEM SUSTENTA", font=f_tit, fill=TINTA)
d.text((40, 112), "A URGÊNCIA NO RN?", font=f_tit, fill=TINTA)

# Natal ou Mossoró?
f_q = ImageFont.truetype(NEGRITO, 50)
partes = [("Natal ", AZUL), ("ou ", TINTA), ("Mossoró?", LARANJA)]
largura = sum(d.textlength(t, font=f_q) for t, _ in partes)
x = cx - largura / 2
for t, cor in partes:
    d.text((x, 445), t, font=f_q, fill=cor)
    x += d.textlength(t, font=f_q)
f_sub = ImageFont.truetype(NORMAL, 27)
sub = "grafos com dados do SUS · 2024"
d.text((cx - d.textlength(sub, font=f_sub) / 2, 518), sub, font=f_sub, fill=TINTA_2)

img.save(AQUI / "out" / "miniatura.png", optimize=True)
print("ok", (AQUI / "out" / "miniatura.png").stat().st_size // 1024, "KB")
