# -*- coding: utf-8 -*-
"""
Calage des cliches anciens.

Trois niveaux, du plus rapide au plus juste :

  1. FOOTPRINT  - ajustement sur l'emprise IGN du tableau d'assemblage.
                  4 points de calage = similitude (rotation + echelle). Le
                  cliche est place et oriente correctement, au decalage pres
                  de l'emprise IGN elle-meme (qui est approximative).

  2. ORTHO      - recalage automatique sur l'ortho actuelle de l'IGN par mise
                  en correspondance de points (OpenCV / AKAZE + RANSAC), puis
                  transformation polynomiale ou TPS. Supprime l'essentiel de
                  l'erreur planimetrique. Determine aussi l'orientation du
                  cliche (0/90/180/270) sans metadonnee.

  3. MNT        - orthorectification vraie : resection spatiale (DLT 11
                  parametres) a partir des points d'appui du niveau 2, puis
                  reprojection de chaque pixel de sortie a travers le MNT
                  RGE ALTI. C'est ce niveau qui corrige le devers du relief.

Limite assumee : la distorsion de l'objectif n'est pas modelisee et la
resection utilise un modele projectif. Pour une chaine photogrammetrique
complete (reperes de fond de chambre, aerotriangulation, MNS), voir
l'outil officiel de l'IGN : https://github.com/IGNF/Pompei
"""

import math
import os

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()


# ==========================================================================
# geometrie de l'emprise
# ==========================================================================
def _convex_hull(pts):
    pts = sorted(set(map(tuple, pts)))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_area_rect(points):
    """Rectangle d'aire minimale englobant : renvoie 4 sommets (sens horaire)."""
    hull = _convex_hull(points)
    if len(hull) < 3:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return [(min(xs), max(ys)), (max(xs), max(ys)),
                (max(xs), min(ys)), (min(xs), min(ys))]
    best = None
    n = len(hull)
    for i in range(n):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % n]
        ang = math.atan2(y2 - y1, x2 - x1)
        ca, sa = math.cos(-ang), math.sin(-ang)
        rx = [p[0] * ca - p[1] * sa for p in hull]
        ry = [p[0] * sa + p[1] * ca for p in hull]
        area = (max(rx) - min(rx)) * (max(ry) - min(ry))
        if best is None or area < best[0]:
            best = (area, ang, min(rx), max(rx), min(ry), max(ry))
    _, ang, x0, x1, y0, y1 = best
    ca, sa = math.cos(ang), math.sin(ang)
    corners = []
    for (u, v) in ((x0, y1), (x1, y1), (x1, y0), (x0, y0)):
        corners.append((u * ca - v * sa, u * sa + v * ca))
    return corners


def order_corners_cw(corners):
    """Ordonne 4 sommets dans le sens horaire, en partant du plus 'haut-gauche'."""
    cx = sum(p[0] for p in corners) / 4.0
    cy = sum(p[1] for p in corners) / 4.0
    ordered = sorted(corners, key=lambda p: -math.atan2(p[1] - cy, p[0] - cx))
    # depart : sommet le plus proche du coin haut-gauche de la bbox
    xs = [p[0] for p in ordered]
    ys = [p[1] for p in ordered]
    ref = (min(xs), max(ys))
    k = min(range(4), key=lambda i: (ordered[i][0] - ref[0]) ** 2 +
                                    (ordered[i][1] - ref[1]) ** 2)
    return ordered[k:] + ordered[:k]


# ==========================================================================
# niveau 1 : calage sur l'emprise du tableau d'assemblage
# ==========================================================================
def gcps_from_footprint(img_size, ground_corners_cw, rotation_steps=0,
                        mirror=False):
    """
    Associe les 4 coins du cliche aux 4 sommets de l'emprise.
    rotation_steps : 0/1/2/3 -> rotation de 90 degres du cliche.
    """
    w, h = img_size
    img_corners = [(0.5, 0.5), (w - 0.5, 0.5), (w - 0.5, h - 0.5), (0.5, h - 0.5)]
    k = rotation_steps % 4
    ground = ground_corners_cw[k:] + ground_corners_cw[:k]
    if mirror:
        img_corners = [img_corners[1], img_corners[0],
                       img_corners[3], img_corners[2]]
    return [gdal.GCP(g[0], g[1], 0.0, p[0], p[1])
            for p, g in zip(img_corners, ground)]


def warp_with_gcps(src_path, dest_path, gcps, gcp_srs, out_srs,
                   res=None, order=0, tps=False, resample="cubic",
                   nodata=0, compress="DEFLATE"):
    """Applique les GCP puis reprojette en raster nord-en-haut."""
    src = gdal.Open(src_path)
    if src is None:
        raise IOError("Ouverture impossible : %s" % src_path)

    srs_in = osr.SpatialReference()
    srs_in.SetFromUserInput(gcp_srs)
    vrt = gdal.Translate("", src, format="VRT", GCPs=gcps,
                         outputSRS=srs_in.ExportToWkt())

    kwargs = dict(
        format="GTiff",
        dstSRS=out_srs,
        resampleAlg=resample,
        dstNodata=nodata,
        multithread=True,
        creationOptions=["COMPRESS=%s" % compress, "TILED=YES",
                         "BIGTIFF=IF_SAFER", "PHOTOMETRIC=MINISBLACK"],
    )
    if tps:
        kwargs["tps"] = True
    else:
        kwargs["polynomialOrder"] = order if order in (1, 2, 3) else 1
    if res:
        kwargs["xRes"] = res
        kwargs["yRes"] = res
        kwargs["targetAlignedPixels"] = True

    gdal.Warp(dest_path, vrt, options=gdal.WarpOptions(**kwargs))
    vrt = None
    src = None
    return dest_path


# ==========================================================================
# niveau 2 : recalage automatique sur l'ortho actuelle
# ==========================================================================
def cv2_available():
    try:
        import cv2  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _to_gray_u8(path, max_size=2000):
    ds = gdal.Open(path)
    w, h = ds.RasterXSize, ds.RasterYSize
    scale = max(1.0, max(w, h) / float(max_size))
    ow, oh = max(1, int(w / scale)), max(1, int(h / scale))
    arr = ds.GetRasterBand(1).ReadAsArray(0, 0, w, h, ow, oh).astype(np.float32)
    if ds.RasterCount >= 3:
        for b in (2, 3):
            arr += ds.GetRasterBand(b).ReadAsArray(0, 0, w, h, ow, oh).astype(np.float32)
        arr /= 3.0
    gt = ds.GetGeoTransform()
    ds = None
    lo, hi = np.percentile(arr, (2, 98))
    if hi <= lo:
        hi = lo + 1
    arr = np.clip((arr - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    return arr, (w, h), (ow, oh), gt


def make_detector():
    """
    Detecteur de points d'interet disponible dans la build d'OpenCV installee.

    Toutes les distributions n'exposent pas AKAZE : certaines roues allegees
    en sont depourvues. On prend le premier detecteur present, avec la norme
    de distance qui lui correspond.
    """
    import cv2

    for name, norm in (("AKAZE_create", cv2.NORM_HAMMING),
                       ("ORB_create", cv2.NORM_HAMMING),
                       ("BRISK_create", cv2.NORM_HAMMING),
                       ("SIFT_create", cv2.NORM_L2),
                       ("KAZE_create", cv2.NORM_L2)):
        factory = getattr(cv2, name, None)
        if factory is None:
            continue
        try:
            det = factory(nfeatures=8000) if name == "ORB_create" else factory()
        except Exception:  # noqa: BLE001
            try:
                det = factory()
            except Exception:  # noqa: BLE001
                continue
        return det, norm, name.replace("_create", "")
    raise RuntimeError(
        u"Aucun detecteur de points d'interet dans cette version d'OpenCV "
        u"(ni AKAZE, ni ORB, ni BRISK, ni SIFT).")


def opencv_report():
    """Description de la build d'OpenCV, pour le diagnostic."""
    try:
        import cv2
    except Exception as exc:  # noqa: BLE001
        return u"cv2 indisponible : %s" % exc
    present = [n for n in ("AKAZE_create", "ORB_create", "BRISK_create",
                           "SIFT_create", "KAZE_create")
               if hasattr(cv2, n)]
    return u"cv2 %s (%s) - detecteurs : %s" % (
        getattr(cv2, "__version__", "?"),
        os.path.dirname(getattr(cv2, "__file__", "?")),
        u", ".join(p.replace("_create", "") for p in present) or u"aucun")


def match_on_ortho(cliche_path, ortho_path, min_inliers=25, grid=5):
    """
    Cherche l'homographie cliche -> ortho de reference.
    Retourne (gcps_img_xy, gcps_ground_xy, score, rotation_steps) ou None.
    Les coordonnees image sont en pixels PLEINE RESOLUTION du cliche.
    """
    import cv2

    img, (iw, ih), (siw, sih), _ = _to_gray_u8(cliche_path)
    ref, (rw, rh), (srw, srh), gt = _to_gray_u8(ortho_path)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    ref_e = clahe.apply(ref)

    det, norm, det_name = make_detector()
    kp_r, des_r = det.detectAndCompute(ref_e, None)
    if des_r is None or len(kp_r) < 50:
        return None

    # AKAZE, ORB et consorts sont invariants a la rotation : on essaie d'abord
    # l'image telle quelle et son miroir (certains scans sont retournes cote
    # emulsion), et on ne deroule les quarts de tour qu'en cas d'echec.
    best = None
    attempts = [(False, 0), (True, 0)] + [(m, k) for k in (1, 2, 3)
                                          for m in (False, True)]
    for mirror, steps in attempts:
        if best is not None and best[0] >= 3 * min_inliers:
            break
        base = np.fliplr(img) if mirror else img
        rot = np.rot90(base, -steps).copy()
        rot_e = clahe.apply(rot)
        kp_i, des_i = det.detectAndCompute(rot_e, None)
        if des_i is None or len(kp_i) < 50:
            continue
        bf = cv2.BFMatcher(norm)
        raw = bf.knnMatch(des_i, des_r, k=2)
        good = [m for m, n in (p for p in raw if len(p) == 2)
                if m.distance < 0.75 * n.distance]
        if len(good) < min_inliers:
            continue
        src = np.float32([kp_i[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp_r[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None:
            continue
        inl = int(mask.sum())
        if inl >= min_inliers and (best is None or inl > best[0]):
            best = (inl, steps, H, rot.shape, mirror)

    if best is None:
        return None

    inl, steps, H, (rh_s, rw_s), mirror = best

    # grille reguliere dans le cliche tourne -> ortho -> terrain
    us = np.linspace(0.08, 0.92, grid) * rw_s
    vs = np.linspace(0.08, 0.92, grid) * rh_s
    pts = np.float32([[u, v] for v in vs for u in us]).reshape(-1, 1, 2)
    proj = cv2.perspectiveTransform(pts, H).reshape(-1, 2)

    img_xy, gnd_xy = [], []
    sx, sy = iw / float(siw), ih / float(sih)
    rsx, rsy = rw / float(srw), rh / float(srh)
    for (u, v), (pu, pv) in zip(pts.reshape(-1, 2), proj):
        if not (0 <= pu < srw and 0 <= pv < srh):
            continue
        # retour aux pixels du cliche non tourne (basse resolution)
        if steps == 0:
            cu, cv_ = u, v
        elif steps == 1:
            cu, cv_ = v, (rw_s - 1 - u)
        elif steps == 2:
            cu, cv_ = (rw_s - 1 - u), (rh_s - 1 - v)
        else:
            cu, cv_ = (rh_s - 1 - v), u
        if mirror:
            cu = siw - 1 - cu
        img_xy.append((cu * sx, cv_ * sy))
        X = gt[0] + (pu * rsx) * gt[1] + (pv * rsy) * gt[2]
        Y = gt[3] + (pu * rsx) * gt[4] + (pv * rsy) * gt[5]
        gnd_xy.append((X, Y))

    if len(img_xy) < 6:
        return None
    return img_xy, gnd_xy, inl, steps, det_name, mirror


# ==========================================================================
# niveau 3 : orthorectification sur MNT (resection spatiale DLT)
# ==========================================================================
def solve_dlt(ground_xyz, image_xy):
    """
    Resout la matrice projective 3x4 (11 parametres, p34=1) reliant
    (X,Y,Z) terrain aux (col,lig) image. Necessite >= 6 points.
    """
    XYZ = np.asarray(ground_xyz, dtype=np.float64)
    xy = np.asarray(image_xy, dtype=np.float64)
    if len(XYZ) < 6:
        raise ValueError("Au moins 6 points d'appui sont necessaires.")

    # normalisation (stabilite numerique)
    c = XYZ.mean(axis=0)
    s = np.maximum(XYZ.std(axis=0), 1e-6)
    Xn = (XYZ - c) / s
    ci = xy.mean(axis=0)
    si = max(float(np.abs(xy - ci).mean()), 1e-6)
    xn = (xy - ci) / si

    n = len(Xn)
    A = np.zeros((2 * n, 11))
    b = np.zeros(2 * n)
    for i in range(n):
        X, Y, Z = Xn[i]
        u, v = xn[i]
        A[2 * i] = [X, Y, Z, 1, 0, 0, 0, 0, -u * X, -u * Y, -u * Z]
        b[2 * i] = u
        A[2 * i + 1] = [0, 0, 0, 0, X, Y, Z, 1, -v * X, -v * Y, -v * Z]
        b[2 * i + 1] = v
    p, *_ = np.linalg.lstsq(A, b, rcond=None)
    P = np.append(p, 1.0).reshape(3, 4)

    # de-normalisation
    T = np.array([[1.0 / s[0], 0, 0, -c[0] / s[0]],
                  [0, 1.0 / s[1], 0, -c[1] / s[1]],
                  [0, 0, 1.0 / s[2], -c[2] / s[2]],
                  [0, 0, 0, 1.0]])
    K = np.array([[si, 0, ci[0]], [0, si, ci[1]], [0, 0, 1.0]])
    P = K.dot(P).dot(T)
    return P / P[2, 3]


def dlt_residuals(P, ground_xyz, image_xy):
    XYZ = np.asarray(ground_xyz, dtype=np.float64)
    xy = np.asarray(image_xy, dtype=np.float64)
    hom = np.hstack([XYZ, np.ones((len(XYZ), 1))])
    proj = hom.dot(P.T)
    uv = proj[:, :2] / proj[:, 2:3]
    return np.linalg.norm(uv - xy, axis=1)


def orthorectify(cliche_path, dem_path, P, dest_path, res, bounds,
                 out_srs, nodata=0, compress="DEFLATE", block=512,
                 progress=None):
    """
    Ortho vraie sur MNT : pour chaque pixel de sortie (X,Y), on lit Z dans le
    MNT puis on projette (X,Y,Z) dans le cliche par la matrice P.
    """
    src = gdal.Open(cliche_path)
    nb = src.RasterCount
    iw, ih = src.RasterXSize, src.RasterYSize
    bands = [src.GetRasterBand(i + 1).ReadAsArray() for i in range(nb)]

    xmin, ymin, xmax, ymax = bounds
    ow = int(math.ceil((xmax - xmin) / res))
    oh = int(math.ceil((ymax - ymin) / res))
    if ow <= 0 or oh <= 0:
        raise ValueError("Emprise de sortie vide.")

    # MNT reechantillonne sur la grille de sortie
    dem_ds = gdal.Warp("", dem_path, options=gdal.WarpOptions(
        format="MEM", dstSRS=out_srs, xRes=res, yRes=res,
        outputBounds=(xmin, ymin, xmin + ow * res, ymin + oh * res),
        resampleAlg="bilinear"))
    dem = dem_ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    dem_nd = dem_ds.GetRasterBand(1).GetNoDataValue()
    if dem_nd is not None:
        dem[dem == dem_nd] = np.nan
    dem[dem < -1000] = np.nan
    med = np.nanmedian(dem)
    if np.isnan(med):
        med = 0.0
    dem = np.where(np.isnan(dem), med, dem)

    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(dest_path, ow, oh, nb, src.GetRasterBand(1).DataType,
                     options=["COMPRESS=%s" % compress, "TILED=YES",
                              "BIGTIFF=IF_SAFER"])
    srs = osr.SpatialReference()
    srs.SetFromUserInput(out_srs)
    out.SetProjection(srs.ExportToWkt())
    out.SetGeoTransform((xmin, res, 0.0, ymin + oh * res, 0.0, -res))

    xs = xmin + (np.arange(ow) + 0.5) * res
    for y0 in range(0, oh, block):
        y1 = min(oh, y0 + block)
        ys = (ymin + oh * res) - (np.arange(y0, y1) + 0.5) * res
        X = np.tile(xs, (y1 - y0, 1))
        Y = np.repeat(ys[:, None], ow, axis=1)
        Z = dem[y0:y1, :]

        den = P[2, 0] * X + P[2, 1] * Y + P[2, 2] * Z + P[2, 3]
        den = np.where(np.abs(den) < 1e-12, 1e-12, den)
        u = (P[0, 0] * X + P[0, 1] * Y + P[0, 2] * Z + P[0, 3]) / den
        v = (P[1, 0] * X + P[1, 1] * Y + P[1, 2] * Z + P[1, 3]) / den

        valid = (u >= 0) & (u <= iw - 1) & (v >= 0) & (v <= ih - 1)
        u0 = np.clip(np.floor(u), 0, iw - 2).astype(np.int32)
        v0 = np.clip(np.floor(v), 0, ih - 2).astype(np.int32)
        du = np.clip(u - u0, 0, 1)
        dv = np.clip(v - v0, 0, 1)

        for bi, band in enumerate(bands):
            b = band.astype(np.float32)
            val = (b[v0, u0] * (1 - du) * (1 - dv) +
                   b[v0, u0 + 1] * du * (1 - dv) +
                   b[v0 + 1, u0] * (1 - du) * dv +
                   b[v0 + 1, u0 + 1] * du * dv)
            val = np.where(valid, val, nodata)
            out.GetRasterBand(bi + 1).WriteArray(val.astype(band.dtype), 0, y0)

        if progress:
            progress(100.0 * y1 / oh)

    for i in range(nb):
        out.GetRasterBand(i + 1).SetNoDataValue(nodata)
    out.FlushCache()
    out = None
    dem_ds = None
    src = None
    return dest_path


# ==========================================================================
# utilitaires
# ==========================================================================
def transform_points(points, src_srs, dst_srs):
    a = osr.SpatialReference()
    a.SetFromUserInput(src_srs)
    b = osr.SpatialReference()
    b.SetFromUserInput(dst_srs)
    try:
        a.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        b.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:
        pass
    ct = osr.CoordinateTransformation(a, b)
    return [tuple(ct.TransformPoint(float(p[0]), float(p[1]))[:2]) for p in points]


def raster_size(path):
    ds = gdal.Open(path)
    s = (ds.RasterXSize, ds.RasterYSize)
    ds = None
    return s


def build_overviews(path, levels=(2, 4, 8, 16)):
    try:
        ds = gdal.Open(path, gdal.GA_Update)
        ds.BuildOverviews("AVERAGE", list(levels))
        ds = None
    except Exception:  # noqa: BLE001
        pass


# ==========================================================================
# niveau 2 bis : appariement de secours SANS OpenCV (numpy pur)
# ==========================================================================
def affine_from_gcps(gcps):
    """
    Ajuste l'affine pixel -> terrain a partir de GCP gdal.
    Retourne (A, Ainv), matrices 2x3 : [X,Y] = A . [col,lig,1].
    """
    P = np.array([[g.GCPPixel, g.GCPLine, 1.0] for g in gcps])
    G = np.array([[g.GCPX, g.GCPY] for g in gcps])
    A, *_ = np.linalg.lstsq(P, G, rcond=None)
    A = A.T                                   # 2x3
    M = np.vstack([A, [0.0, 0.0, 1.0]])
    Mi = np.linalg.inv(M)
    return A, Mi[:2, :]


def _prep_tile(arr):
    """Gradient normalise + fenetre de Hann : rend la correlation robuste
    aux differences radiometriques entre 1950 et aujourd'hui."""
    a = arr.astype(np.float32)
    gy, gx = np.gradient(a)
    g = np.hypot(gx, gy)
    g -= g.mean()
    s = g.std()
    if s < 1e-6:
        return None
    g /= s
    h = np.hanning(g.shape[0])[:, None] * np.hanning(g.shape[1])[None, :]
    return g * h


def _phase_correlate(ref, mov):
    """Decalage (dx, dy) tel que ref(x) ~ mov(x - d), + rapport pic/bruit."""
    A = np.fft.rfft2(ref)
    B = np.fft.rfft2(mov)
    R = A * np.conj(B)
    R /= np.maximum(np.abs(R), 1e-9)
    r = np.fft.irfft2(R, s=ref.shape)
    idx = int(np.argmax(r))
    dy, dx = np.unravel_index(idx, r.shape)
    peak = float(r.flat[idx])
    snr = peak / (float(r.std()) + 1e-9)
    h, w = r.shape
    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w
    return float(dx), float(dy), snr


def match_on_ortho_fft(cale_path, ortho_path, gcps_level1,
                       tiles=6, min_tiles=6, min_snr=6.0, max_shift_frac=0.35):
    """
    Appariement de secours : le cliche deja cale au niveau 1 et l'ortho sont
    ramenes sur la meme grille, decoupes en tuiles, et chaque tuile est recalee
    par correlation de phase. Les decalages coherents fournissent des points
    d'appui. Moins robuste qu'AKAZE (translation seule par tuile, pas de
    rotation) mais ne demande aucune dependance externe.

    Retourne (img_xy, gnd_xy, n_tuiles, 0) ou None.
    """
    src = gdal.Open(cale_path)
    if src is None:
        return None
    gt = src.GetGeoTransform()
    w, h = src.RasterXSize, src.RasterYSize
    img = src.GetRasterBand(1).ReadAsArray().astype(np.float32)
    nd = src.GetRasterBand(1).GetNoDataValue()
    proj = src.GetProjection()
    src = None

    ref_ds = gdal.Warp("", ortho_path, options=gdal.WarpOptions(
        format="MEM", dstSRS=proj, xRes=abs(gt[1]), yRes=abs(gt[5]),
        outputBounds=(gt[0], gt[3] + h * gt[5], gt[0] + w * gt[1], gt[3]),
        resampleAlg="bilinear"))
    if ref_ds is None:
        return None
    ref = ref_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    if ref_ds.RasterCount >= 3:
        for b in (2, 3):
            ref += ref_ds.GetRasterBand(b).ReadAsArray().astype(np.float32)
        ref /= 3.0
    ref_ds = None
    if ref.shape != img.shape:
        return None

    ts = int(min(img.shape) / float(tiles))
    ts = max(96, min(768, ts))
    if ts * 2 > min(img.shape):
        return None
    max_shift = max_shift_frac * ts

    _, Ainv = affine_from_gcps(gcps_level1)

    cand = []
    ys = np.linspace(0, img.shape[0] - ts, tiles).astype(int)
    xs = np.linspace(0, img.shape[1] - ts, tiles).astype(int)
    for y0 in ys:
        for x0 in xs:
            sub_i = img[y0:y0 + ts, x0:x0 + ts]
            sub_r = ref[y0:y0 + ts, x0:x0 + ts]
            if nd is not None and (sub_i == nd).mean() > 0.05:
                continue
            if sub_i.std() < 3 or sub_r.std() < 3:
                continue
            pi = _prep_tile(sub_i)
            pr = _prep_tile(sub_r)
            if pi is None or pr is None:
                continue
            dx, dy, snr = _phase_correlate(pr, pi)
            if snr < min_snr or abs(dx) > max_shift or abs(dy) > max_shift:
                continue
            cand.append((x0 + ts / 2.0, y0 + ts / 2.0, dx, dy, snr))

    if len(cand) < min_tiles:
        return None

    # rejet des decalages incoherents (MAD)
    dxs = np.array([c[2] for c in cand])
    dys = np.array([c[3] for c in cand])
    mx, my = np.median(dxs), np.median(dys)
    mad = np.median(np.hypot(dxs - mx, dys - my)) + 1.0
    keep = [c for c in cand
            if math.hypot(c[2] - mx, c[3] - my) < max(6.0, 3.5 * mad)]
    if len(keep) < min_tiles:
        return None

    img_xy, gnd_xy = [], []
    for (cx, cy, dx, dy, _snr) in keep:
        X = gt[0] + cx * gt[1] + cy * gt[2]
        Y = gt[3] + cx * gt[4] + cy * gt[5]
        px = Ainv.dot(np.array([X, Y, 1.0]))
        Xc = X + dx * gt[1] + dy * gt[2]
        Yc = Y + dx * gt[4] + dy * gt[5]
        img_xy.append((float(px[0]), float(px[1])))
        gnd_xy.append((float(Xc), float(Yc)))

    return img_xy, gnd_xy, len(keep), 0


# ==========================================================================
# exploitation de l'attribut "orientation du nord" du tableau d'assemblage
# ==========================================================================
def north_angle_of_fit(img_size, ground_corners_cw, k):
    """
    Angle du nord dans le repere image, en degres, pour l'assignation des
    coins decalee de k quarts de tour.

    Convention retenue, celle affichee par remonterletemps.ign.fr : angle
    mesure depuis le haut du cliche, positif dans le sens horaire.
    """
    gcps = gcps_from_footprint(img_size, ground_corners_cw, k)
    A, _ = affine_from_gcps(gcps)          # [X,Y] = A . [col,lig,1]
    M = A[:, :2]
    try:
        Mi = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        return None
    # direction du nord terrain (0,1) exprimee en (col, lig)
    d = Mi.dot(np.array([0.0, 1.0]))
    # lig croit vers le bas : le "haut" du cliche est -lig
    return (math.degrees(math.atan2(d[0], -d[1])) + 180.0) % 360.0 - 180.0


def rotation_from_orientation(img_size, ground_corners_cw, orientation_deg,
                              invert=False):
    """
    Choisit le quart de tour k (0-3) dont l'angle du nord colle a l'attribut
    d'orientation fourni par l'IGN. Retourne (k, ecart_en_degres).
    """
    try:
        target = float(orientation_deg)
    except (TypeError, ValueError):
        return 0, None
    if invert:
        target = -target
    target = (target + 180.0) % 360.0 - 180.0

    best, best_err = 0, None
    for k in range(4):
        angle = north_angle_of_fit(img_size, ground_corners_cw, k)
        if angle is None:
            continue
        err = abs((angle - target + 180.0) % 360.0 - 180.0)
        if best_err is None or err < best_err:
            best, best_err = k, err
    return best, best_err


# ==========================================================================
# calage metrique : pas de numerisation + echelle + centre + orientation
# ==========================================================================
STANDARD_PLATES_MM = ((240, 180), (180, 180), (230, 230), (240, 240),
                      (300, 300), (140, 140))


def scan_pitch_ds(ds):
    """
    Pas de numerisation du scan, en metres sur le film, deduit des tags TIFF.

    Retourne (pitch_m, dpi) ou (None, None). La subtilite est l'unite :
    ResolutionUnit vaut 2 pour pouce et 3 pour centimetre, et il faut donc
    convertir avant d'en tirer des microns.
    """
    if ds is None:
        return None, None
    xres = ds.GetMetadataItem("TIFFTAG_XRESOLUTION")
    unit = ds.GetMetadataItem("TIFFTAG_RESOLUTIONUNIT")
    if not xres:
        return None, None
    try:
        value = float(xres)
    except (TypeError, ValueError):
        return None, None
    code = 2
    if unit:
        digits = "".join(c for c in str(unit).strip() if c.isdigit())
        if digits:
            code = int(digits[0])
    if code == 3:            # pixels par centimetre
        dpi = value * 2.54
    elif code == 2:          # pixels par pouce
        dpi = value
    else:
        return None, None
    if dpi <= 0:
        return None, None
    return 0.0254 / dpi, dpi


def scan_pitch(path):
    """Idem scan_pitch_ds, a partir d'un chemin (ou d'une URL /vsicurl/)."""
    try:
        return scan_pitch_ds(gdal.Open(path))
    except Exception:  # noqa: BLE001
        return None, None


def plate_format_mm(pitch_m, img_size):
    """Dimensions du cliche sur le film, en mm, et format normalise le plus proche."""
    w_mm = img_size[0] * pitch_m * 1000.0
    h_mm = img_size[1] * pitch_m * 1000.0
    best, best_err = None, None
    for std in STANDARD_PLATES_MM:
        for a, b in ((std[0], std[1]), (std[1], std[0])):
            err = abs(w_mm - a) / a + abs(h_mm - b) / b
            if best_err is None or err < best_err:
                best, best_err = (a, b), err
    return (w_mm, h_mm), best, best_err


def north_vectors(north_deg):
    """
    Vecteurs terrain des axes image, pour un nord situe a `north_deg` degres
    du haut du cliche, positif dans le sens horaire (convention RLT).
    """
    a = math.radians(north_deg)
    right = (math.cos(a), math.sin(a))     # axe des colonnes
    up = (-math.sin(a), math.cos(a))       # axe des lignes, vers le haut
    return right, up


def gcps_from_metric(img_size, centre_ground, gsd, north_deg,
                     crop_offset=(0, 0), full_size=None, mirror=False):
    """
    Points de calage deduits de la geometrie de la prise de vue plutot que de
    l'emprise approximative du tableau d'assemblage.

    img_size     : taille du cliche decoupe (px)
    centre_ground: centre du cliche dans le CRS de sortie
    gsd          : taille pixel au sol (m), = pas de numerisation x echelle
    north_deg    : orientation du nord dans le repere image
    crop_offset  : origine de la decoupe dans le scan brut
    full_size    : taille du scan brut, pour situer le point principal
    """
    right, up = north_vectors(north_deg)
    full = full_size or img_size
    # point principal suppose au centre du scan brut, exprime en pixels decoupes
    cu = full[0] / 2.0 - crop_offset[0]
    cv = full[1] / 2.0 - crop_offset[1]

    w, h = img_size
    gcps = []
    for (u, v) in ((0.5, 0.5), (w - 0.5, 0.5), (w - 0.5, h - 0.5), (0.5, h - 0.5),
                   (w / 2.0, h / 2.0)):
        du = ((w - 1.0 - u) - cu) * gsd if mirror else (u - cu) * gsd
        dv = (cv - v) * gsd          # lignes vers le bas
        X = centre_ground[0] + du * right[0] + dv * up[0]
        Y = centre_ground[1] + du * right[1] + dv * up[1]
        gcps.append(gdal.GCP(X, Y, 0.0, u, v))
    return gcps


def resolve_north_angle(orientation_deg, img_size, ground_corners_cw):
    """
    Angle du nord a utiliser, en levant l'ambiguite de convention.

    L'emprise du tableau d'assemblage donne une orientation grossiere mais sans
    ambiguite ; l'attribut IGN donne la valeur precise mais dans une convention
    que rien ne documente. On confronte les deux : si l'attribut colle mieux
    une fois son signe inverse, c'est que la convention est l'inverse, et on le
    signale au lieu de le deviner.
    """
    coarse = [north_angle_of_fit(img_size, ground_corners_cw, k) for k in range(4)]
    coarse = [c for c in coarse if c is not None]
    if not coarse:
        return None, u"emprise inexploitable"
    try:
        attr = float(orientation_deg)
    except (TypeError, ValueError):
        return None, u"pas d'orientation fournie"

    def closest(value):
        return min(abs((c - value + 180.0) % 360.0 - 180.0) for c in coarse)

    err_direct = closest(attr)
    err_inverse = closest(-attr)
    if min(err_direct, err_inverse) > 50.0:
        return None, (u"orientation IGN (%.0f deg) incoherente avec l'emprise "
                      u"(ecart %.0f deg)" % (attr, min(err_direct, err_inverse)))
    if err_inverse < err_direct:
        return -attr, (u"orientation IGN %.0f deg, convention inversee "
                       u"(ecart %.0f deg)" % (attr, err_inverse))
    return attr, u"orientation IGN %.0f deg (ecart %.0f deg)" % (attr, err_direct)
