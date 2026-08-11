# -*- coding: utf-8 -*-
"""
Decoupe du cadre des scans argentiques IGN.

Un scan PVA contient, autour de la zone photographiee :
  - un liseré noir de bord de film,
  - les reperes de fond de chambre (fiducial marks) aux coins / milieux de bords,
  - parfois un bandeau clair avec numero de mission, horloge, niveau a bulle.

detect_content_box() cherche le plus grand bloc contigu de lignes / colonnes
"claires" (le cliche lui-meme), puis applique une marge de securite pour
eliminer les reperes qui mordent sur l'image.
"""

import numpy as np
from osgeo import gdal

gdal.UseExceptions()


def _overview_array(path, target=1200):
    """Lit une version reduite en niveaux de gris (uint8)."""
    ds = gdal.Open(path)
    if ds is None:
        raise IOError("Ouverture impossible : %s" % path)
    w, h = ds.RasterXSize, ds.RasterYSize
    scale = max(1.0, max(w, h) / float(target))
    ow, oh = max(1, int(w / scale)), max(1, int(h / scale))
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray(0, 0, w, h, ow, oh).astype(np.float32)
    if ds.RasterCount >= 3:
        for b in (2, 3):
            arr += ds.GetRasterBand(b).ReadAsArray(0, 0, w, h, ow, oh).astype(np.float32)
        arr /= 3.0
    ds = None
    lo, hi = np.percentile(arr, (1, 99))
    if hi <= lo:
        hi = lo + 1.0
    arr = np.clip((arr - lo) / (hi - lo) * 255.0, 0, 255)
    return arr.astype(np.uint8), (w, h), (ow, oh)


def _longest_run(mask):
    """Indices (debut, fin inclus) du plus long bloc de True."""
    best = (0, len(mask) - 1)
    best_len = -1
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best_len:
                best_len = i - start
                best = (start, i - 1)
            start = None
    if start is not None and len(mask) - start > best_len:
        best = (start, len(mask) - 1)
    return best


def _energy_profiles(small):
    """Energie de gradient par ligne et par colonne."""
    a = small.astype(np.float32)
    gy, gx = np.gradient(a)
    energy = np.hypot(gx, gy)
    return energy.mean(axis=1), energy.mean(axis=0)


def _smooth(profile, window=9):
    if len(profile) < window:
        return profile
    kernel = np.ones(window, dtype=np.float32) / float(window)
    return np.convolve(profile, kernel, mode="same")


def _box_from_profiles(row, col, ratio):
    """Plus long bloc contigu au-dessus du seuil, sur chaque axe."""
    def run(profile):
        prof = _smooth(profile)
        hi = float(np.percentile(prof, 95))
        lo = float(np.percentile(prof, 5))
        if hi <= lo:
            return None
        thr = lo + ratio * (hi - lo)
        return _longest_run(prof > thr)

    r = run(row)
    c = run(col)
    if r is None or c is None:
        return None
    return c[0], r[0], c[1], r[1]


def detect_content_box(path, margin_pct=2.0, dark_ratio=0.55):
    """
    Retourne (x0, y0, x1, y1) en pixels pleine resolution : la zone utile.

    La zone photographiee se distingue du pourtour par sa TEXTURE, pas par sa
    luminosite : selon les campagnes, le fond de chambre apparait noir, gris
    ou clair, et un bandeau de reperes peut border le cliche. On cherche donc
    le plus long bloc de lignes et de colonnes a forte energie de gradient,
    et on ne retombe sur le critere de luminosite qu'en cas d'echec.

    margin_pct : marge de securite rognee sur chaque bord, en % de la
                 dimension correspondante.
    """
    small, (w, h), (ow, oh) = _overview_array(path)

    box = None
    rows, cols = _energy_profiles(small)
    for ratio in (0.30, 0.20, 0.45):
        cand = _box_from_profiles(rows, cols, ratio)
        if cand is None:
            continue
        x0s, y0s, x1s, y1s = cand
        if (y1s - y0s) > 0.45 * oh and (x1s - x0s) > 0.45 * ow:
            box = cand
            break

    if box is None:
        # repli : cadre nettement plus sombre que le cliche
        thr = max(20, int(0.45 * float(np.median(small))))
        bright = small > thr
        y0s, y1s = _longest_run(bright.mean(axis=1) > dark_ratio)
        x0s, x1s = _longest_run(bright.mean(axis=0) > dark_ratio)
        if (y1s - y0s) > 0.3 * oh and (x1s - x0s) > 0.3 * ow:
            box = (x0s, y0s, x1s, y1s)

    if box is None:
        box = (0, 0, ow - 1, oh - 1)

    x0s, y0s, x1s, y1s = box
    sx, sy = w / float(ow), h / float(oh)
    x0, x1 = int(round(x0s * sx)), int(round((x1s + 1) * sx))
    y0, y1 = int(round(y0s * sy)), int(round((y1s + 1) * sy))

    mx = int(round((x1 - x0) * margin_pct / 100.0))
    my = int(round((y1 - y0) * margin_pct / 100.0))
    x0, y0 = max(0, x0 + mx), max(0, y0 + my)
    x1, y1 = min(w, x1 - mx), min(h, y1 - my)

    if x1 - x0 < 50 or y1 - y0 < 50:
        return 0, 0, w, h
    return x0, y0, x1, y1


def fixed_box(path, margin_pct=8.0):
    ds = gdal.Open(path)
    w, h = ds.RasterXSize, ds.RasterYSize
    ds = None
    mx = int(round(w * margin_pct / 100.0))
    my = int(round(h * margin_pct / 100.0))
    return mx, my, w - mx, h - my


def crop_to_file(src_path, dest_path, box, compress="DEFLATE"):
    """Ecrit le cliche rogne (sans georeferencement) en GeoTIFF."""
    x0, y0, x1, y1 = box
    opts = gdal.TranslateOptions(
        srcWin=[x0, y0, x1 - x0, y1 - y0],
        format="GTiff",
        creationOptions=["COMPRESS=%s" % compress, "TILED=YES", "BIGTIFF=IF_SAFER"],
    )
    gdal.Translate(dest_path, src_path, options=opts)
    return dest_path
