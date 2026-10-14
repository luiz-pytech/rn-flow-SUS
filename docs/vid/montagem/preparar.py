"""Monta a linha do tempo do vídeo final a partir das gravações em docs/vid/me.

1. Corta o silêncio do começo e do fim de cada gravação, acelera (RITMO), normaliza o volume e recorta só o
   círculo da webcam -> public/cam/*.mp4
2. Liga (hard link) as figuras usadas em public/fig e extrai o último quadro de cada animação -> public/hold
3. Converte as deixas (tempo na gravação original ou palavra da fala) em quadros e escreve src/VideoFinal.tsx

Uso (na pasta docs/vid/montagem):  python preparar.py   [--sem-cortes  pula o ffmpeg se public/cam já existe]
"""
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image

AQUI = Path(__file__).resolve().parent
VID = AQUI.parent
ME = VID / "me"
PUB = AQUI / "public"
FF = imageio_ffmpeg.get_ffmpeg_exe()
FPS = 30
RITMO = 1.12          # aceleração da fala (tom preservado pelo atempo)
FOLGA_INI, FOLGA_FIM = 0.15, 0.3   # segundos de respiro mantidos antes da 1ª e depois da última palavra
FADE = 8              # quadros de dissolve entre figuras
CAM = (1524, 30, 312)  # recorte do círculo da câmera nas gravações (x, y, lado)

# animações: arquivo -> duração em segundos (o último quadro vira imagem parada se a deixa for mais longa)
MOVIMENTOS = {
    "01_intro/03_contador_83.mp4": 3.5, "01_intro/04_fluxos_crescendo.mp4": 4.0,
    "03_grafo/14_multicamada_3d.mp4": 8.0, "04_polos/06_skyline_3d.mp4": 8.0,
    "06_remocao/05_remocao_mossoro.mp4": 5.0, "08_kcore/04_kcore_3d.mp4": 8.0,
}
RODAPE = {
    "004": "rodape_semana2_grafos", "005": "rodape_semana2_grafos", "006": "rodape_semana2_grafos",
    "007": "rodape_semanas2e3_grau_bfs", "008": "rodape_semanas2e3_grau_bfs", "009": "rodape_semanas2e3_grau_bfs",
    "012": "rodape_semana4_componentes", "013": "rodape_semana4_componentes",
    "014": "rodape_semana4_centralidades", "015": "rodape_semana5_kcore",
    "016": "rodape_semana6_bipartida", "017": "rodape_semana6_bipartida", "018": "rodape_semana6_bipartida",
    "019": "rodape_semana6_bipartida", "020": "rodape_semana6_bipartida", "023": "rodape_semana6_limiar",
}

FOLGA_FIM_CLIP = {"000_intro": 1.0}

# Trechos que não são uma gravação inteira (regravações e cortes). O resto usa a gravação de mesmo nome.
TRECHOS = {
    "008_polos": ("008_polos", None, 32.35),                       # sai "…Mossoró, capital do Seridó"
    "008b_oeste": ("regravacao_oestepotiguar", None, None),
    "014_centralidade": ("014_centralidade", None, 36.95),         # sai o relato da estrada (aresta ≠ rota)
    "014b_ponte": ("ponte_ceara_mirim", None, None),
    "021b_natal": ("regravação_horas", None, 46.6),                 # sai "causaria com certeza superlotação"
    "021c_interior": ("regravação_horas", 50.8, None),
    "022_respostas": ("022_respostas", 19.45, None),               # o interior já foi dito com os números
    "024_limitacoes": ("024_limitacoes", None, 52.35),             # sai a repetição sobre o Jaccard
    "026a_final": ("026_final", None, 12.0),                        # sai "Mossoró… no Seridó"
    "026b_oeste": ("regravao_mossoro_sustenta_conec", None, None),
    "026c_final": ("026_final", 15.9, None),
}

# Deixas: (quando, figura[, opções]). Opções: fade (quadros do dissolve), vel (velocidade da animação),
# foco (zoom na camada de urgência). "quando" = segundos na gravação original, ou "palavra" / ("palavra", depois_de_s).
CENAS = {
    "000_intro": [(0, "01_intro/03_contador_83.mp4"), ("significa", "01_intro/04_fluxos_crescendo.mp4"),
                  ("Dessa", "01_intro/05_fluxos_peso.png"), ("Hoje", "01_intro/06_enfase_natal.png"),
                  (("Mas", 30), "01_intro/07_enfase_natal_e_mossoro.png", {"fade": 24})],
    "002_pergunta": [(0, "01_intro/08_titulo.png"), (("Quais", 8), "02_pergunta/01_pergunta_1.png"),
                     (("Quais", 14), "02_pergunta/02_pergunta_2.png"), (("E", 18.5), "02_pergunta/03_pergunta_3.png"),
                     (("Essas", 29), "02_pergunta/06_para_quem.png")],
    "003_pergunta": [(0, "02_pergunta/04_esquema_com_natal.png"),
                     ("alternativas", "02_pergunta/05_esquema_sem_natal.png")],
    "004_modelagem": [(0, "03_grafo/08_cards_todos.png"), (("cada", 9), "03_grafo/04_card_no.png"),
                      (("aresta", 14), "03_grafo/05_card_aresta.png"), (("Então", 22), "03_grafo/01_caico_natal_nos.png"),
                      ("saindo", "03_grafo/02_caico_natal_seta.png"), (("Nesse", 32), "03_grafo/06_card_direcao.png"),
                      (("Para", 39), "03_grafo/07_card_peso.png"), (("Então", 50), "03_grafo/15_caico_natal_peso_ano.png")],
    "005_modelagem": [(0, "03_grafo/09_atributo_e_nao_laco.png")],
    "006_modelagem": [(0, "03_grafo/10_camadas_juntas.png"), (("internações", 5), "03_grafo/14_multicamada_3d.mp4"),
                      ("Analisando", "03_grafo/10_camadas_juntas.png"), (("No", 15.5), "03_grafo/12_almino_afonso.png"),
                      (("E", 32), "03_grafo/11_camadas_separadas.png"),
                      ("apenas", "03_grafo/11_camadas_separadas.png", {"foco": True})],
    "007_polos": [(0, "00_comum/fio_condutor_1.png"), (("E", 6.5), "04_polos/01_in_degree_vs_in_strength.png"),
                  (("Essas", 30), "01_intro/05_fluxos_peso.png")],
    "008_polos": [(0, "04_polos/02_bacias.png"), (("Esses", 11), "04_polos/05_top5_85_peso.png"),
                  (("Natal", 16), "04_polos/03_bacias_natal.png"), (("e", 21.5), "04_polos/04_bacias_mossoro.png"),
                  (("Esses", 28), "04_polos/06_skyline_3d.mp4")],
    "008b_oeste": [(0, "04_polos/03_bacias_natal.png"), ("Mossoró", "04_polos/04_bacias_mossoro.png")],
    "009_polos": [(0, "04_polos/07_bfs_natal_camada0.png"), ("vizinhos", "04_polos/08_bfs_natal_camada1.png"),
                  (("E", 15.5), "04_polos/09_bfs_mossoro_camada0.png"),
                  ("bacinha", "04_polos/10_bfs_mossoro_camada1.png"), (("o", 22.5), "04_polos/11_bfs_todas.png")],
    "010_dependencia": [(0, "05_dependencia/01_parnamirim_15_destinos.png"),
                        (("No", 19), "05_dependencia/02_parnamirim_97_natal.png"),
                        (("Então", 25), "05_dependencia/03_muitos_destinos.png"),
                        (("Nesse", 32), "05_dependencia/02_parnamirim_97_natal.png")],
    "011_dependencia": [(0, "05_dependencia/04_mapa_dependencia.png")],
    "012_remocao": [(0, "00_comum/fio_condutor_2.png"), (("Se", 4), "06_remocao/01_quiz.png"),
                    ("Naturalmente", "04_polos/02_bacias.png"), (("E", 18), "01_intro/06_enfase_natal.png"),
                    (("No", 28), "06_remocao/01_quiz.png")],
    "013_remocao": [(0, "06_remocao/05_remocao_mossoro.mp4", {"vel": 0.6}),
                    (("Nesse", 10), "06_remocao/06_lista_isolados.png"),
                    (("Isso", 17), "06_remocao/07_hipotese_e_teste.png")],
    "014_centralidade": [(0, "07_centralidade/01_tres_medidas.png"),
                         (("Com", 21), "07_centralidade/03_natal_vs_mossoro.png"),
                         (("Em", 29), "07_centralidade/04_betweenness_ceara_mirim.png")],
    "014b_ponte": [(0, "07_centralidade/04_betweenness_ceara_mirim.png"),
                   (("Além", 15), "07_centralidade/03_natal_vs_mossoro.png"),
                   (("No", 24), "06_remocao/02_quiz_resposta.png"),
                   (("Ou", 36), "07_centralidade/02_central_nao_e_critico.png")],
    "015_kcore": [(0, "08_kcore/02_aneis.png"), (("Eu", 14.5), "08_kcore/04_kcore_3d.mp4"),
                  (("No", 24), "08_kcore/03_aneis_caico_pdf.png"), (("Embora", 34), "08_kcore/01_hub_nao_e_nucleo.png")],
    "016_bipartida": [(0, "09_bipartida/01_bipartida.png")],
    "017_bipartida": [(0, "09_bipartida/02_projecao.png"), ("Entretanto", "09_bipartida/03_walfredo_artefato_clique.png")],
    "018_bipartida": [(0, "09_bipartida/03_walfredo_artefato_clique.png"), ("Por", "09_bipartida/04_jaccard.png")],
    "019_bipartida": [(0, "09_bipartida/05_jaccard_aviso.png")],
    "020_bipartida": [(0, "09_bipartida/06_mossoro_vs_pau_dos_ferros.png"), (("Por", 16), "00_comum/fio_condutor_3.png")],
    "021_respostas": [(0, "10_resposta/02_card_mossoro.png"), (("cinco", 5), "06_remocao/04_rotas_sem_mossoro.png"),
                      (("Além", 10), "10_resposta/02_card_mossoro.png")],
    "021b_natal": [(0, "10_resposta/01_card_natal.png"), (("Quase", 12), "06_remocao/02_quiz_resposta.png"),
                   (("E", 15.5), "10_resposta/01_card_natal.png"), (("Você", 21), "01_intro/05_fluxos_peso.png"),
                   (("No", 29), "10_resposta/01_card_natal.png")],
    "021c_interior": [(0, "10_resposta/03_card_interior.png")],
    "022_respostas": [(0, "10_resposta/04_tres_cards.png"), (("O", 25), "10_resposta/05_frase_central.png")],
    "023_respostas": [(0, "11_sensibilidade/01_limiares.png"),
                      (("E", 19), "11_sensibilidade/02_pdf_mossoro_vs_santa_cruz.png"),
                      (("como", 30.5), "11_sensibilidade/03_robusto.png")],
    "024_limitacoes": [(0, "12_limitacoes/01_limitacoes_1.png"), (("O", 14.5), "12_limitacoes/02_limitacoes_2.png"),
                       (("O", 22.5), "12_limitacoes/03_limitacoes_3.png"),
                       (("Além", 40), "12_limitacoes/04_limitacoes_4.png")],
    "025_limitacao": [(0, "12_limitacoes/05_limitacoes_5.png")],
    "026a_final": [(0, "10_resposta/06_mapa_tres_criticidades.png"), (("Natal", 5), "10_resposta/01_card_natal.png")],
    "026b_oeste": [(0, "10_resposta/02_card_mossoro.png")],
    "026c_final": [(0, "10_resposta/03_card_interior.png"),
                  (("Esse", 26), "13_encerramento/01_titulo_final.png"),
                  ("próximos", "13_encerramento/02_proximos_passos.png"),
                  (("Eu", 62), "13_encerramento/01_titulo_final.png")],
}



def trocar_cena(nome, novas):
    """Substitui a cena `nome` pelas cenas `novas`, na mesma posição da linha do tempo."""
    global CENAS
    itens = []
    for k, v in CENAS.items():
        itens.extend(novas.items() if k == nome else [(k, v)])
    CENAS = dict(itens)


if (ME / "regravação multirao.mp4").exists():       # "pedra nos rins" -> catarata e vesícula
    TRECHOS.update({"006_modelagem": ("006_modelagem", None, 15.85),
                    "006b_mutirao": ("regravação multirao", None, None),
                    "006c_modelagem": ("006_modelagem", 32.45, None)})
    trocar_cena("006_modelagem", {
        "006_modelagem": [(0, "03_grafo/10_camadas_juntas.png"),
                          (("internações", 5), "03_grafo/14_multicamada_3d.mp4"),
                          ("Analisando", "03_grafo/10_camadas_juntas.png")],
        "006b_mutirao": [(0, "03_grafo/12_almino_afonso.png")],
        "006c_modelagem": [(0, "03_grafo/11_camadas_separadas.png"),
                           ("apenas", "03_grafo/11_camadas_separadas.png", {"foco": True})]})

if (ME / "regravaofluxo.mp4").exists():      # "in-degree é a força de entrada" -> grau de entrada
    TRECHOS.update({"007_polos": ("007_polos", None, 12.5),
                    "007b_indegree": ("regravaofluxo", None, None),
                    "007c_polos": ("007_polos", 21.58, None)})
    trocar_cena("007_polos", {
        "007_polos": [(0, "00_comum/fio_condutor_1.png"), (("E", 6.5), "04_polos/01_in_degree_vs_in_strength.png")],
        "007b_indegree": [(0, "04_polos/01_in_degree_vs_in_strength.png")],
        "007c_polos": [(0, "04_polos/01_in_degree_vs_in_strength.png"),
                       (("Essas", 30), "01_intro/05_fluxos_peso.png")]})

if (ME / "regravacao indegree.mp4").exists():         # 5 polos das bacias (com Caicó) = 84%
    TRECHOS.update({"008_polos": ("008_polos", None, 11.0),
                    "008a_polos84": ("regravacao indegree", None, None),
                    "008c_polos": ("008_polos", 16.55, 32.35)})
    trocar_cena("008_polos", {
        "008_polos": [(0, "04_polos/02_bacias.png")],
        "008a_polos84": [(0, "04_polos/05_cinco_polos_peso.png")],
        "008c_polos": [(0, "04_polos/03_bacias_natal.png"), (("e", 21.5), "04_polos/04_bacias_mossoro.png"),
                       (("Esses", 28), "04_polos/06_skyline_3d.mp4")]})


def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def palavras(clip):
    segs = json.load(open(AQUI / "transcricao" / f"{clip}.json", encoding="utf-8"))
    ws = [(w["ini"], re.sub(r"\W", "", sem_acento(w["p"]))) for s in segs for w in s["palavras"]]
    return ws or [(s["ini"], re.sub(r"\W", "", sem_acento(s["texto"].split()[0]))) for s in segs]


def quando(clip, q):
    """Segundos na gravação original."""
    if isinstance(q, (int, float)):
        return float(q)
    chave, depois = (q, 0.0) if isinstance(q, str) else q
    chave = sem_acento(chave)
    for t, w in palavras(clip):
        if t >= depois and w == chave:
            return max(0.0, t - 0.1)
    raise ValueError(f"{clip}: palavra {q!r} não encontrada")


def ffmpeg(*args):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def duracao(f):
    out = subprocess.run([FF, "-i", str(f)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def limites_fala(f, folga_fim=FOLGA_FIM, corte_ini=None, corte_fim=None):
    """Início e fim da fala, pelo silêncio nas pontas (ffmpeg silencedetect)."""
    out = subprocess.run([FF, "-hide_banner", "-nostats", "-i", str(f), "-af", "silencedetect=noise=-38dB:d=0.4",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    dur = duracao(f)
    ini = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    fim = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    fim += [dur] * (len(ini) - len(fim))
    a = fim[0] - FOLGA_INI if ini and ini[0] < 0.05 else 0.0
    b = ini[-1] + folga_fim if fim and fim[-1] >= dur - 0.05 else dur
    a = a if corte_ini is None else corte_ini
    b = b if corte_fim is None else corte_fim
    return max(0.0, a), min(dur, b)


def quadros(f):
    out = subprocess.run([FF, "-i", str(f), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return int(re.findall(r"frame=\s*(\d+)", out)[-1])


def ligar(rel):
    destino = PUB / "fig" / rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.unlink()
    os.link(VID / rel, destino)


def ocupa_rodape(img_path):
    """True se a figura tem conteúdo na faixa do rodapé (canto inferior esquerdo)."""
    a = np.asarray(Image.open(img_path).convert("L"))[990:1072, 20:935]
    return bool((a < 235).mean() > 0.002)


def main():
    (PUB / "cam").mkdir(parents=True, exist_ok=True)
    (PUB / "hold").mkdir(parents=True, exist_ok=True)
    pular = "--sem-cortes" in sys.argv
    usados = {c[1] for cues in CENAS.values() for c in cues}
    for rel in sorted(usados) + [f"00_comum/{r}.png" for r in set(RODAPE.values())]:
        ligar(rel)
    for mov in MOVIMENTOS:
        hold = PUB / "hold" / (Path(mov).stem + ".png")
        if not hold.exists():
            ffmpeg("-sseof", "-0.2", "-i", str(VID / mov), "-frames:v", "1", "-update", "1", str(hold))

    linhas, total = [], 0
    for clip, cues in CENAS.items():
        gravacao, t_ini, t_fim = TRECHOS.get(clip, (clip, None, None))
        origem = ME / f"{gravacao}.mp4"
        if not origem.exists():
            print(f"{clip:18s} (sem gravação {gravacao}.mp4 — pulado)")
            continue
        a, b = limites_fala(origem, FOLGA_FIM_CLIP.get(clip, FOLGA_FIM), t_ini, t_fim)
        saida = PUB / "cam" / f"{clip}.mp4"
        if not (pular and saida.exists()):
            x, y, lado = CAM
            ffmpeg("-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(origem), "-filter_complex",
                   f"[0:v]crop={lado}:{lado}:{x}:{y},setpts=PTS/{RITMO},fps={FPS}[v];"
                   f"[0:a]atempo={RITMO},loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                   f"afade=t=in:d=0.05,areverse,afade=t=in:d=0.08,areverse[s]",
                   "-map", "[v]", "-map", "[s]", "-c:v", "libx264", "-crf", "14", "-preset", "medium",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(saida))
        n = quadros(saida)
        # deixas -> quadros na saída
        marcas = []
        for q, fig, *op in cues:
            t = (quando(gravacao, q) - a) / RITMO
            marcas.append((max(0, round(t * FPS)), fig, op[0] if op else {}))
        marcas[0] = (0, *marcas[0][1:])
        marcas = sorted(marcas, key=lambda m: m[0])
        blocos = []
        for i, (ini, fig, op) in enumerate(marcas):
            fim = marcas[i + 1][0] if i + 1 < len(marcas) else n
            if fig in MOVIMENTOS:
                lim = ini + int(MOVIMENTOS[fig] / op.get("vel", 1) * FPS)
                blocos.append((ini, min(fim, lim), fig, "mov", op))
                if fim > lim:
                    blocos.append((lim, fim, "hold/" + Path(fig).stem + ".png", "hold", {}))
            else:
                blocos.append((ini, fim, fig, "foco" if op.get("foco") else "img", op))
        linhas.append((clip, n, blocos, a, b))
        total += n
        print(f"{clip:18s} {a:5.2f}–{b:6.2f}s -> {n / FPS:6.2f}s  {len(blocos)} blocos")
    print(f"TOTAL {total} quadros = {int(total / FPS // 60)}:{total / FPS % 60:04.1f}")
    escrever_tsx(linhas, total)


def escrever_tsx(linhas, total):
    L = []
    for clip, n, blocos, a, b in linhas:
        L.append(f'      <TransitionSeries.Sequence name="{clip}" durationInFrames={{{n}}} premountFor={{fps}}>')
        rodape = RODAPE.get(clip[:3])
        for k, (ini, fim, fig, tipo, op) in enumerate(blocos):
            prox = blocos[k + 1][4].get("fade", FADE) if k + 1 < len(blocos) else 0
            dur = min(fim - ini + prox, n - ini)   # cobre o dissolve da próxima figura
            src = fig if tipo == "hold" else "fig/" + fig
            fade = 0 if (k == 0 or tipo in ("hold", "foco")) else op.get("fade", FADE)
            nome = Path(fig).stem
            if tipo == "mov":
                L.append(f'        <Sequence name="{nome}" from={{{ini}}} durationInFrames={{{dur}}} premountFor={{fps}}>'
                         f'<Movimento src="{src}" fade={{{fade}}} vel={{{op.get("vel", 1)}}} /></Sequence>')
            elif tipo == "foco":
                L.append(f'        <Sequence name="{nome} (foco na urgência)" from={{{ini}}} durationInFrames={{{dur}}} '
                         f'premountFor={{fps}}><FocoUrgencia src="{src}" /></Sequence>')
            else:
                L.append(f'        <Sequence name="{nome}" from={{{ini}}} durationInFrames={{{dur}}} premountFor={{fps}}>'
                         f'<Figura src="{src}" dur={{{dur}}} fade={{{fade}}} zoom={{{"false" if tipo == "hold" else "true"}}} /></Sequence>')
            caminho = PUB / ("hold/" + Path(fig).stem + ".png" if tipo == "mov" else src)
            if rodape and not ocupa_rodape(caminho):
                L.append(f'        <Sequence name="rodapé" from={{{ini}}} durationInFrames={{{fim - ini}}}>'
                         f'<Rodape src="fig/00_comum/{rodape}.png" /></Sequence>')
        L.append(f'        <Webcam src="cam/{clip}.mp4" />')
        L.append("      </TransitionSeries.Sequence>")
    corpo = "\n".join(L)
    tsx = f'''// Gerado por preparar.py — edite as deixas lá (ou ajuste aqui pelo Studio).
import {{Video}} from "@remotion/media";
import {{TransitionSeries}} from "@remotion/transitions";
import {{AbsoluteFill, Easing, Img, interpolate, Sequence, staticFile, useCurrentFrame, useVideoConfig}} from "remotion";

export const DURACAO_TOTAL = {total};
const FUNDO = "#fcfcfb";
const AZUL = "#2a78d6";
const opacidade = (frame: number, fade: number) =>
  fade > 0 ? interpolate(frame, [0, fade], [0, 1], {{extrapolateLeft: "clamp", extrapolateRight: "clamp"}}) : 1;

const Figura: React.FC<{{src: string; dur: number; fade: number; zoom: boolean}}> = ({{src, dur, fade, zoom}}) => {{
  const frame = useCurrentFrame();
  const escala = zoom ? interpolate(frame, [0, dur], [1, 1.025], {{extrapolateRight: "clamp"}}) : 1;
  return (
    <AbsoluteFill style={{{{backgroundColor: FUNDO, opacity: opacidade(frame, fade)}}}}>
      <Img src={{staticFile(src)}} style={{{{width: 1920, height: 1080, transform: `scale(${{escala}})`, transformOrigin: "38.5% 50%"}}}} />
    </AbsoluteFill>
  );
}};

const Movimento: React.FC<{{src: string; fade: number; vel: number}}> = ({{src, fade, vel}}) => {{
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{{{backgroundColor: FUNDO, opacity: opacidade(frame, fade)}}}}>
      <Video src={{staticFile(src)}} muted playbackRate={{vel}} style={{{{width: 1920, height: 1080}}}} />
    </AbsoluteFill>
  );
}};

// Camadas lado a lado -> a eletiva some e a câmera aproxima o mapa da urgência (metade esquerda da figura).
const FocoUrgencia: React.FC<{{src: string}}> = ({{src}}) => {{
  const frame = useCurrentFrame();
  const some = interpolate(frame, [0, 12], [0, 1], {{extrapolateLeft: "clamp", extrapolateRight: "clamp"}});
  const p = interpolate(frame, [8, 40], [0, 1], {{extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.cubic)}});
  return (
    <AbsoluteFill style={{{{backgroundColor: FUNDO}}}}>
      <AbsoluteFill style={{{{transform: `translate(${{40 * p}}px, ${{-510 * p}}px) scale(${{1 + 0.5 * p}})`,
        transformOrigin: "0 0"}}}}>
        <Img src={{staticFile(src)}} style={{{{width: 1920, height: 1080}}}} />
        <div style={{{{position: "absolute", left: 960, top: 0, width: 960, height: 1080, backgroundColor: FUNDO,
          opacity: some}}}} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
}};

const Rodape: React.FC<{{src: string}}> = ({{src}}) => (
  <AbsoluteFill>
    <Img src={{staticFile(src)}} style={{{{width: 1920, height: 1080}}}} />
  </AbsoluteFill>
);

// Câmera: círculo de {CAM[2]} px recortado das gravações, com anel azul por fora (mesma posição do OBS).
const Webcam: React.FC<{{src: string}}> = ({{src}}) => (
  <AbsoluteFill>
    <div style={{{{position: "absolute", left: {CAM[0] - 6}, top: {CAM[1] - 6}, width: {CAM[2] + 12}, height: {CAM[2] + 12},
      borderRadius: "50%", backgroundColor: AZUL, boxShadow: "0 6px 24px rgba(0,0,0,0.18)"}}}} />
    <Video src={{staticFile(src)}} style={{{{position: "absolute", left: {CAM[0]}, top: {CAM[1]}, width: {CAM[2]},
      height: {CAM[2]}, borderRadius: "50%", overflow: "hidden"}}}} />
  </AbsoluteFill>
);

export const VideoFinal: React.FC = () => {{
  const {{fps}} = useVideoConfig();
  return (
    <AbsoluteFill style={{{{backgroundColor: FUNDO}}}}>
    <TransitionSeries name="Vídeo">
{corpo}
    </TransitionSeries>
    </AbsoluteFill>
  );
}};
'''
    (AQUI / "src" / "VideoFinal.tsx").write_text(tsx, encoding="utf-8")
    (AQUI / "src" / "Root.tsx").write_text(f'''import {{Composition}} from "remotion";
import {{VideoFinal}} from "./VideoFinal";

export const RemotionRoot: React.FC = () => {{
  return (
    <Composition
      id="VideoFinal"
      component={{VideoFinal}}
      durationInFrames={{{total}}}
      fps={{{FPS}}}
      width={{1920}}
      height={{1080}}
    />
  );
}};
''', encoding="utf-8")


if __name__ == "__main__":
    main()
