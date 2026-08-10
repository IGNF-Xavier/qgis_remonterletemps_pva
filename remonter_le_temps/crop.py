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


def detect_content_box(path, margin_pct=1.5, dark_ratio=0.55):
    """
    Retourne (x0, y0, x1, y1) en pixels pleine resolution : la zone utile.

    margin_pct : marge de securite rognee sur chaque bord, en % de la
                 dimension correspondante (les reperes de fond de chambre
                 debordent souvent legerement sur l'image).
    """
    small, (w, h), (ow, oh) = _overview_array(path)

    # seuil : mi-chemin entre le noir du cadre et la moyenne du cliche
    thr = max(20, int(0.45 * float(np.median(small))))
    bright = small > thr

    row_frac = bright.mean(axis=1)
    col_frac = bright.mean(axis=0)

    y0s, y1s = _longest_run(row_frac > dark_ratio)
    x0s, x1s = _longest_run(col_frac > dark_ratio)

    # securite : si la detection est absurde, on retombe sur toute l'image
    if (y1s - y0s) < 0.3 * oh or (x1s - x0s) < 0.3 * ow:
        x0s, y0s, x1s, y1s = 0, 0, ow - 1, oh - 1

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
