"""Figuras e animações do vídeo, cena por cena, de acordo com docs/roteiro.txt.

Rodar da raiz do repositório:
    pip install imageio-ffmpeg        # só para exportar os MP4
    python docs/vid/gerar_figuras.py            # tudo
    python docs/vid/gerar_figuras.py 01 04      # só algumas cenas

Tudo sai em 1920 × 1080. O canto superior direito (PIP) fica livre para a webcam: ao salvar, o
script avisa se algum texto ou caixa invadiu esse espaço. As figuras com fundo transparente
(rodapés e guia da webcam) servem para sobrepor no editor.
"""
import gzip
import json
import sys
import urllib.request
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
from matplotlib import animation, font_manager
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.colors import to_rgba
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle
from matplotlib.text import Text
from matplotlib.transforms import Bbox
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

RAIZ = Path(__file__).resolve().parents[2]
PROCESSED = RAIZ / "data" / "processed"
SAIDA = Path(__file__).resolve().parent
MALHA = SAIDA / "dados" / "malha_rn_municipios.geojson"
URL_MALHA = ("https://servicodados.ibge.gov.br/api/v3/malhas/estados/24"
             "?formato=application/vnd.geo%2Bjson&qualidade=intermediaria&intrarregiao=municipio")

# ---------------------------------------------------------------- estilo e layout
AZUL, LARANJA, VERDE, AMARELO, ROSA = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
VERMELHO, CINZA, CINZA_CLARO = "#e34948", "#c3c2b7", "#e9e8e3"
TINTA, TINTA_2, FUNDO, AZUL_ESCURO = "#0b0b0b", "#52514e", "#fcfcfb", "#104281"
COR_BACIA = {"Natal": AZUL, "Mossoró": LARANJA, "Pau dos Ferros": VERDE, "Caicó": AMARELO, "Santa Cruz": ROSA}

for fonte in ["Segoe UI", "DejaVu Sans"]:
    try:
        font_manager.findfont(fonte, fallback_to_default=False)
        plt.rcParams["font.family"] = fonte
        break
    except ValueError:
        pass
plt.rcParams.update({"figure.dpi": 100, "savefig.dpi": 100, "text.color": TINTA})

W, H = 19.2, 10.8                   # 1920 × 1080
FPS = 30
PIP = (0.765, 0.68, 0.985, 0.975)   # espaço da webcam (x0, y0, x1, y1), em fração da tela
CX = 0.385                          # centro horizontal do conteúdo (os 3/4 da esquerda)
LON, LAT = (-38.65, -34.85), (-7.0, -4.75)
ASPECTO = 1 / np.cos(np.deg2rad(5.8))


def milhar(x):
    return f"{x:,.0f}".replace(",", ".")


# ---------------------------------------------------------------- dados
def carregar():
    nos = pd.read_csv(PROCESSED / "nos_rn_2024_urgencia.csv", dtype={"cod6": str}).set_index("cod6")
    G = nx.read_graphml(PROCESSED / "rede_rn_2024_urgencia.graphml")
    U = nx.DiGraph()
    U.add_nodes_from(nos.index)
    U.add_edges_from((o, v, {"weight": int(d["weight"])}) for o, v, d in G.edges(data=True))
    cod = pd.Series(nos.index, index=nos["label"].values)

    df = pd.read_parquet(PROCESSED / "rd_rn_2024.parquet")
    df = df[(df["IDENT"] == "1") & df["NOME_RES"].notna()]
    fora = df[df["MUNIC_RES"] != df["MUNIC_MOV"]]
    eletiva = nx.DiGraph()
    eletiva.add_nodes_from(nos.index)
    for (o, v), w in fora[fora["CAMADA"] == "eletiva"].groupby(["MUNIC_RES", "MUNIC_MOV"]).size().items():
        if o in nos.index and v in nos.index:
            eletiva.add_edge(o, v, weight=int(w))

    if not MALHA.exists():
        MALHA.parent.mkdir(parents=True, exist_ok=True)
        conteudo = urllib.request.urlopen(URL_MALHA).read()
        if conteudo[:2] == b"\x1f\x8b":  # a API do IBGE pode responder compactado
            conteudo = gzip.decompress(conteudo)
        MALHA.write_bytes(conteudo)
    poligonos = []  # (cod6, anel externo)
    for f in json.loads(MALHA.read_text(encoding="utf-8"))["features"]:
        c = f["properties"]["codarea"][:6]
        geo = f["geometry"]
        partes = [geo["coordinates"]] if geo["type"] == "Polygon" else geo["coordinates"]
        poligonos += [(c, np.array(p[0])) for p in partes]
    return nos, U, eletiva, cod, poligonos


nos, U, ELETIVA, cod, POLIGONOS = carregar()
POS = {c: np.array([nos.at[c, "longitude"], nos.at[c, "latitude"]]) for c in nos.index}


def rotas(G, limiar):
    R = nx.DiGraph()
    R.add_nodes_from(G)
    R.add_edges_from((o, v, d) for o, v, d in G.edges(data=True) if d["weight"] >= limiar)
    return R


def isolados_sem(polo, limiar):
    R = rotas(U, limiar)
    R.remove_node(polo)
    maior = max(nx.weakly_connected_components(R), key=len)
    return sorted([c for c in R if c not in maior], key=lambda c: nos.at[c, "label"])


# ---------------------------------------------------------------- salvar e conferir a webcam
def nova_fig(transparente=False):
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor("none" if transparente else FUNDO)
    return fig


def invade_pip(fig):
    """Textos e caixas cuja área desenhada cruza o espaço da webcam."""
    r = fig.canvas.get_renderer()
    pip = Bbox.from_extents(PIP[0] * W * 100, PIP[1] * H * 100, PIP[2] * W * 100, PIP[3] * H * 100)
    culpados = []
    for t in fig.findobj(Text):
        if t.get_visible() and t.get_text().strip() and t.get_window_extent(r).overlaps(pip):
            culpados.append(repr(t.get_text()[:30]))
    for p in fig.patches:
        if p.get_window_extent(r).overlaps(pip):
            culpados.append("caixa")
    return culpados


def salvar(fig, caminho, transparente=False):
    caminho = SAIDA / caminho
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()
    culpados = invade_pip(fig)
    fig.savefig(caminho, transparent=transparente, facecolor="none" if transparente else FUNDO)
    plt.close(fig)
    print("  ", caminho.relative_to(SAIDA), f"  << invade a webcam: {culpados}" if culpados else "")


SEM_VIDEO = "--sem-video" in sys.argv


def gravar(fig, atualizar, n_quadros, caminho):
    if SEM_VIDEO:
        plt.close(fig)
        return
    import imageio_ffmpeg
    plt.rcParams["animation.ffmpeg_path"] = imageio_ffmpeg.get_ffmpeg_exe()
    caminho = SAIDA / caminho
    caminho.parent.mkdir(parents=True, exist_ok=True)
    anim = animation.FuncAnimation(fig, atualizar, frames=n_quadros, blit=False)
    anim.save(caminho, writer=animation.FFMpegWriter(fps=FPS, bitrate=8000), savefig_kwargs={"facecolor": FUNDO})
    plt.close(fig)
    print("  ", caminho.relative_to(SAIDA))


def suave(t):
    """Easing: começa e termina devagar."""
    t = np.clip(t, 0, 1)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- mapas
def eixo_mapa(fig, rect=(0.01, 0.07, 0.75, 0.84)):
    ax = fig.add_axes(rect)
    ax.set_xlim(*LON)
    ax.set_ylim(*LAT)
    ax.set_aspect(ASPECTO)
    ax.axis("off")
    return ax


def municipios(ax, cor=None, padrao=CINZA_CLARO, borda=FUNDO, lw=0.6, alpha=1.0, z=1):
    cor = cor or {}
    ax.add_collection(PolyCollection([anel for _, anel in POLIGONOS],
                                     facecolors=[cor.get(c, padrao) for c, _ in POLIGONOS],
                                     edgecolors=borda, linewidths=lw, alpha=alpha, zorder=z))


def curva(p0, p1, curv=0.18, n=40):
    meio, d = (p0 + p1) / 2, p1 - p0
    ctrl = meio + curv * np.array([-d[1], d[0]])
    t = np.linspace(0, 1, n)[:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * ctrl + t ** 2 * p1


def fluxos(ax, arestas, cor=AZUL, lw_max=4.0, alpha=0.55, frac=1.0, z=3, peso_ref=None, linear=False):
    """Arestas como curvas; `frac` < 1 desenha só o começo de cada curva (para animar o crescimento).

    `linear=True`: espessura proporcional ao peso (as grossas embaixo, para não esconder as finas)."""
    if not arestas:
        return
    peso_ref = peso_ref or max(w for *_, w in arestas)
    if linear:
        arestas = sorted(arestas, key=lambda e: -e[2])
    segs, larg = [], []
    for o, v, w in arestas:
        pts = curva(POS[o], POS[v])
        segs.append(pts[:max(2, int(len(pts) * frac))])
        larg.append(0.3 + lw_max * w / peso_ref if linear else 0.4 + lw_max * np.sqrt(w / peso_ref))
    if linear:
        cores = [to_rgba(cor, alpha * (0.35 + 0.65 * np.sqrt(w / peso_ref))) for *_, w in arestas]
        ax.add_collection(LineCollection(segs, linewidths=larg, colors=cores, capstyle="round", zorder=z))
        return
    ax.add_collection(LineCollection(segs, linewidths=larg, colors=cor, alpha=alpha, capstyle="round", zorder=z))


LW_PESO = 16.0   # espessura (pt) do maior fluxo nas figuras "_peso"


def legenda_peso(fig, peso_ref, cor=AZUL, x=0.02, y=0.03):
    """Três traços de referência com a mesma escala de `fluxos(..., linear=True)`."""
    ax = fig.add_axes((x, y, 0.17, 0.2))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0, 0.95, "internações/ano", fontsize=20, color=TINTA_2, va="top")
    for k, w in enumerate([100, 1000, 3000]):
        yk = 0.62 - 0.27 * k
        ax.plot([0, 0.38], [yk, yk], lw=0.3 + LW_PESO * w / peso_ref, color=cor, alpha=0.8, solid_capstyle="butt")
        ax.text(0.46, yk, milhar(w), fontsize=20, color=TINTA, va="center")


ZOOM_NATAL = ((-35.56, -35.06), (-6.14, -5.58))
METRO = {"Parnamirim": "Parnamirim", "São Gonçalo do Amarante": "S. G. do Amarante", "Macaíba": "Macaíba",
         "Extremoz": "Extremoz", "Ceará-Mirim": "Ceará-Mirim", "São José de Mipibu": "S. J. de Mipibu"}


def lupa_natal(fig, ax_mapa, arestas, rect=(0.775, 0.05, 0.2, 0.38)):
    """Zoom na região de Natal: os maiores fluxos são curtos e somem no mapa inteiro."""
    (x0, x1), (y0, y1) = ZOOM_NATAL
    ax_mapa.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec=TINTA_2, lw=1.5, ls="--", zorder=4))
    ax = fig.add_axes(rect)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect(ASPECTO)
    ax.axis("off")
    municipios(ax)
    dentro = [e for e in arestas if e[1] == NATAL and x0 < POS[e[0]][0] < x1 and y0 < POS[e[0]][1] < y1]
    fluxos(ax, dentro, cor=AZUL, alpha=0.75, lw_max=LW_PESO, peso_ref=PESO_MAX, linear=True)
    ponto(ax, NATAL, AZUL_ESCURO, tam=220)
    ax.text(*(POS[NATAL] + [0.02, 0.0]), "Natal", fontsize=16, fontweight="bold", va="center", zorder=8)
    for c in nos.index[nos["label"].isin(list(METRO))]:
        ax.scatter(*POS[c], s=30, color=TINTA_2, zorder=7)
        ax.text(*(POS[c] + [0, -0.012]), METRO[nos.at[c, "label"]], fontsize=13, ha="center", va="top", zorder=8,
                bbox=dict(boxstyle="round,pad=0.15", fc=FUNDO, ec="none", alpha=0.8))
    ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes, fill=False, ec=TINTA_2, lw=1.5, ls="--"))
    ax.text(0.03, 0.97, "zoom: região de Natal", transform=ax.transAxes, fontsize=16, color=TINTA_2, va="top")


def ponto(ax, c, cor, tam=200, z=6, halo=False, alpha=1.0):
    x, y = POS[c]
    if halo:
        for r, a in [(tam * 9, 0.08), (tam * 4.5, 0.15), (tam * 2.2, 0.25)]:
            ax.scatter(x, y, s=r, color=cor, alpha=a * alpha, linewidths=0, zorder=z - 1)
    ax.scatter(x, y, s=tam, color=cor, edgecolors=FUNDO, linewidths=2, alpha=alpha, zorder=z)


def rotulo(ax, c, texto, dx=0.06, dy=0.06, tam=22, cor=TINTA, peso="bold", ha="left", z=8, caixa=False):
    x, y = POS[c]
    bbox = dict(boxstyle="round,pad=0.35", fc=FUNDO, ec="none", alpha=0.85) if caixa else None
    ax.text(x + dx, y + dy, texto, fontsize=tam, color=cor, fontweight=peso, ha=ha, va="bottom", zorder=z, bbox=bbox)


def x_isolado(ax, c, alpha=1.0, tam=0.08):
    x, y = POS[c]
    for sx in (1, -1):
        ax.plot([x - tam, x + tam], [y - sx * tam, y + sx * tam], color=VERMELHO, lw=8, zorder=9, alpha=alpha)


# ---------------------------------------------------------------- textos e esquemas
def titulo(fig, texto, sub=None, y=0.93, tam=40):
    fig.text(0.03, y, texto, fontsize=tam, fontweight="bold", color=TINTA, va="center")
    if sub:
        fig.text(0.03, y - 0.055, sub, fontsize=22, color=TINTA_2, va="center")


def cartao(caminho, linhas, fundo_mapa=False, x=CX):
    """Quadro de texto centralizado na área de conteúdo. `linhas`: lista de (texto, tamanho, cor, negrito)."""
    fig = nova_fig()
    if fundo_mapa:
        ax = eixo_mapa(fig, (0.03, 0.1, 0.7, 0.8))
        municipios(ax, padrao="#f1f0ec", borda=FUNDO)
    total = sum(t * 1.55 for _, t, *_ in linhas)
    y = 0.47 + total / 2 / 1080
    for texto, tam, cor, negrito in linhas:
        y -= tam * 1.55 / 1080 / 2
        fig.text(x, y, texto, fontsize=tam, color=cor, ha="center", va="center", fontweight="bold" if negrito else "normal")
        y -= tam * 1.55 / 1080 / 2
    salvar(fig, caminho)


def caixa(fig, x, y, w, h, cor, fundo="white", lw=4, alpha=1.0):
    fig.patches.append(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.005,rounding_size=0.015",
                                      transform=fig.transFigure, fc=fundo, ec=cor, lw=lw, alpha=alpha))


def eixo_esquema(fig, rect=(0.03, 0.08, 0.7, 0.72)):
    """Eixo para mini-grafos: y de 0 a 1 e x de 0 a `r` (proporção do retângulo), com escala igual."""
    ax = fig.add_axes(rect)
    r = (rect[2] * W) / (rect[3] * H)
    ax.set_xlim(0, r)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.r = r
    return ax


def esquema(ax, nos_xy, setas, cores=None, removido=None, raio=0.1, fonte=30):
    """Mini-grafo conceitual; coordenadas x em 0–1 (esticadas para a largura do eixo)."""
    cores = cores or {}
    p = {n: (x * ax.r, y) for n, (x, y) in nos_xy.items()}
    for a, b in setas:
        ax.add_patch(FancyArrowPatch(p[a], p[b], arrowstyle="-|>", mutation_scale=40, color=TINTA_2, lw=5,
                                     shrinkA=raio * 900, shrinkB=raio * 900))
    for n, (x, y) in p.items():
        cor = cores.get(n, AZUL)
        tirado = n == removido
        ax.add_patch(Circle((x, y), raio, fc=FUNDO if tirado else cor, ec=VERMELHO if tirado else FUNDO, lw=5,
                            ls="--" if tirado else "-", zorder=5))
        ax.text(x, y - (raio + 0.06 if tirado else 0), n, fontsize=fonte, fontweight="bold",
                color=VERMELHO if tirado else "white", ha="center", va="center", zorder=6)
    return p


def estrela(ax, n, centro_cor, ponta_cor, larguras=None, raio_c=0.2, raio_p=0.06, seta=True):
    ax.set_xlim(-1.25, 1.25)
    ax.set_ylim(-1.25, 1.25)
    ax.set_aspect("equal")
    ax.axis("off")
    larguras = larguras or [2] * n
    for i, lw in enumerate(larguras):
        ang = 2 * np.pi * i / n + 0.3
        q = (np.cos(ang), np.sin(ang))
        if seta:
            ax.add_patch(FancyArrowPatch(q, (0, 0), arrowstyle="-|>", mutation_scale=18 + lw, lw=lw, color=centro_cor,
                                         shrinkB=36, alpha=0.85))
        else:
            ax.plot([0, q[0]], [0, q[1]], color=CINZA, lw=lw)
        ax.add_patch(Circle(q, raio_p, fc=ponta_cor, ec="none"))
    ax.add_patch(Circle((0, 0), raio_c, fc=centro_cor, ec="none", zorder=5))


# ---------------------------------------------------------------- números usados nas cenas
SEM_LOCAL = list(nos.index[nos["locais"] == 0])
NATAL, MOSSORO, CAICO, PDF = cod["Natal"], cod["Mossoró"], cod["Caicó"], cod["Pau dos Ferros"]
SJM = cod["São José de Mipibu"]
SANTA_CRUZ, PARNAMIRIM, ALMINO = cod["Santa Cruz"], cod["Parnamirim"], cod["Almino Afonso"]
POLOS = [NATAL, MOSSORO, PDF, CAICO, SANTA_CRUZ]
FORTES = [(o, v, d["weight"]) for o, v, d in U.edges(data=True) if d["weight"] >= 30]
PESO_MAX = max(w for *_, w in FORTES)
COR_GRUPO = {c: COR_BACIA.get(nos.at[c, "grupo_bacia"], CINZA) for c in nos.index}
FIO = ["Quem parece importante?", "Quem realmente causa dano?", "Existe alternativa?"]


def fio_condutor(caminho, atual):
    """Slide de transição com o fio condutor; `atual` (0, 1 ou 2) em destaque."""
    fig = nova_fig()
    for i, etapa in enumerate(FIO):
        y = 0.68 - 0.2 * i
        ativo = i == atual
        caixa(fig, CX, y, 0.58, 0.13, AZUL if ativo else CINZA, fundo=AZUL if ativo else "white", lw=4)
        fig.text(CX, y, etapa, fontsize=46, fontweight="bold", ha="center", va="center",
                 color="white" if ativo else CINZA)
        if i < 2:
            fig.text(CX, y - 0.1, "↓", fontsize=36, color=CINZA, ha="center", va="center")
    salvar(fig, caminho)


def pare_e_pense(caminho, pergunta, opcoes=None, linhas_extras=()):
    fig = nova_fig()
    caixa(fig, CX, 0.86, 0.26, 0.08, AMARELO, fundo=AMARELO)
    fig.text(CX, 0.86, "PARE E PENSE", fontsize=34, fontweight="bold", color="white", ha="center", va="center")
    fig.text(CX, 0.66, pergunta, fontsize=46, fontweight="bold", ha="center", va="center", linespacing=1.3)
    for i, extra in enumerate(linhas_extras):
        fig.text(CX, 0.52 - 0.06 * i, extra, fontsize=30, color=TINTA_2, ha="center", va="center")
    for i, (op, cor) in enumerate(opcoes or []):
        x = CX + (i - (len(opcoes) - 1) / 2) * 0.23
        caixa(fig, x, 0.3, 0.2, 0.15, cor)
        fig.text(x, 0.3, op, fontsize=40, fontweight="bold", color=cor, ha="center", va="center")
    salvar(fig, caminho)


# ================================================================ cenas
def cena_00():
    print("00 · comum")
    fig = nova_fig()
    municipios(eixo_mapa(fig))
    salvar(fig, "00_comum/mapa_rn_base.png")
    fig = nova_fig(transparente=True)
    fig.patches.append(Rectangle((PIP[0], PIP[1]), PIP[2] - PIP[0], PIP[3] - PIP[1], transform=fig.transFigure,
                                 fill=False, ec=VERMELHO, lw=4, ls="--"))
    fig.text((PIP[0] + PIP[2]) / 2, (PIP[1] + PIP[3]) / 2, "WEBCAM", fontsize=30, color=VERMELHO, ha="center",
             va="center", fontweight="bold")
    fig.savefig(SAIDA / "00_comum/guia_webcam.png", transparent=True)
    plt.close(fig)
    print("   00_comum/guia_webcam.png")
    for nome_arq, texto in [
        ("rodape_semana2_grafos.png", "Semana 2 · grafos dirigidos, pesos e subgrafos"),
        ("rodape_semanas2e3_grau_bfs.png", "Semanas 2 e 3 · grau, strength e BFS"),
        ("rodape_semana4_componentes.png", "Semana 4 · componentes WCC/SCC e remoção de nós"),
        ("rodape_semana4_centralidades.png", "Semana 4 · centralidades: grau, closeness e betweenness"),
        ("rodape_semana5_kcore.png", "Semana 5 · k-core, k-shell e core number"),
        ("rodape_semana6_bipartida.png", "Semana 6 · rede bipartida, projeção e Jaccard"),
        ("rodape_semana6_limiar.png", "Semana 6 · esqueleto por limiar de peso"),
    ]:
        fig = nova_fig(transparente=True)
        fig.text(0.02, 0.045, texto, fontsize=24, color="white", fontweight="bold", va="center",
                 bbox=dict(boxstyle="round,pad=0.55,rounding_size=0.4", fc=AZUL_ESCURO, ec="none", alpha=0.92))
        salvar(fig, f"00_comum/{nome_arq}", transparente=True)
    for i in range(3):
        fio_condutor(f"00_comum/fio_condutor_{i + 1}.png", i)


def cena_01():
    print("01 · introdução")
    vermelho_claro = "#f19a92"
    ordem_83 = sorted(SEM_LOCAL, key=lambda c: POS[c][0])  # acendem de oeste para leste

    fig = nova_fig()
    municipios(eixo_mapa(fig))
    salvar(fig, "01_intro/01_mapa_rn.png")

    def mapa_83(fig, n):
        ax = eixo_mapa(fig)
        municipios(ax, {c: vermelho_claro for c in ordem_83[:n]})
        fig.text(0.03, 0.905, f"{n}", fontsize=110, fontweight="bold", color=VERMELHO, va="center")
        fig.text(0.155, 0.925, "de 167 municípios", fontsize=34, fontweight="bold", va="center")
        fig.text(0.155, 0.875, "sem internação de urgência local em 2024", fontsize=24, color=TINTA_2, va="center")

    fig = nova_fig()
    mapa_83(fig, len(ordem_83))
    salvar(fig, "01_intro/02_mapa_83_de_167.png")
    fig = nova_fig()
    n_q = int(3.5 * FPS)

    def atualizar(i):
        fig.clear()
        fig.patch.set_facecolor(FUNDO)
        mapa_83(fig, int(round(len(ordem_83) * suave(i / (n_q - FPS)))))
    gravar(fig, atualizar, n_q, "01_intro/03_contador_83.mp4")

    def mapa_fluxos(fig, frac, linear=False):
        ax = eixo_mapa(fig)
        municipios(ax)
        if linear:
            fluxos(ax, FORTES, cor=AZUL, alpha=0.5, frac=frac, lw_max=LW_PESO, linear=True)
        else:
            fluxos(ax, FORTES, cor=AZUL, alpha=0.45, frac=frac)
        for c in POLOS:
            ponto(ax, c, AZUL_ESCURO, tam=90 + 0.012 * nos.at[c, "forca_entrada"])
        return ax

    fig = nova_fig()
    n_q = int(4 * FPS)

    def atualizar(i):
        fig.clear()
        fig.patch.set_facecolor(FUNDO)
        mapa_fluxos(fig, suave(i / (n_q - FPS)))
    gravar(fig, atualizar, n_q, "01_intro/04_fluxos_crescendo.mp4")
    fig = nova_fig()
    mapa_fluxos(fig, 1)
    salvar(fig, "01_intro/05_fluxos.png")
    fig = nova_fig()
    lupa_natal(fig, mapa_fluxos(fig, 1, linear=True), FORTES)
    legenda_peso(fig, PESO_MAX)
    salvar(fig, "01_intro/05_fluxos_peso.png")

    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax, alpha=0.6)
    fluxos(ax, FORTES, cor=CINZA, alpha=0.35)
    fluxos(ax, [e for e in FORTES if e[1] == NATAL], cor=AZUL, alpha=0.7)
    ponto(ax, NATAL, AZUL, tam=900, halo=True)
    rotulo(ax, NATAL, "Natal", dx=0.12, dy=0.05, tam=40, caixa=True)
    rotulo(ax, NATAL, "o maior polo", dx=0.12, dy=-0.13, tam=26, peso="normal", cor=TINTA_2, caixa=True)
    salvar(fig, "01_intro/06_enfase_natal.png")

    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax, alpha=0.6)
    fluxos(ax, FORTES, cor=CINZA, alpha=0.3)
    fluxos(ax, [e for e in FORTES if e[1] == NATAL], cor=AZUL, alpha=0.3)
    fluxos(ax, [e for e in FORTES if e[1] == MOSSORO], cor=LARANJA, alpha=0.8)
    ponto(ax, NATAL, AZUL, tam=700, alpha=0.45)
    rotulo(ax, NATAL, "Natal", dx=0.12, dy=0.05, tam=30, cor=TINTA_2, caixa=True)
    ponto(ax, MOSSORO, LARANJA, tam=800, halo=True)
    rotulo(ax, MOSSORO, "Mossoró", dx=0.12, dy=0.05, tam=40, caixa=True)
    titulo(fig, "Natal é o maior polo. Mas é o mais crítico?")
    salvar(fig, "01_intro/07_enfase_natal_e_mossoro.png")

    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax, alpha=0.6)
    fluxos(ax, [e for e in FORTES if e[1] not in (NATAL, MOSSORO)], cor=CINZA, alpha=0.4, lw_max=LW_PESO,
           peso_ref=PESO_MAX, linear=True)
    fluxos(ax, [e for e in FORTES if e[1] == NATAL], cor=AZUL, alpha=0.45, lw_max=LW_PESO, peso_ref=PESO_MAX,
           linear=True)
    fluxos(ax, [e for e in FORTES if e[1] == MOSSORO], cor=LARANJA, alpha=0.75, lw_max=LW_PESO, peso_ref=PESO_MAX,
           linear=True)
    ponto(ax, NATAL, AZUL, tam=700, alpha=0.6)
    rotulo(ax, NATAL, "Natal", dx=0.12, dy=0.05, tam=30, cor=TINTA_2, caixa=True)
    ponto(ax, MOSSORO, LARANJA, tam=800, halo=True)
    rotulo(ax, MOSSORO, "Mossoró", dx=0.12, dy=0.05, tam=40, caixa=True)
    titulo(fig, "Natal é o maior polo. Mas é o mais crítico?")
    legenda_peso(fig, PESO_MAX, cor=TINTA_2)
    salvar(fig, "01_intro/07_enfase_natal_e_mossoro_peso.png")

    cartao("01_intro/08_titulo.png", [("Quem sustenta a urgência", 76, TINTA, True), ("no RN?", 76, TINTA, True),
                                      ("Fluxo de internações do SUS entre municípios", 32, TINTA_2, False)],
           fundo_mapa=True)


def cena_02():
    print("02 · pergunta")
    perguntas = ["Quem sustenta o acesso?", "Quem depende de quem?", "O que acontece se um polo parar?"]
    for k in range(1, 4):
        cartao(f"02_pergunta/0{k}_pergunta_{k}.png",
               [(p, 58, TINTA if i == k - 1 else CINZA, True) for i, p in enumerate(perguntas[:k])])

    xy = {"A": (0.12, 0.55), "B": (0.88, 0.55), "C": (0.5, 0.12), "Natal": (0.5, 0.6)}
    fig = nova_fig()
    esquema(eixo_esquema(fig), xy, [("A", "Natal"), ("B", "Natal"), ("C", "Natal")], {"Natal": AZUL_ESCURO}, fonte=32)
    titulo(fig, "Municípios pequenos dependem de um polo")
    salvar(fig, "02_pergunta/04_esquema_com_natal.png")

    fig = nova_fig()
    ax = eixo_esquema(fig)
    p = esquema(ax, xy, [], removido="Natal", fonte=26)
    x, y = p["Natal"]
    for s in (1, -1):
        ax.plot([x - 0.07, x + 0.07], [y - s * 0.07, y + s * 0.07], color=VERMELHO, lw=8, zorder=7)
    for n in "ABC":
        ax.text(p[n][0], p[n][1] + 0.16, "?", fontsize=60, fontweight="bold", color=VERMELHO, ha="center", va="center")
    titulo(fig, "Se o polo para, quem fica sem alternativa?")
    salvar(fig, "02_pergunta/05_esquema_sem_natal.png")

    cartao("02_pergunta/06_para_quem.png",
           [("PARA QUEM?", 60, AZUL, True), ("SESAP-RN · planejamento regional", 42, TINTA, True), ("", 20, TINTA, False),
            ("onde investir recursos", 34, TINTA_2, False), ("onde ampliar a rede hospitalar", 34, TINTA_2, False),
            ("onde uma falha é mais difícil de contornar", 34, TINTA_2, False)])


def cena_03():
    print("03 · como o problema virou um grafo")
    w_caico = U[CAICO][NATAL]["weight"]
    xy = {"Caicó": (0.16, 0.5), "Natal": (0.84, 0.5)}
    for k, sufixo in enumerate(["nos", "seta", "peso"], 1):
        fig = nova_fig()
        ax = eixo_esquema(fig)
        p = esquema(ax, xy, [] if k == 1 else [("Caicó", "Natal")], {"Caicó": AMARELO, "Natal": AZUL}, raio=0.12,
                    fonte=32)
        if k == 3:
            meio = (p["Caicó"][0] + p["Natal"][0]) / 2
            ax.text(meio, 0.72, f"{w_caico} internações de urgência em 2024", fontsize=34, fontweight="bold",
                    ha="center")
            ax.text(meio, 0.36, "residência  →  internação", fontsize=26, ha="center", color=TINTA_2)
        salvar(fig, f"03_grafo/0{k}_caico_natal_{sufixo}.png")
    fig = nova_fig()
    p = esquema(eixo_esquema(fig), xy, [("Caicó", "Natal")], {"Caicó": AMARELO, "Natal": AZUL}, raio=0.12, fonte=32)
    meio = (p["Caicó"][0] + p["Natal"][0]) / 2
    ax = fig.axes[0]
    ax.text(meio, 0.72, "peso = nº de internações no ano", fontsize=34, fontweight="bold", ha="center")
    ax.text(meio, 0.36, "residência  →  internação", fontsize=26, ha="center", color=TINTA_2)
    salvar(fig, "03_grafo/15_caico_natal_peso_ano.png")

    cards = [("NÓ", "município", AZUL, "no"), ("ARESTA", "fluxo hospitalar", LARANJA, "aresta"),
             ("DIREÇÃO", "residência → internação", VERDE, "direcao"), ("PESO", "nº de internações", AMARELO, "peso")]
    for k, (t, s, cor, arq) in enumerate(cards, 4):
        cartao(f"03_grafo/0{k}_card_{arq}.png", [(t, 90, cor, True), (s, 44, TINTA, False)])
    fig = nova_fig()
    for i, (t, s, cor, _) in enumerate(cards):
        x, y = CX - 0.19 + 0.38 * (i % 2), 0.62 - 0.32 * (i // 2)
        caixa(fig, x, y, 0.34, 0.25, cor)
        fig.text(x, y + 0.04, t, fontsize=52, fontweight="bold", color=cor, ha="center", va="center")
        fig.text(x, y - 0.05, s, fontsize=30, color=TINTA, ha="center", va="center")
    salvar(fig, "03_grafo/08_cards_todos.png")

    fig = nova_fig()
    ax = eixo_esquema(fig)
    cx = 0.22 * ax.r
    ax.add_patch(Circle((cx, 0.45), 0.13, fc=AZUL, ec=FUNDO, lw=5))
    ax.text(cx, 0.45, "Natal", fontsize=34, fontweight="bold", color="white", ha="center", va="center")
    ax.add_patch(Circle((cx, 0.66), 0.09, fill=False, ec=CINZA, lw=6, zorder=2))
    for sx in (1, -1):
        ax.plot([cx - 0.08, cx + 0.08], [0.66 - sx * 0.08, 0.66 + sx * 0.08], color=VERMELHO, lw=8, zorder=3)
    ax.text(cx, 0.8, "laço: não", fontsize=28, fontweight="bold", color=VERMELHO, ha="center")
    ax.text(0.42 * ax.r, 0.55, f"{milhar(nos.at[NATAL, 'locais'])} internações no próprio município", fontsize=32,
            fontweight="bold", va="center")
    ax.text(0.42 * ax.r, 0.4, "= atributo do nó: não entra no grau,\n   na densidade nem nos caminhos", fontsize=28,
            color=TINTA_2, va="center")
    salvar(fig, "03_grafo/09_atributo_e_nao_laco.png")

    total = {}
    for G in (U, ELETIVA):
        for o, v, d in G.edges(data=True):
            total[(o, v)] = total.get((o, v), 0) + d["weight"]
    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax)
    fluxos(ax, [(o, v, w) for (o, v), w in total.items() if w >= 30], cor="#7a59c9", alpha=0.45)
    titulo(fig, "URGÊNCIA + ELETIVA", "a rede agregada")
    salvar(fig, "03_grafo/10_camadas_juntas.png")

    fig = nova_fig()
    for k, (G, cor, nome_c) in enumerate([(U, AZUL, "URGÊNCIA"), (ELETIVA, LARANJA, "ELETIVA")]):
        ax = eixo_mapa(fig, (0.01 + 0.49 * k, 0.06, 0.48, 0.56))
        municipios(ax, lw=0.4)
        fluxos(ax, [(o, v, d["weight"]) for o, v, d in G.edges(data=True) if d["weight"] >= 30], cor=cor,
               alpha=0.5, lw_max=3)
        fig.text(0.25 + 0.49 * k, 0.62, nome_c, fontsize=48, fontweight="bold", color=cor, ha="center")
    salvar(fig, "03_grafo/11_camadas_separadas.png")

    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax)
    fluxos(ax, [(o, v, d["weight"]) for o, v, d in ELETIVA.edges(data=True) if v == ALMINO], cor=LARANJA, alpha=0.6)
    ponto(ax, ALMINO, LARANJA, tam=700, halo=True)
    rotulo(ax, ALMINO, "Almino Afonso", dx=0.1, dy=0.08, tam=36, caixa=True)
    x0 = 0.775
    for i, (txt, tam, cor, neg) in enumerate([
            (f"{milhar(ELETIVA.in_degree(ALMINO, weight='weight'))}", 64, LARANJA, True), ("internações eletivas", 26, TINTA, False),
            (f"{U.in_degree(ALMINO, weight='weight')} de urgência", 34, TINTA, True), ("1 hospital", 26, TINTA_2, False),
            ("43% catarata (CID H25)", 26, TINTA_2, False), ("mutirão, não polo", 30, TINTA, True),
            ("de urgência", 30, TINTA, True)]):
        fig.text(x0, 0.6 - 0.065 * i, txt, fontsize=tam, color=cor, fontweight="bold" if neg else "normal", va="center")
    salvar(fig, "03_grafo/12_almino_afonso.png")
    multicamada_3d()


def base_3d(fig, rect=(-0.06, -0.12, 0.88, 1.2), altura=1.2, zoom=1.35):
    ax = fig.add_axes(rect, projection="3d")
    ax.set_facecolor(FUNDO)
    ax.set_axis_off()
    ax.set_box_aspect((LON[1] - LON[0], (LAT[1] - LAT[0]) * ASPECTO, altura), zoom=zoom)
    ax.set_proj_type("persp", focal_length=0.6)
    ax.computed_zorder = False  # desenha na ordem em que os elementos entram: plano, linhas, pontos, textos
    return ax


def plano_municipios(ax, z, cor=CINZA_CLARO, borda=None, alpha=1.0, cores=None):
    cores = cores or {}
    polys = [np.column_stack([anel, np.full(len(anel), z)]) for _, anel in POLIGONOS]
    faces = [cores.get(c, cor) for c, _ in POLIGONOS]
    ax.add_collection3d(Poly3DCollection(polys, facecolors=faces, edgecolors=borda or faces, linewidths=0.4,
                                         alpha=alpha))


def girar(fig, ax, caminho_png, caminho_mp4, elev, azim, segundos=8):
    """Salva o quadro final em PNG e a rotação da câmera em MP4."""
    n_q = segundos * FPS

    def atualizar(i):
        t = suave(i / (n_q - 1))
        ax.view_init(elev=elev[0] + (elev[1] - elev[0]) * t, azim=azim[0] + (azim[1] - azim[0]) * t)
    atualizar(n_q - 1)
    salvar_png = SAIDA / caminho_png
    fig.canvas.draw()
    fig.savefig(salvar_png, facecolor=FUNDO)
    print("  ", salvar_png.relative_to(SAIDA))
    gravar(fig, atualizar, n_q, caminho_mp4)


def multicamada_3d():
    fig = nova_fig()
    ax = base_3d(fig, rect=(-0.02, -0.04, 0.82, 1.08), altura=2.4, zoom=1.1)
    for z, G, cor, nome_c in [(0, ELETIVA, LARANJA, "ELETIVA"), (1, U, AZUL, "URGÊNCIA")]:
        plano_municipios(ax, z, cor="#efeee9" if z == 0 else "#e8eef8", alpha=0.95)
        segs = [np.column_stack([curva(POS[o], POS[v])[::4], np.full(10, z)])
                for o, v, d in G.edges(data=True) if d["weight"] >= 30]
        ax.add_collection3d(Line3DCollection(segs, colors=cor, linewidths=1.2, alpha=0.6))
        forca = pd.Series(dict(G.in_degree(weight="weight")))
        forca = forca[forca > 0]
        ax.scatter([POS[c][0] for c in forca.index], [POS[c][1] for c in forca.index], [z] * len(forca),
                   s=8 + 0.03 * forca.values, c=cor, depthshade=False, edgecolors="white", linewidths=0.5)
        ax.text(LON[0] + 0.3, LAT[1], z + 0.15, nome_c, fontsize=34, fontweight="bold", color=cor, zorder=20,
                bbox=dict(boxstyle="round,pad=0.2", fc=FUNDO, ec="none"))
    for c, cor, z_rot in [(NATAL, AZUL_ESCURO, 1.08), (MOSSORO, LARANJA, 1.08), (ALMINO, LARANJA, 0.08)]:
        ax.plot([POS[c][0]] * 2, [POS[c][1]] * 2, [0, 1], color=cor, lw=2, ls="--", alpha=0.8)
        ax.text(POS[c][0], POS[c][1], z_rot, nos.at[c, "label"], fontsize=20, fontweight="bold", zorder=20,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.85))
    ax.set_xlim(*LON)
    ax.set_ylim(*LAT)
    ax.set_zlim(0, 1.15)
    girar(fig, ax, "03_grafo/13_multicamada_3d.png", "03_grafo/14_multicamada_3d.mp4", elev=(14, 22), azim=(-80, -50))


def mapa_bacias(fig, destaque=None, camadas_bfs=None):
    """Mapa das bacias. `destaque`: polos visíveis; `camadas_bfs`: {polo: camada máxima visível}."""
    ax = eixo_mapa(fig)
    cor = {}
    for c in nos.index:
        g = nos.at[c, "grupo_bacia"]
        polo = cod[g] if g in COR_BACIA else None
        visivel = polo is not None and (destaque is None or polo in destaque) and \
            (camadas_bfs is None or (polo in camadas_bfs and nos.at[c, "camada_bfs"] <= camadas_bfs[polo]))
        cor[c] = COR_BACIA[g] if visivel else CINZA_CLARO
    municipios(ax, cor, alpha=0.9)
    for c in nos.index:
        g = nos.at[c, "grupo_bacia"]
        if cor[c] != CINZA_CLARO and c != cod[g]:
            ax.add_patch(FancyArrowPatch(POS[c], POS[cod[g]], arrowstyle="-|>", mutation_scale=10, lw=1,
                                         color="white", alpha=0.85, zorder=4, shrinkA=2, shrinkB=6))
    return ax


def cena_04():
    print("04 · quem são os polos")
    fig = nova_fig()
    for k, (t, s, cor, larg, nota) in enumerate([
            ("IN-DEGREE", "quantas origens?", AZUL, [2] * 8, "muitas origens"),
            ("IN-STRENGTH", "quantas internações?", LARANJA, [1, 2, 3, 14], "o volume que chega")]):
        x = CX - 0.19 + 0.38 * k
        fig.text(x, 0.6, t, fontsize=58, fontweight="bold", color=cor, ha="center")
        fig.text(x, 0.53, s, fontsize=34, ha="center")
        estrela(fig.add_axes((x - 0.14, 0.08, 0.28, 0.42)), len(larg), cor, CINZA, larg)
        fig.text(x, 0.05, nota, fontsize=24, color=TINTA_2, ha="center")
    salvar(fig, "04_polos/01_in_degree_vs_in_strength.png")

    fig = nova_fig()
    ax = mapa_bacias(fig)
    for g in COR_BACIA:
        ponto(ax, cod[g], TINTA, tam=120)
        rotulo(ax, cod[g], g, tam=26, caixa=True)
    salvar(fig, "04_polos/02_bacias.png")
    for k, (polo, cor_txt, arq) in enumerate([(NATAL, AZUL, "natal"), (MOSSORO, LARANJA, "mossoro")], 3):
        fig = nova_fig()
        ax = mapa_bacias(fig, destaque={polo})
        ponto(ax, polo, TINTA, tam=200)
        if polo == NATAL:
            rotulo(ax, polo, nos.at[polo, "label"], dx=-0.06, tam=34, caixa=True, ha="right")
        else:
            rotulo(ax, polo, nos.at[polo, "label"], tam=34, caixa=True)
        fig.text(0.775, 0.55, milhar(nos.at[polo, "forca_entrada"]), fontsize=72, fontweight="bold", color=cor_txt,
                 va="center")
        fig.text(0.775, 0.47, "internações recebidas\nde outros municípios", fontsize=24, color=TINTA_2, va="center")
        salvar(fig, f"04_polos/0{k}_bacias_{arq}.png")

    top5 = nos["forca_entrada"].nlargest(5)
    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax)
    fluxos(ax, [e for e in FORTES if e[1] in top5.index], cor=AZUL, alpha=0.45)
    for c in top5.index:
        ponto(ax, c, AZUL_ESCURO, tam=80 + 0.02 * top5[c], halo=True)
        if c == SJM:
            rotulo(ax, c, nos.at[c, "label"], dx=0, dy=-0.2, tam=22, caixa=True, ha="center")
        else:
            rotulo(ax, c, nos.at[c, "label"], tam=22, caixa=True)
    fig.text(0.775, 0.55, f"{top5.sum() / nos['forca_entrada'].sum():.0%}", fontsize=96, fontweight="bold",
             color=AZUL_ESCURO, va="center")
    fig.text(0.775, 0.45, "dos fluxos vão para\nos 5 maiores polos", fontsize=26, color=TINTA_2, va="center")
    salvar(fig, "04_polos/05_top5_85.png")

    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax)
    fluxos(ax, [e for e in FORTES if e[1] in top5.index], cor=AZUL, alpha=0.5, lw_max=LW_PESO, peso_ref=PESO_MAX,
           linear=True)
    for c in top5.index:
        ponto(ax, c, AZUL_ESCURO, tam=80 + 0.02 * top5[c], halo=True)
        if c == SJM:
            rotulo(ax, c, nos.at[c, "label"], dx=0, dy=-0.2, tam=22, caixa=True, ha="center")
        else:
            rotulo(ax, c, nos.at[c, "label"], tam=22, caixa=True)
    fig.text(0.775, 0.6, f"{top5.sum() / nos['forca_entrada'].sum():.0%}", fontsize=96, fontweight="bold",
             color=AZUL_ESCURO, va="center")
    fig.text(0.775, 0.5, "dos fluxos vão para\nos 5 maiores polos", fontsize=26, color=TINTA_2, va="center")
    lupa_natal(fig, ax, [e for e in FORTES if e[1] in top5.index], rect=(0.775, 0.03, 0.2, 0.37))
    legenda_peso(fig, PESO_MAX)
    salvar(fig, "04_polos/05_top5_85_peso.png")

    # os 5 polos das bacias (com Caicó), que é o conjunto usado no resto do vídeo
    cinco = nos.loc[POLOS, "forca_entrada"]
    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax)
    fluxos(ax, [e for e in FORTES if e[1] in cinco.index], cor=AZUL, alpha=0.5, lw_max=LW_PESO, peso_ref=PESO_MAX,
           linear=True)
    for c in cinco.index:
        ponto(ax, c, AZUL_ESCURO, tam=80 + 0.02 * cinco[c], halo=True)
        rotulo(ax, c, nos.at[c, "label"], tam=22, caixa=True)
    fig.text(0.775, 0.6, f"{cinco.sum() / nos['forca_entrada'].sum():.0%}", fontsize=96, fontweight="bold",
             color=AZUL_ESCURO, va="center")
    fig.text(0.775, 0.5, "dos fluxos vão para\nos 5 polos das bacias", fontsize=26, color=TINTA_2, va="center")
    lupa_natal(fig, ax, [e for e in FORTES if e[1] in cinco.index], rect=(0.775, 0.03, 0.2, 0.37))
    legenda_peso(fig, PESO_MAX)
    salvar(fig, "04_polos/05_cinco_polos_peso.png")
    skyline_3d()

    passos = [({NATAL: 0}, "Natal · camada 0", "07_bfs_natal_camada0"),
              ({NATAL: 1}, "Natal · camada 1", "08_bfs_natal_camada1"),
              ({NATAL: 1, MOSSORO: 0}, "Mossoró · camada 0", "09_bfs_mossoro_camada0"),
              ({NATAL: 1, MOSSORO: 1}, "Mossoró · camada 1", "10_bfs_mossoro_camada1"),
              ({cod[g]: 2 for g in COR_BACIA}, "as 5 bacias", "11_bfs_todas")]
    for camadas, legenda, arq in passos:
        fig = nova_fig()
        ax = mapa_bacias(fig, destaque=set(camadas), camadas_bfs=camadas)
        for polo in camadas:
            ponto(ax, polo, TINTA, tam=160, halo=camadas[polo] == 0)
            rotulo(ax, polo, nos.at[polo, "label"], tam=26, caixa=True)
        titulo(fig, "BFS a partir do polo", legenda, tam=44)
        salvar(fig, f"04_polos/{arq}.png")


def skyline_3d():
    receb = nos[nos["forca_entrada"] > 0]
    fig = nova_fig()
    ax = base_3d(fig)
    plano_municipios(ax, 0, cor="#f4f3ef")
    escala = 1.0 / receb["forca_entrada"].max()
    lado = 0.09
    for c in receb.sort_values("forca_entrada").index:
        x, y = POS[c]
        ax.bar3d(x - lado / 2, y - lado / 2, 0, lado, lado, receb.at[c, "forca_entrada"] * escala, color=COR_GRUPO[c],
                 shade=True, alpha=0.95)
    for c in POLOS:
        ax.text(POS[c][0], POS[c][1], receb.at[c, "forca_entrada"] * escala + 0.06,
                f"{nos.at[c, 'label']}\n{milhar(receb.at[c, 'forca_entrada'])}", fontsize=18, fontweight="bold",
                ha="center")
    ax.set_xlim(*LON)
    ax.set_ylim(*LAT)
    ax.set_zlim(0, 1.15)
    girar(fig, ax, "04_polos/06_skyline_3d.png", "04_polos/06_skyline_3d.mp4", elev=(16, 30), azim=(-105, -65))


def cena_05():
    print("05 · muitos destinos não significam independência")
    saidas = sorted([(v, d["weight"]) for v, d in U[PARNAMIRIM].items()], key=lambda vw: -vw[1])
    total = sum(w for _, w in saidas)
    for k, proporcional in enumerate([False, True], 1):
        fig = nova_fig()
        ax = fig.add_axes((0.08, 0.03, 0.6, 0.82))
        ax.set_xlim(-1.35, 1.35)
        ax.set_ylim(-1.2, 1.2)
        ax.set_aspect("equal")
        ax.axis("off")
        for i, (v, w) in enumerate(saidas):
            ang = np.pi / 2 - 2 * np.pi * i / len(saidas)
            p = np.array([np.cos(ang), np.sin(ang)])
            frac = w / total
            lw = 1.5 + 30 * frac if proporcional else 3
            cor = AZUL if v == NATAL or not proporcional else CINZA
            ax.add_patch(FancyArrowPatch((0, 0), p * 0.92, arrowstyle="-|>", mutation_scale=18 + lw, lw=lw, color=cor,
                                         shrinkA=40, alpha=0.9))
            ax.add_patch(Circle(p, 0.11 if v == NATAL else 0.07, fc=AZUL if v == NATAL else CINZA, ec="none"))
            destaque = proporcional and v == NATAL
            ha = "left" if p[0] > 0.3 else ("right" if p[0] < -0.3 else "center")
            va = "bottom" if p[1] > 0.3 else ("top" if p[1] < -0.3 else "center")
            ax.text(*(p * 1.16), f"{nos.at[v, 'label']} · {frac:.0%}" if destaque else nos.at[v, "label"],
                    fontsize=30 if destaque else 15, fontweight="bold" if v == NATAL else "normal", ha=ha,
                    va=va, color=TINTA if (v == NATAL or not proporcional) else TINTA_2)
        ax.add_patch(Circle((0, 0), 0.24, fc=AZUL_ESCURO, ec="none", zorder=5))
        ax.text(0, 0, "Parnamirim", fontsize=17, fontweight="bold", color="white", ha="center", va="center", zorder=6)
        fig.text(0.70, 0.55, "PARNAMIRIM", fontsize=44, fontweight="bold", va="center")
        fig.text(0.70, 0.47, f"{len(saidas)} destinos" if not proporcional else
                 f"{saidas[0][1] / total:.0%} do que sai\nvai para Natal", fontsize=34, color=TINTA_2, va="center")
        salvar(fig, f"05_dependencia/0{k}_parnamirim_{'97_natal' if proporcional else '15_destinos'}.png")
    cartao("05_dependencia/03_muitos_destinos.png", [("MUITOS DESTINOS", 84, TINTA, True), ("≠", 84, VERMELHO, True),
                                                     ("INDEPENDÊNCIA", 84, TINTA, True)])
    dep = nos["dependencia"]
    escala = ["#f1f0ec", "#cde2fb", "#86b6ef", "#2a78d6", "#104281"]
    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax, {c: escala[min(4, int(dep[c] * 5))] for c in nos.index})
    fig.text(0.775, 0.56, f"{(dep > 0.5).sum()}", fontsize=96, fontweight="bold", color=AZUL_ESCURO, va="center")
    fig.text(0.775, 0.45, "municípios mandam\nmais da metade das\nurgências externas\npara um único polo",
             fontsize=22, color=TINTA_2, va="center")
    fig.text(0.775, 0.32, f"{(dep > 0.7).sum()} mandam mais de 70%", fontsize=24, fontweight="bold", va="center")
    for i, (lab, c) in enumerate(zip(["0–20%", "20–40%", "40–60%", "60–80%", "80–100%"], escala)):
        fig.patches.append(Rectangle((0.775 + 0.04 * i, 0.16), 0.036, 0.03, transform=fig.transFigure, fc=c, ec="none"))
        fig.text(0.793 + 0.04 * i, 0.14, lab, fontsize=12, ha="center", color=TINTA_2)
    fig.text(0.775, 0.215, "dependência do destino principal", fontsize=16, color=TINTA_2)
    salvar(fig, "05_dependencia/04_mapa_dependencia.png")


def cena_06():
    print("06 · remoção de polos")
    opcoes = [("NATAL", AZUL), ("MOSSORÓ", LARANJA), ("CAICÓ", AMARELO)]
    polo_da_opcao = {"NATAL": NATAL, "MOSSORÓ": MOSSORO, "CAICÓ": CAICO}
    pare_e_pense("06_remocao/01_quiz.png", "Se um polo parar,\nqual causa maior fragmentação?", opcoes)
    fig = nova_fig()
    fig.text(CX, 0.66, "Com rotas estabelecidas (≥ 10 internações/ano):", fontsize=34, color=TINTA_2, ha="center")
    for i, (op, cor) in enumerate(opcoes):
        x = CX + (i - 1) * 0.23
        certo = op == "MOSSORÓ"
        caixa(fig, x, 0.42, 0.2, 0.15, cor if certo else CINZA, fundo=cor if certo else "white", alpha=1 if certo else 0.5)
        fig.text(x, 0.42, op, fontsize=40, fontweight="bold", ha="center", va="center", color="white" if certo else CINZA)
        n_iso = len(isolados_sem(polo_da_opcao[op], 10))
        fig.text(x, 0.28, f"{n_iso} isolado{'s' if n_iso != 1 else ''}", fontsize=30, fontweight="bold",
                 color=VERMELHO if certo else TINTA_2, ha="center")
    salvar(fig, "06_remocao/02_quiz_resposta.png")

    R = rotas(U, 10)
    isolados = isolados_sem(MOSSORO, 10)
    arestas_R = [(o, v, d["weight"]) for o, v, d in R.edges(data=True)]
    outras = [e for e in arestas_R if MOSSORO not in e[:2]]
    da_mossoro = [e for e in arestas_R if MOSSORO in e[:2]]
    desloc = {"Tibau": (0.06, 0.03), "Governador Dix-Sept Rosado": (0.07, -0.16), "Paraú": (-0.07, 0.0),
              "Triunfo Potiguar": (0.07, -0.1), "Janduís": (0.07, -0.1)}

    def mapa_remocao(fig, t):
        ax = eixo_mapa(fig)
        sumir, acender = suave(t / 0.5), suave((t - 0.5) / 0.4)
        municipios(ax, {c: "#f6c3be" for c in isolados} if acender > 0 else {})
        fluxos(ax, outras, cor=AZUL, alpha=0.35, lw_max=3, peso_ref=7000)
        if sumir < 1:
            fluxos(ax, da_mossoro, cor=LARANJA, alpha=0.75 * (1 - sumir), lw_max=3, peso_ref=7000)
        ponto(ax, MOSSORO, LARANJA, tam=600, halo=sumir < 0.5, alpha=1 - 0.85 * sumir)
        if sumir > 0.6:
            x_isolado(ax, MOSSORO, alpha=sumir)
        if acender > 0:
            for c in isolados:
                ponto(ax, c, VERMELHO, tam=260, halo=True, alpha=acender)
        if acender > 0.95:
            for c in isolados:
                dx, dy = desloc.get(nos.at[c, "label"], (0.06, 0.04))
                rotulo(ax, c, nos.at[c, "label"], dx=dx, dy=dy, tam=22, caixa=True, ha="right" if dx < 0 else "left")
        titulo(fig, "Rotas estabelecidas (≥ 10 internações/ano)", "sem Mossoró" if sumir > 0.5 else "com Mossoró")
        if acender > 0.95:
            fig.text(0.775, 0.55, f"{len(isolados)}", fontsize=110, fontweight="bold", color=VERMELHO, va="center")
            fig.text(0.775, 0.44, "municípios ficam\nisolados", fontsize=30, fontweight="bold", va="center")

    for k, t, arq in [(3, 0, "com"), (4, 1, "sem")]:
        fig = nova_fig()
        mapa_remocao(fig, t)
        salvar(fig, f"06_remocao/0{k}_rotas_{arq}_mossoro.png")
    fig = nova_fig()
    n_q = int(5 * FPS)

    def atualizar(i):
        fig.clear()
        fig.patch.set_facecolor(FUNDO)
        mapa_remocao(fig, min(1, i / (n_q - FPS)))
    gravar(fig, atualizar, n_q, "06_remocao/05_remocao_mossoro.mp4")

    cartao("06_remocao/06_lista_isolados.png", [("Sem Mossoró, ficam sem rota estabelecida:", 36, TINTA_2, False)] +
           [(nos.at[c, "label"], 50, VERMELHO, True) for c in isolados])
    fig = nova_fig()
    for k, (a, b, cor) in enumerate([("CENTRALIDADE", "HIPÓTESE", AZUL), ("REMOÇÃO", "TESTE DE DANO", VERMELHO)]):
        x = CX - 0.19 + 0.38 * k
        fig.text(x, 0.62, a, fontsize=56, fontweight="bold", color=cor, ha="center", va="center")
        fig.text(x, 0.47, "↓", fontsize=80, color=TINTA_2, ha="center", va="center")
        fig.text(x, 0.32, b, fontsize=50, fontweight="bold", ha="center", va="center")
    salvar(fig, "06_remocao/07_hipotese_e_teste.png")


def cena_07():
    print("07 · centralidade não é criticidade")
    pare_e_pense("07_centralidade/00_pare_e_pense.png", "Natal é o 1º em todas as centralidades.\nEntão é o mais crítico?")
    medidas = [("DEGREE /\nSTRENGTH", "concentração", AZUL), ("CLOSENESS", "proximidade\nestrutural", VERDE),
               ("BETWEENNESS", "intermediação", LARANJA)]
    fig = nova_fig()
    for i, (t, s, cor) in enumerate(medidas):
        x = CX + (i - 1) * 0.245
        caixa(fig, x, 0.47, 0.225, 0.42, cor)
        fig.text(x, 0.56, t, fontsize=38, fontweight="bold", color=cor, ha="center", va="center", linespacing=1.1)
        fig.text(x, 0.38, s, fontsize=30, ha="center", va="center")
    salvar(fig, "07_centralidade/01_tres_medidas.png")
    cartao("07_centralidade/02_central_nao_e_critico.png", [("CENTRAL ≠ CRÍTICO", 104, TINTA, True)])

    medidas_rank = ["grau_entrada", "forca_entrada", "closeness", "betweenness"]
    posicao = nos[medidas_rank].rank(ascending=False, method="min")
    fig = nova_fig()
    for i, (polo, cor) in enumerate([(NATAL, AZUL), (MOSSORO, LARANJA)]):
        x = CX - 0.19 + 0.38 * i
        fig.text(x, 0.64, nos.at[polo, "label"].upper(), fontsize=60, fontweight="bold", color=cor, ha="center")
        fig.text(x, 0.56, "posição nas centralidades", fontsize=24, color=TINTA_2, ha="center")
        fig.text(x, 0.5, " · ".join(f"{int(posicao.at[polo, m])}º" for m in medidas_rank), fontsize=38,
                 fontweight="bold", ha="center")
        fig.text(x, 0.4, "municípios isolados sem ele", fontsize=24, color=TINTA_2, ha="center")
        fig.text(x, 0.28, str(len(isolados_sem(polo, 10))), fontsize=110, fontweight="bold",
                 color=VERMELHO if polo == MOSSORO else TINTA, ha="center", va="center")
        fig.text(x, 0.17, f"({len(isolados_sem(polo, 20))} com rotas ≥ 20)", fontsize=22, color=TINTA_2, ha="center")
    fig.text(CX, 0.07, "ordem: grau de entrada · força de entrada · closeness · betweenness", fontsize=18,
             color=TINTA_2, ha="center")
    salvar(fig, "07_centralidade/03_natal_vs_mossoro.png")

    cm, jc, touros = (cod[n] for n in ("Ceará-Mirim", "João Câmara", "Touros"))
    bet = nos["betweenness"]
    top = bet.nlargest(10)
    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax)
    for c in top.index:
        cor = LARANJA if c == cm else (AZUL_ESCURO if c in (NATAL, MOSSORO) else CINZA)
        ponto(ax, c, cor, tam=60 + 9000 * top[c], halo=c == cm)
    for c in (jc, touros):
        ponto(ax, c, TINTA_2, tam=120)
    rotulo(ax, cm, "Ceará-Mirim", dx=0, dy=0.07, tam=26, caixa=True, ha="center")
    rotulo(ax, jc, "João Câmara", dx=-0.06, dy=0.03, tam=20, cor=TINTA_2, caixa=True, ha="right")
    rotulo(ax, touros, "Touros", dx=0.05, dy=0.03, tam=20, cor=TINTA_2, caixa=True)
    rotulo(ax, NATAL, "Natal", dx=0.07, dy=-0.12, tam=22, caixa=True)
    rotulo(ax, MOSSORO, "Mossoró", dx=0.07, dy=0.05, tam=22, caixa=True)
    titulo(fig, "Betweenness: quem faz ponte", "círculo = intermediação (10 maiores)")
    rank = int(bet.rank(ascending=False, method="min")[cm])
    fig.text(0.775, 0.55, f"{rank}º", fontsize=96, fontweight="bold", color=LARANJA, va="center")
    fig.text(0.775, 0.45, "Ceará-Mirim em\nbetweenness", fontsize=26, color=TINTA_2, va="center")
    salvar(fig, "07_centralidade/04_betweenness_ceara_mirim.png")


def cena_08():
    print("08 · k-core")
    pare_e_pense("08_kcore/00_pare_e_pense.png", "Quem tem mais ligações\nestá no núcleo da rede?")
    cartao("08_kcore/01_hub_nao_e_nucleo.png", [("HUB ≠ NÚCLEO", 116, TINTA, True)])
    k_max = int(nos["core"].max())
    aneis = [(lambda c: c == k_max, 0.33, "#cde2fb", f"CORE {k_max}"),
             (lambda c: c == k_max - 1, 0.66, "#e8eef8", f"SHELL {k_max - 1}"),
             (lambda c: c <= k_max - 2, 1.0, "#f4f3ef", f"SHELL ≤ {k_max - 2}")]
    for k, destaque in enumerate([False, True], 2):
        rng = np.random.default_rng(42)
        fig = nova_fig()
        ax = fig.add_axes((0.06, 0.04, 0.6, 0.86))
        ax.set_xlim(-1.1, 1.1)
        ax.set_ylim(-1.1, 1.1)
        ax.set_aspect("equal")
        ax.axis("off")
        for _, r, cor, _ in reversed(aneis):
            ax.add_patch(Circle((0, 0), r, fc=cor, ec="white", lw=4))
        for (teste, r, _, lab), r_in in zip(aneis, [0, 0.33, 0.66]):
            ax.text(0, r - 0.08, lab, fontsize=24, fontweight="bold", color=TINTA_2, ha="center")
            for c in nos.index[nos["core"].map(teste)]:
                ang, rr = rng.uniform(0, 2 * np.pi), rng.uniform(r_in + 0.06, r - 0.13)
                fixos = {NATAL: (np.deg2rad(200), 0.17), MOSSORO: (np.deg2rad(340), 0.17),
                         PDF: (np.deg2rad(155), 0.55), CAICO: (np.deg2rad(300), 0.5)}
                ang, rr = fixos.get(c, (ang, rr))  # posições fixas para os rótulos não se cruzarem
                foco = c in (CAICO, PDF, NATAL, MOSSORO)
                cor_p = AZUL if lab.startswith("CORE") else CINZA
                if destaque and c in (CAICO, PDF):
                    cor_p = VERMELHO
                ax.scatter(rr * np.cos(ang), rr * np.sin(ang), s=260 if foco else 40, color=cor_p, edgecolors="white",
                           linewidths=1.5, zorder=5)
                if foco:
                    ax.text(rr * np.cos(ang), rr * np.sin(ang) + 0.05, nos.at[c, "label"], fontsize=22,
                            fontweight="bold", ha="center", zorder=6,
                            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.85))
        if destaque:
            fig.text(0.68, 0.45, "Caicó e Pau dos Ferros", fontsize=30, fontweight="bold", color=VERMELHO, va="center")
            fig.text(0.68, 0.37, "cerca de 50 ligações cada,\nmas no shell 9", fontsize=26, va="center")
        salvar(fig, f"08_kcore/0{k}_aneis{'_caico_pdf' if destaque else ''}.png")
    kcore_3d(k_max)


def kcore_3d(k_max):
    fig = nova_fig()
    ax = base_3d(fig)
    plano_municipios(ax, 0, cor="#f4f3ef")
    z = nos["core"] / k_max
    nucleo = nos.index[nos["core"] == k_max]
    segs = [[(*POS[a], z[a]), (*POS[b], z[b])] for a, b in U.to_undirected().subgraph(nucleo).edges()]
    ax.add_collection3d(Line3DCollection(segs, colors=AZUL, linewidths=0.8, alpha=0.35))
    for c in nos.index:
        cor = AZUL if c in nucleo else (VERMELHO if c in (CAICO, PDF) else CINZA)
        ax.plot([POS[c][0]] * 2, [POS[c][1]] * 2, [0, z[c]], color=cor, lw=1, alpha=0.5)
        ax.scatter(*POS[c], z[c], s=70 if (c in nucleo or c in (CAICO, PDF)) else 18, color=cor, depthshade=False,
                   edgecolors="white", linewidths=0.6)
    for c in [NATAL, MOSSORO, CAICO, PDF]:
        ax.text(POS[c][0], POS[c][1], z[c] + 0.07, f"{nos.at[c, 'label']} ({int(nos.at[c, 'core'])})", fontsize=18,
                fontweight="bold", ha="center", color=VERMELHO if c in (CAICO, PDF) else TINTA, zorder=20,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.85))
    fig.text(0.03, 0.06, f"altura = core number · núcleo (k = {k_max}) em azul", fontsize=22, color=TINTA_2)
    ax.set_xlim(*LON)
    ax.set_ylim(*LAT)
    ax.set_zlim(0, 1.2)
    girar(fig, ax, "08_kcore/04_kcore_3d.png", "08_kcore/04_kcore_3d.mp4", elev=(14, 26), azim=(-105, -70))


def cena_09():
    print("09 · rede bipartida")
    mun = {"A": (0.18, 0.78), "B": (0.18, 0.5), "C": (0.18, 0.22)}
    hosp = {"H1": (0.68, 0.66), "H2": (0.68, 0.34)}
    for k, projetar in enumerate([False, True], 1):
        fig = nova_fig()
        ax = eixo_esquema(fig, (0.03, 0.06, 0.7, 0.76))
        r = ax.r
        for a, b in [("A", "H1"), ("B", "H1"), ("B", "H2"), ("C", "H2")]:
            ax.plot([mun[a][0] * r, hosp[b][0] * r], [mun[a][1], hosp[b][1]], zorder=1, lw=6 if a == "B" else 4,
                    color=AZUL if (projetar and a == "B") else CINZA)
        for n, (x, y) in mun.items():
            ax.add_patch(Circle((x * r, y), 0.07, fc=AZUL, ec="white", lw=4, zorder=3))
            ax.text(x * r, y, n, fontsize=32, fontweight="bold", color="white", ha="center", va="center", zorder=4)
        for n, (x, y) in hosp.items():
            ax.add_patch(FancyBboxPatch((x * r - 0.07, y - 0.07), 0.14, 0.14, boxstyle="round,pad=0.01", fc=LARANJA,
                                        ec="white", lw=4, zorder=3))
            ax.text(x * r, y, n, fontsize=28, fontweight="bold", color="white", ha="center", va="center", zorder=4)
        ax.text(0.18 * r, 0.95, "MUNICÍPIOS", fontsize=32, fontweight="bold", color=AZUL, ha="center")
        ax.text(0.68 * r, 0.95, "HOSPITAIS", fontsize=32, fontweight="bold", color=LARANJA, ha="center")
        if projetar:
            ax.annotate("", xy=(0.78 * r, 0.36), xytext=(0.78 * r, 0.64),
                        arrowprops=dict(arrowstyle="-", color=LARANJA, lw=8, connectionstyle="arc3,rad=-0.6"))
            fig.text(0.645, 0.47, "H1 — H2:\natendem B", fontsize=28, fontweight="bold", color=LARANJA, va="center")
        salvar(fig, f"09_bipartida/0{k}_{'projecao' if projetar else 'bipartida'}.png")

    fig = nova_fig()
    estrela(fig.add_axes((0.03, 0.12, 0.36, 0.72)), 40, AZUL_ESCURO, LARANJA, [1.5] * 40, raio_p=0.035, seta=False)
    for i, (txt, tam, cor, neg) in enumerate([("Walfredo Gurgel", 50, TINTA, True),
                                              ("107 municípios atendidos", 34, TINTA, False),
                                              ("ligado a 83 de 116 hospitais", 34, LARANJA, True),
                                              ("na projeção por contagem:", 26, TINTA_2, False),
                                              ("o artefato do clique", 32, VERMELHO, True)]):
        fig.text(0.42, 0.62 - 0.08 * i, txt, fontsize=tam, color=cor, fontweight="bold" if neg else "normal")
    salvar(fig, "09_bipartida/03_walfredo_artefato_clique.png")

    for k, aviso in enumerate([False, True], 4):
        fig = nova_fig()
        fig.text(CX, 0.64, r"$J(A,B) = \dfrac{|N(A)\ \cap\ N(B)|}{|N(A)\ \cup\ N(B)|}$", fontsize=52, ha="center",
                 va="center")
        ax = fig.add_axes((CX - 0.17, 0.27, 0.34, 0.27))
        ax.set_xlim(-1.6, 1.6)
        ax.set_ylim(-1.1, 1.1)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.add_patch(Circle((-0.45, 0), 0.9, fc=AZUL, alpha=0.35, ec=AZUL, lw=4))
        ax.add_patch(Circle((0.45, 0), 0.9, fc=LARANJA, alpha=0.35, ec=LARANJA, lw=4))
        ax.text(-0.85, 0, "N(A)", fontsize=26, fontweight="bold", ha="center", va="center")
        ax.text(0.85, 0, "N(B)", fontsize=26, fontweight="bold", ha="center", va="center")
        ax.text(0, 0, "∩", fontsize=38, fontweight="bold", ha="center", va="center")
        fig.text(CX, 0.19, "J alto = cobertura territorial semelhante", fontsize=36, fontweight="bold", ha="center")
        if aviso:
            fig.text(CX, 0.1, "NÃO significa mesmo serviço clínico", fontsize=36, fontweight="bold", color=VERMELHO,
                     ha="center")
        salvar(fig, f"09_bipartida/0{k}_jaccard{'_aviso' if aviso else ''}.png")

    fig = nova_fig()
    for i, (cid, cor, n_h, linhas) in enumerate([
            ("MOSSORÓ", LARANJA, 4, ["4 hospitais com", "Jaccard 0,71–0,72", "redundância territorial"]),
            ("PAU DOS FERROS", VERDE, 1, ["1 hospital regional", "10 municípios dependem dele",
                                          "sem ele, 5 iriam a outra", "cidade (+1,4 h)"])]):
        x = CX - 0.19 + 0.38 * i
        fig.text(x, 0.64, cid, fontsize=48, fontweight="bold", color=cor, ha="center")
        ax = fig.add_axes((x - 0.1, 0.36, 0.2, 0.26))
        ax.set_xlim(-1.3, 1.3)
        ax.set_ylim(-1.3, 1.3)
        ax.set_aspect("equal")
        ax.axis("off")
        pts = [(0, 0)] if n_h == 1 else [(np.cos(a), np.sin(a)) for a in np.linspace(np.pi / 4, 2.25 * np.pi, n_h,
                                                                                    endpoint=False)]
        for a in range(n_h):
            for b in range(a + 1, n_h):
                ax.plot(*zip(pts[a], pts[b]), color=cor, lw=6, alpha=0.6)
        for p in pts:
            ax.add_patch(FancyBboxPatch((p[0] - 0.25, p[1] - 0.25), 0.5, 0.5, boxstyle="round,pad=0.02", fc=cor,
                                        ec="white", lw=3))
        for j, l in enumerate(linhas):
            fig.text(x, 0.29 - 0.055 * j, l, fontsize=26, ha="center", fontweight="bold" if j == 0 else "normal")
    salvar(fig, "09_bipartida/06_mossoro_vs_pau_dos_ferros.png")


def cena_10():
    print("10 · resposta")
    cards = [("NATAL", "CARGA", AZUL, ["60 mil internações", "afetadas", "Macaíba teria de", "atender 7,8×"]),
             ("MOSSORÓ", "CONECTIVIDADE", LARANJA, ["24 mil internações", "5 municípios sem rota", "1,4 h → 2,8 h"]),
             ("CAICÓ /\nPAU DOS FERROS", "DISTÂNCIA\nREGIONAL", VERDE, ["0,8 h → 3,1 h (Caicó)",
                                                                        "0,7 h → 2,7 h (P. Ferros)"])]
    for k, (n, papel, cor, linhas) in enumerate(cards, 1):
        cartao(f"10_resposta/0{k}_card_{['natal', 'mossoro', 'interior'][k - 1]}.png",
               [(l, 72, cor, True) for l in n.split("\n")] + [(l, 50, TINTA, True) for l in papel.split("\n")] +
               [("", 16, TINTA, False)] + [(l, 34, TINTA_2, False) for l in linhas])
    fig = nova_fig()
    for i, (n, papel, cor, linhas) in enumerate(cards):
        x = CX + (i - 1) * 0.245
        caixa(fig, x, 0.45, 0.225, 0.62, cor, lw=6)
        fig.text(x, 0.67, n, fontsize=30 if "\n" in n else 40, fontweight="bold", color=cor, ha="center", va="center",
                 linespacing=1.1)
        fig.text(x, 0.56, papel, fontsize=26, fontweight="bold", ha="center", va="center", linespacing=1.1)
        for j, l in enumerate(linhas):
            fig.text(x, 0.44 - 0.055 * j, l, fontsize=20, color=TINTA_2, ha="center", va="center")
    salvar(fig, "10_resposta/04_tres_cards.png")
    cartao("10_resposta/05_frase_central.png", [("Mossoró é crítico", 64, LARANJA, True),
                                                ("para a conectividade.", 64, LARANJA, True), ("", 20, TINTA, False),
                                                ("Natal é crítico", 64, AZUL, True), ("para a carga.", 64, AZUL, True)])
    fig = nova_fig()
    ax = eixo_mapa(fig)
    municipios(ax)
    for c, cor, txt, dx, dy in [(NATAL, AZUL, "Natal · carga", 0.08, 0.05),
                                (MOSSORO, LARANJA, "Mossoró · conectividade", 0.08, 0.05),
                                (CAICO, VERDE, "Caicó · distância", 0.08, -0.15),
                                (PDF, VERDE, "Pau dos Ferros · distância", 0.08, 0.05)]:
        ponto(ax, c, cor, tam=500, halo=True)
        rotulo(ax, c, txt, dx=dx, dy=dy, tam=28, caixa=True)
    salvar(fig, "10_resposta/06_mapa_tres_criticidades.png")


def cena_11():
    print("11 · sensibilidade")
    cartao("11_sensibilidade/01_limiares.png", [("LIMIAR", 64, TINTA_2, True), ("1  →  5  →  10  →  20", 88, TINTA, True),
                                                ("internações por ano para contar como rota", 30, TINTA_2, False)])
    fig = nova_fig()
    colunas = [0.4, 0.5, 0.6, 0.7]
    for x, lim in zip(colunas, [1, 5, 10, 20]):
        fig.text(x, 0.62, f"limiar {lim}", fontsize=26, color=TINTA_2, ha="center")
    for i, (rot, vals, cor, veredito) in enumerate([
            ("Pau dos Ferros → Mossoró", ["✓", "✓", "✓", "✓"], VERDE, "estável"),
            ("Santa Cruz → ?", ["Natal", "Natal", "Currais\nNovos", "S. Paulo\ndo Potengi"], VERMELHO, "instável")]):
        y = 0.48 - 0.24 * i
        fig.text(0.03, y, rot, fontsize=32, fontweight="bold", va="center")
        fig.text(0.03, y - 0.07, veredito, fontsize=26, color=cor, fontweight="bold", va="center")
        for x, v in zip(colunas, vals):
            fig.text(x, y, v, fontsize=58 if v == "✓" else 20, fontweight="bold", color=cor, ha="center", va="center",
                     fontfamily="DejaVu Sans" if v == "✓" else None)
    salvar(fig, "11_sensibilidade/02_pdf_mossoro_vs_santa_cruz.png")
    cartao("11_sensibilidade/03_robusto.png", [("Resultado bonito não basta.", 56, TINTA_2, False),
                                               ("Ele precisa ser robusto.", 64, TINTA, True)])


def cena_12():
    print("12 · limitações")
    itens = ["ATENDIMENTO ≠ DEMANDA", "AIH ≠ PACIENTE", "CARGA ≠ CAPACIDADE", "COBERTURA ≠ SERVIÇO CLÍNICO",
             "ARESTA ≠ ROTA"]
    for k in range(1, 6):
        cartao(f"12_limitacoes/0{k}_limitacoes_{k}.png",
               [(t, 52, TINTA if i == k - 1 else CINZA, True) for i, t in enumerate(itens[:k])])


def cena_13():
    print("13 · encerramento")
    cartao("13_encerramento/01_titulo_final.png",
           [("Quem sustenta a urgência no RN?", 56, TINTA, True),
            ("Fluxo de internações do SUS entre municípios", 30, TINTA_2, False), ("", 30, TINTA, False),
            ("Luiz Felipe de Souza Silva", 40, AZUL, True),
            ("UFRN · Algoritmos e Estruturas de Dados II", 26, TINTA_2, False)], fundo_mapa=True)
    cartao("13_encerramento/02_proximos_passos.png",
           [("PRÓXIMOS PASSOS", 60, AZUL, True), ("", 16, TINTA, False), ("rede temporal (mês a mês)", 40, TINTA, False),
            ("capacidade e leitos (CNES)", 40, TINTA, False), ("especialidades", 40, TINTA, False),
            ("falhas simultâneas", 40, TINTA, False)])


CENAS = {f"{i:02d}": f for i, f in enumerate([cena_00, cena_01, cena_02, cena_03, cena_04, cena_05, cena_06,
                                              cena_07, cena_08, cena_09, cena_10, cena_11, cena_12, cena_13])}
CENAS["3d"] = lambda: (multicamada_3d(), skyline_3d(), kcore_3d(int(nos["core"].max())))  # só as rotações 3D

if __name__ == "__main__":
    for c in [a for a in sys.argv[1:] if not a.startswith("--")] or list(CENAS):
        CENAS[c]()
