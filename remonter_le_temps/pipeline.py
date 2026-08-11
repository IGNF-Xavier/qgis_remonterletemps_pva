# -*- coding: utf-8 -*-
"""Chaine de traitement d'un cliche : telechargement -> decoupe -> calage."""

import json
import math
import os

import numpy as np
from osgeo import gdal

from . import crop as crop_mod
from . import deps
from . import georef
from . import ign_api

LEVEL_FOOTPRINT = 0
LEVEL_ORTHO = 1
LEVEL_DEM = 2

CROP_NONE = 0
CROP_AUTO = 1
CROP_FIXED = 2


class Options(object):
    def __init__(self):
        self.outdir = ""
        self.out_crs = "EPSG:2154"
        self.level = LEVEL_ORTHO
        self.crop_mode = CROP_AUTO
        self.crop_margin = 1.5
        self.fixed_margin = 8.0
        self.resolution = 0.0        # 0 = automatique
        self.keep_raw = True
        self.overwrite = False
        self.rotation_steps = 0      # calage sur emprise uniquement
        self.build_ovr = True
        self.write_json = True


def _log(feedback, msg):
    if feedback is not None:
        feedback(msg)


def _auto_resolution(ground_corners, img_size):
    (x0, y0), (x1, y1) = ground_corners[0], ground_corners[1]
    width_m = math.hypot(x1 - x0, y1 - y0)
    r = width_m / max(1, img_size[0])
    for step in (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0):
        if r <= step:
            return step
    return round(r, 2)


def _ground_quad_from_P(P, img_size, z):
    """Emprise sol du cliche pour une altitude moyenne z (inversion d'homographie)."""
    H = np.array([
        [P[0, 0], P[0, 1], P[0, 2] * z + P[0, 3]],
        [P[1, 0], P[1, 1], P[1, 2] * z + P[1, 3]],
        [P[2, 0], P[2, 1], P[2, 2] * z + P[2, 3]],
    ])
    Hi = np.linalg.inv(H)
    w, h = img_size
    pts = []
    for (u, v) in ((0, 0), (w, 0), (w, h), (0, h)):
        q = Hi.dot(np.array([u, v, 1.0]))
        pts.append((q[0] / q[2], q[1] / q[2]))
    return pts


def process_cliche(props, ring3857, opt, feedback=None, is_canceled=None):
    """
    props    : dict des attributs du cliche (dataset_identifier, image_identifier...)
    ring3857 : liste de (x, y) en EPSG:3857 = emprise IGN du cliche
    Retourne le chemin du GeoTIFF cale.
    """
    ds_id = props["dataset_identifier"]
    img_id = props["image_identifier"]

    raw_dir = os.path.join(opt.outdir, "01_scans_bruts")
    crop_dir = os.path.join(opt.outdir, "02_cliches_decoupes")
    out_dir = os.path.join(opt.outdir, "03_cliches_cales")
    tmp_dir = os.path.join(opt.outdir, "_tmp")
    for d in (raw_dir, crop_dir, out_dir, tmp_dir):
        os.makedirs(d, exist_ok=True)

    dest = os.path.join(out_dir, "%s_cale.tif" % img_id)
    if os.path.exists(dest) and not opt.overwrite:
        _log(feedback, u"  deja traite, ignore")
        return dest

    # ---- 1. telechargement --------------------------------------------
    _log(feedback, u"  telechargement du scan...")
    raw = ign_api.download_cliche(ds_id, img_id, raw_dir, overwrite=opt.overwrite)

    if opt.write_json:
        meta = dict(props)
        meta["_emprise_wfs_epsg3857"] = [list(p) for p in ring3857]
        meta["_scan"] = os.path.basename(raw)
        meta["_source"] = ign_api.DOWNLOAD_URL.format(
            ds=ds_id, img=img_id, ext=os.path.splitext(raw)[1])
        with open(os.path.join(raw_dir, "%s.json" % img_id), "w",
                  encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2, default=str)

    if is_canceled and is_canceled():
        return None

    # ---- 2. decoupe du cadre ------------------------------------------
    if opt.crop_mode == CROP_NONE:
        cropped = raw
    else:
        if opt.crop_mode == CROP_AUTO:
            box = crop_mod.detect_content_box(raw, margin_pct=opt.crop_margin)
        else:
            box = crop_mod.fixed_box(raw, margin_pct=opt.fixed_margin)
        cropped = os.path.join(crop_dir, "%s_crop.tif" % img_id)
        _log(feedback, u"  decoupe du cadre : %s" % (box,))
        crop_mod.crop_to_file(raw, cropped, box)
    if is_canceled and is_canceled():
        return None

    img_size = georef.raster_size(cropped)

    # ---- 3. emprise IGN -> CRS de sortie -------------------------------
    rect = georef.min_area_rect(ring3857)
    rect = georef.order_corners_cw(rect)
    ground = georef.transform_points(rect, "EPSG:3857", opt.out_crs)
    res = opt.resolution or _auto_resolution(ground, img_size)

    # ---- 4. niveau 1 : calage sur l'emprise ---------------------------
    gcps = georef.gcps_from_footprint(img_size, ground, opt.rotation_steps)
    _log(feedback, u"  calage sur l'emprise IGN (%.2f m/px)" % res)
    georef.warp_with_gcps(cropped, dest, gcps, opt.out_crs, opt.out_crs,
                          res=res, order=1)
    if opt.level == LEVEL_FOOTPRINT:
        if opt.build_ovr:
            georef.build_overviews(dest)
        return dest

    # ---- 5. niveau 2 : recalage sur l'ortho actuelle -------------------
    use_cv2 = deps.have_cv2()
    if not use_cv2:
        _log(feedback, u"  OpenCV absent : appariement de secours (numpy).")

    xs = [p[0] for p in ground]
    ys = [p[1] for p in ground]
    mx = 0.20 * (max(xs) - min(xs))
    my = 0.20 * (max(ys) - min(ys))
    bbox = (min(xs) - mx, min(ys) - my, max(xs) + mx, max(ys) + my)
    ortho_res = max(res, 1.0)
    ow = min(4000, max(500, int((bbox[2] - bbox[0]) / ortho_res)))
    oh = min(4000, max(500, int((bbox[3] - bbox[1]) / ortho_res)))

    ortho = os.path.join(tmp_dir, "%s_ortho_ref.tif" % img_id)
    _log(feedback, u"  extraction de l'ortho de reference...")
    try:
        ign_api.fetch_ortho(bbox, opt.out_crs, ow, oh, ortho)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! ortho de reference indisponible (%s)" % exc)
        if opt.build_ovr:
            georef.build_overviews(dest)
        return dest

    if is_canceled and is_canceled():
        return None

    if use_cv2:
        _log(feedback, u"  recherche de points d'appui (AKAZE + RANSAC)...")
        match = georef.match_on_ortho(cropped, ortho)
    else:
        _log(feedback, u"  recherche de points d'appui (correlation de phase)...")
        match = georef.match_on_ortho_fft(dest, ortho, gcps)

    if match is None:
        _log(feedback, u"  ! aucun appariement fiable, calage sur emprise conserve.")
        if opt.build_ovr:
            georef.build_overviews(dest)
        return dest

    img_xy, gnd_xy, inliers, steps = match
    if use_cv2:
        _log(feedback, u"  %d points d'appui (%d inliers, rotation %d degres)"
             % (len(img_xy), inliers, steps * 90))
    else:
        _log(feedback, u"  %d points d'appui (tuiles correlees)" % len(img_xy))

    order = 2 if len(img_xy) >= 12 else 1
    gcps2 = [gdal.GCP(g[0], g[1], 0.0, p[0], p[1])
             for p, g in zip(img_xy, gnd_xy)]
    georef.warp_with_gcps(cropped, dest, gcps2, opt.out_crs, opt.out_crs,
                          res=res, order=order)

    if opt.level == LEVEL_ORTHO:
        if opt.build_ovr:
            georef.build_overviews(dest)
        return dest

    # ---- 6. niveau 3 : orthorectification sur MNT ---------------------
    if len(img_xy) < 6:
        _log(feedback, u"  ! moins de 6 points d'appui : ortho MNT impossible.")
        return dest

    _log(feedback, u"  interrogation du RGE ALTI...")
    lonlat = georef.transform_points(gnd_xy, opt.out_crs, "EPSG:4326")
    try:
        zs = ign_api.elevations(lonlat)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! RGE ALTI indisponible (%s)" % exc)
        return dest

    xyz, uv = [], []
    for (X, Y), z, p in zip(gnd_xy, zs, img_xy):
        if z is None or z < -1000:
            continue
        xyz.append((X, Y, float(z)))
        uv.append(p)
    if len(xyz) < 6:
        _log(feedback, u"  ! altitudes insuffisantes, ortho MNT abandonnee.")
        return dest

    try:
        P = georef.solve_dlt(xyz, uv)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! resection spatiale impossible (%s)" % exc)
        return dest

    resid = georef.dlt_residuals(P, xyz, uv)
    _log(feedback, u"  resection : residu moyen %.1f px (max %.1f px)"
         % (resid.mean(), resid.max()))
    if resid.mean() > 60:
        _log(feedback, u"  ! resection peu fiable, on conserve le calage 2D.")
        return dest

    zmed = float(np.median([p[2] for p in xyz]))
    quad = _ground_quad_from_P(P, img_size, zmed)
    qx = [p[0] for p in quad]
    qy = [p[1] for p in quad]
    bounds = (min(qx), min(qy), max(qx), max(qy))

    dem = os.path.join(tmp_dir, "%s_mnt.tif" % img_id)
    dem_res = max(res * 4.0, 5.0)
    dw = min(3000, max(50, int((bounds[2] - bounds[0]) / dem_res)))
    dh = min(3000, max(50, int((bounds[3] - bounds[1]) / dem_res)))
    _log(feedback, u"  extraction du MNT...")
    try:
        ign_api.fetch_dem(bounds, opt.out_crs, dw, dh, dem)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! MNT indisponible (%s)" % exc)
        return dest

    _log(feedback, u"  orthorectification sur MNT...")
    ortho_dest = os.path.join(out_dir, "%s_ortho.tif" % img_id)
    try:
        georef.orthorectify(cropped, dem, P, ortho_dest, res, bounds, opt.out_crs)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! echec de l'orthorectification (%s)" % exc)
        return dest

    if opt.build_ovr:
        georef.build_overviews(ortho_dest)
    return ortho_dest
