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
        self.use_orientation = True   # exploite l'attribut d'orientation IGN
        self.invert_orientation = False
        self.use_metric = True        # calage par pas de scan + echelle + centre
        self.extra_rotation_deg = 0.0  # correction manuelle, s'ajoute a l'orientation
        self.mirror = False            # scan numerise cote emulsion


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


ORIENT_KEYS = ("orientation", "orientation_nord", "azimut", "azimuth",
               "cap", "angle", "rotation")


def _orientation_of(props):
    for key in ORIENT_KEYS:
        val = props.get(key)
        if val not in (None, ""):
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


SCALE_KEYS = ("echelle", "echelle_cliche", "scale", "denominateur_echelle")
CENTRE_KEYS = (("x", "y"), ("centre_x", "centre_y"), ("lon", "lat"))


def _scale_of(props):
    """Denominateur d'echelle du cliche (11361 pour 1/11361)."""
    for key in SCALE_KEYS:
        val = props.get(key)
        if val in (None, ""):
            continue
        try:
            if isinstance(val, str) and "/" in val:
                num, den = val.split("/", 1)
                num = float(num.strip().replace("1", "1", 1))
                return float(den.strip()) / (num or 1.0)
            num = float(val)
        except (TypeError, ValueError):
            continue
        if num <= 0:
            continue
        return 1.0 / num if num < 1.0 else num
    return None


def _centre_of(props, ring3857, out_crs):
    """Centre du cliche dans le CRS de sortie."""
    for kx, ky in CENTRE_KEYS:
        vx, vy = props.get(kx), props.get(ky)
        if vx in (None, "") or vy in (None, ""):
            continue
        try:
            x, y = float(vx), float(vy)
        except (TypeError, ValueError):
            continue
        src = "EPSG:4326" if abs(x) <= 180 and abs(y) <= 90 else "EPSG:3857"
        return georef.transform_points([(x, y)], src, out_crs)[0]
    xs = [p[0] for p in ring3857[:-1]] or [p[0] for p in ring3857]
    ys = [p[1] for p in ring3857[:-1]] or [p[1] for p in ring3857]
    centre = (sum(xs) / len(xs), sum(ys) / len(ys))
    return georef.transform_points([centre], "EPSG:3857", out_crs)[0]


def _gsd_from_footprint(ground, full_size):
    """
    Taille pixel au sol impliquee par l'emprise du tableau d'assemblage.

    Le calcul porte sur la taille du SCAN COMPLET, pas sur celle du cliche
    decoupe : le modele metrique exprime les pixels par rapport au centre du
    scan, et l'emprise IGN decrit le cliche entier. Utiliser la taille apres
    decoupe surestimerait la taille pixel dans le rapport des deux.
    """
    diag_img = math.hypot(full_size[0], full_size[1])
    diag_gnd = max(math.hypot(a[0] - b[0], a[1] - b[1])
                   for a in ground for b in ground)
    if diag_img <= 0 or diag_gnd <= 0:
        return None
    return diag_gnd / diag_img


def _metric_gcps(props, pitch_m, decim, img_size, full_size, crop_offset,
                 centre, ground, opt, feedback=None):
    """
    Calage deduit de la geometrie de prise de vue. Retourne (gcps, gsd) ou None
    si une piece manque, auquel cas l'appelant retombe sur l'emprise IGN.
    """
    if not opt.use_metric:
        return None
    if pitch_m is None:
        _log(feedback, u"  pas de numerisation absent des tags TIFF, "
                       u"echelle deduite de l'emprise")
    scale = _scale_of(props)
    gsd_from_footprint = None
    if not scale:
        # L'echelle n'est pas toujours diffusee dans le WFS. On la remplace par
        # celle qu'implique l'emprise : c'est moins rigoureux, mais on conserve
        # l'essentiel, a savoir le centre et l'angle d'orientation continu,
        # au lieu de retomber sur un ajustement des 4 coins.
        gsd_from_footprint = _gsd_from_footprint(ground, full_size)
        if gsd_from_footprint is None:
            _log(feedback, u"  echelle absente et emprise inexploitable")
            return None
        _log(feedback, u"  echelle absente : deduite de l'emprise "
                       u"(%.3f m/px, soit environ 1/%d)"
             % (gsd_from_footprint,
                round(gsd_from_footprint / (pitch_m * decim))
                if pitch_m else 0))

    if pitch_m:
        mm, std, err = georef.plate_format_mm(pitch_m * decim, full_size)
        _log(feedback, u"  scan %.1f um -> plaque %.0fx%.0f mm (format %dx%d)"
             % (pitch_m * 1e6, mm[0], mm[1], std[0], std[1]))
        if err > 0.15:
            _log(feedback, u"  ! format non standard, calage metrique suspect")

    orient = _orientation_of(props)
    north, note = georef.resolve_north_angle(
        -orient if (orient is not None and opt.invert_orientation) else orient,
        img_size, ground)
    _log(feedback, u"  %s" % note)
    if north is None:
        return None
    if opt.extra_rotation_deg:
        north = (north + opt.extra_rotation_deg + 180.0) % 360.0 - 180.0
        _log(feedback, u"  rotation manuelle +%d deg -> nord a %.0f deg"
             % (opt.extra_rotation_deg, north))
    if opt.mirror:
        _log(feedback, u"  symetrie horizontale appliquee")

    if scale:
        gsd = pitch_m * decim * scale
        _log(feedback, u"  echelle 1/%d -> %.3f m/px au sol" % (round(scale), gsd))
    else:
        gsd = gsd_from_footprint
    gcps = georef.gcps_from_metric(img_size, centre, gsd, north,
                                   crop_offset, full_size, mirror=opt.mirror)

    # garde-fou : le calage metrique doit rester compatible avec l'emprise du
    # tableau d'assemblage. Un ecart enorme signale une metadonnee mal lue
    # (echelle, unite de resolution, centre) plutot qu'une emprise imprecise.
    ok, note = _sanity_check(gcps, ground)
    _log(feedback, u"  %s" % note)
    if not ok:
        return None
    return gcps, gsd


def _sanity_check(gcps, ground, max_centre_ratio=0.75, size_range=(0.5, 2.0)):
    """Compare le calage metrique a l'emprise IGN. Retourne (ok, message)."""
    import math as _math

    quad = [(g.GCPX, g.GCPY) for g in gcps[:4]]
    def centre(pts):
        return (sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts))
    def diag(pts):
        return max(_math.hypot(a[0] - b[0], a[1] - b[1])
                   for a in pts for b in pts)

    cm, cf = centre(quad), centre(ground)
    dm, df = diag(quad), diag(ground)
    if df <= 0:
        return True, u"emprise IGN inexploitable, calage metrique conserve"

    shift = _math.hypot(cm[0] - cf[0], cm[1] - cf[1])
    ratio = dm / df
    msg = (u"controle : centre a %.0f m de l'emprise IGN, taille x%.2f"
           % (shift, ratio))
    if shift > max_centre_ratio * df:
        return False, msg + u" -> incoherent, repli sur l'emprise IGN"
    if not (size_range[0] <= ratio <= size_range[1]):
        return False, msg + u" -> echelle suspecte, repli sur l'emprise IGN"
    return True, msg


def _rotation_steps(props, img_size, ground, opt, feedback=None):
    """Quart de tour a appliquer : attribut IGN si disponible, sinon reglage."""
    extra = int(round(opt.extra_rotation_deg / 90.0)) % 4
    if opt.use_orientation:
        orient = _orientation_of(props)
        if orient is not None:
            steps, err = georef.rotation_from_orientation(
                img_size, ground, orient, invert=opt.invert_orientation)
            if err is not None:
                steps = (steps + extra) % 4
                _log(feedback, u"  orientation IGN %.0f deg -> rotation %d deg "
                               u"(ecart %.0f deg)" % (orient, steps * 90, err))
                return steps
    return (opt.rotation_steps + extra) % 4


def make_preview(props, ring3857, opt, max_size=1600, feedback=None):
    """
    Apercu rapide : lit une version decimee du scan directement sur le serveur
    via /vsicurl/, sans rapatrier les centaines de Mo du fichier complet, puis
    la cale au niveau 1.
    """
    from osgeo import gdal

    ds_id = props["dataset_identifier"]
    img_id = props["image_identifier"]
    tmp_dir = os.path.join(opt.outdir, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    cache_meta = os.path.join(tmp_dir, "%s_apercu.json" % img_id)
    cache_img = os.path.join(tmp_dir, "%s_apercu_crop.tif" % img_id)
    cache_raw = os.path.join(tmp_dir, "%s_apercu_brut.tif" % img_id)
    if os.path.exists(cache_meta) and (os.path.exists(cache_img) or
                                       os.path.exists(cache_raw)):
        try:
            with open(cache_meta, encoding="utf-8") as fh:
                meta = json.load(fh)
            small = cache_img if os.path.exists(cache_img) else cache_raw
            _log(feedback, u"  apercu deja telecharge, simple recalcul")
            return _preview_warp(props, ring3857, opt, small, img_id, tmp_dir,
                                 tuple(meta["prev_full"]),
                                 tuple(meta["crop_offset"]),
                                 meta["scale_factor"], meta["pitch"], feedback)
        except Exception:  # noqa: BLE001
            pass

    ign_api.configure_gdal_http()
    src = None
    for ext in (".tif", ".jp2"):
        try:
            src = gdal.Open(ign_api.vsicurl_path(ds_id, img_id, ext))
        except Exception:  # noqa: BLE001
            src = None
        if src is not None:
            break
    if src is None:
        raise IOError(u"Lecture distante impossible pour %s" % img_id)

    w, h = src.RasterXSize, src.RasterYSize
    scale = max(1.0, max(w, h) / float(max_size))
    ow, oh = max(1, int(w / scale)), max(1, int(h / scale))
    _log(feedback, u"  lecture distante %dx%d -> apercu %dx%d" % (w, h, ow, oh))

    small = os.path.join(tmp_dir, "%s_apercu_brut.tif" % img_id)
    gdal.Translate(small, src, options=gdal.TranslateOptions(
        width=ow, height=oh, format="GTiff",
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES"]))
    # les tags de resolution sont portes par le fichier distant : on les lit
    # avant de fermer le dataset, la version decimee ne les conserve pas
    pitch_remote, _dpi = georef.scan_pitch_ds(src)
    src = None
    scale_factor = float(w) / float(ow)
    prev_full = (ow, oh)
    crop_offset = (0, 0)

    if opt.crop_mode != CROP_NONE:
        box = (crop_mod.detect_content_box(small, margin_pct=opt.crop_margin)
               if opt.crop_mode == CROP_AUTO
               else crop_mod.fixed_box(small, margin_pct=opt.fixed_margin))
        cut = os.path.join(tmp_dir, "%s_apercu_crop.tif" % img_id)
        crop_mod.crop_to_file(small, cut, box)
        small = cut
        crop_offset = (box[0], box[1])

    with open(os.path.join(tmp_dir, "%s_apercu.json" % img_id), "w",
              encoding="utf-8") as fh:
        json.dump({"prev_full": list(prev_full),
                   "crop_offset": list(crop_offset),
                   "scale_factor": scale_factor,
                   "pitch": pitch_remote}, fh)

    return _preview_warp(props, ring3857, opt, small, img_id, tmp_dir,
                         prev_full, crop_offset, scale_factor, pitch_remote,
                         feedback)


def _preview_warp(props, ring3857, opt, small, img_id, tmp_dir, prev_full,
                  crop_offset, scale_factor, pitch_remote, feedback=None):
    """Calage de l'apercu, a partir de la version decimee deja disponible."""
    img_size = georef.raster_size(small)
    rect = georef.order_corners_cw(georef.min_area_rect(ring3857))
    ground = georef.transform_points(rect, "EPSG:3857", opt.out_crs)
    centre = _centre_of(props, ring3857, opt.out_crs)

    metric = _metric_gcps(props, pitch_remote, scale_factor, img_size,
                          prev_full, crop_offset, centre, ground, opt, feedback)
    if metric is not None:
        gcps, res = metric
    else:
        res = _auto_resolution(ground, img_size)
        steps = _rotation_steps(props, img_size, ground, opt, feedback)
        gcps = georef.gcps_from_footprint(img_size, ground, steps,
                                          mirror=opt.mirror)
    dest = os.path.join(tmp_dir, "%s_apercu.tif" % img_id)
    georef.warp_with_gcps(small, dest, gcps, opt.out_crs, opt.out_crs,
                          res=res, order=1)
    return dest


def _settings_stamp(opt):
    """Signature des reglages qui influencent le resultat du calage."""
    return "|".join(str(v) for v in (
        opt.out_crs, opt.level, opt.crop_mode, opt.crop_margin,
        opt.fixed_margin, opt.resolution, opt.rotation_steps,
        opt.use_metric, opt.use_orientation, opt.invert_orientation,
        round(float(opt.extra_rotation_deg), 3), bool(opt.mirror)))


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
    # Le fichier existant n'est reutilisable que s'il a ete produit avec les
    # MEMES reglages : sinon un changement de rotation ou de niveau de calage
    # serait silencieusement ignore.
    stamp_path = os.path.join(out_dir, "%s_cale.params" % img_id)
    stamp = _settings_stamp(opt)
    if os.path.exists(dest) and not opt.overwrite:
        previous = None
        try:
            with open(stamp_path, encoding="utf-8") as fh:
                previous = fh.read().strip()
        except Exception:  # noqa: BLE001
            previous = None
        if previous == stamp:
            _log(feedback, u"  deja traite avec les memes reglages, ignore")
            return _stamp_done()
        _log(feedback, u"  reglages modifies -> recalcul")

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
    crop_offset = (0, 0)
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
        crop_offset = (box[0], box[1])
    if is_canceled and is_canceled():
        return None

    img_size = georef.raster_size(cropped)
    full_size = georef.raster_size(raw)

    # ---- 3. emprise IGN -> CRS de sortie -------------------------------
    rect = georef.min_area_rect(ring3857)
    rect = georef.order_corners_cw(rect)
    ground = georef.transform_points(rect, "EPSG:3857", opt.out_crs)
    centre = _centre_of(props, ring3857, opt.out_crs)

    # ---- 4. niveau 1 : geometrie de prise de vue, sinon emprise --------
    pitch, dpi = georef.scan_pitch(raw)
    metric = _metric_gcps(props, pitch, 1.0, img_size, full_size, crop_offset,
                          centre, ground, opt, feedback)
    if metric is not None:
        gcps, gsd = metric
        res = opt.resolution or round(gsd, 3)
        _log(feedback, u"  calage metrique (%.3f m/px)" % res)
    else:
        res = opt.resolution or _auto_resolution(ground, img_size)
        steps = _rotation_steps(props, img_size, ground, opt, feedback)
        gcps = georef.gcps_from_footprint(img_size, ground, steps,
                                          mirror=opt.mirror)
        _log(feedback, u"  calage sur l'emprise IGN (%.2f m/px)" % res)
    georef.warp_with_gcps(cropped, dest, gcps, opt.out_crs, opt.out_crs,
                          res=res, order=1)
    def _stamp_done(path=dest):
        try:
            with open(stamp_path, "w", encoding="utf-8") as fh:
                fh.write(stamp)
        except Exception:  # noqa: BLE001
            pass
        return path

    if opt.level == LEVEL_FOOTPRINT:
        if opt.build_ovr:
            georef.build_overviews(dest)
        return _stamp_done()

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
        return _stamp_done()

    if is_canceled and is_canceled():
        return None

    match = None
    try:
        if use_cv2:
            # 1) images pre-alignees : de loin le plus fiable
            _log(feedback, u"  recherche de points d'appui (%s sur images "
                           u"pre-alignees)..." % georef.make_detector()[2])
            match = georef.match_calibrated(dest, ortho, gcps)
            # 2) a defaut, appariement direct sur le scan brut
            if match is None:
                _log(feedback, u"  aucun appui pre-aligne, essai sur le scan "
                               u"brut...")
                match = georef.match_on_ortho(cropped, ortho)
        # 3) dernier recours, correlation de phase (sans OpenCV ou si echec)
        if match is None:
            _log(feedback, u"  essai par correlation de phase...")
            match = georef.match_on_ortho_fft(dest, ortho, gcps)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! appariement impossible (%s), calage de niveau 1 "
                       u"conserve." % exc)
        match = None

    if match is None:
        _log(feedback, u"  ! aucun appariement fiable, calage sur emprise conserve.")
        if opt.build_ovr:
            georef.build_overviews(dest)
        return _stamp_done()

    img_xy, gnd_xy, inliers, steps = match[:4]
    if len(match) > 4:
        det_name, mirror = match[4], match[5]
        _log(feedback, u"  %d points d'appui %s (%d inliers, rotation %d deg%s)"
             % (len(img_xy), det_name, inliers, steps * 90,
                u", scan en miroir" if mirror else u""))
    else:
        _log(feedback, u"  %d points d'appui (tuiles correlees)" % len(img_xy))

    # mesure du deplacement apporte par le recalage : utile pour juger
    try:
        A_before, _ = georef.affine_from_gcps(gcps)
        shifts = []
        for (px, py), (X, Y) in zip(img_xy, gnd_xy):
            before = A_before.dot(np.array([px, py, 1.0]))
            shifts.append(math.hypot(before[0] - X, before[1] - Y))
        if shifts:
            _log(feedback, u"  correction apportee : %.0f m en moyenne "
                           u"(max %.0f m)"
                 % (sum(shifts) / len(shifts), max(shifts)))
    except Exception:  # noqa: BLE001
        pass

    order = 2 if len(img_xy) >= 12 else 1
    gcps2 = [gdal.GCP(g[0], g[1], 0.0, p[0], p[1])
             for p, g in zip(img_xy, gnd_xy)]
    georef.warp_with_gcps(cropped, dest, gcps2, opt.out_crs, opt.out_crs,
                          res=res, order=order)

    if opt.level == LEVEL_ORTHO:
        if opt.build_ovr:
            georef.build_overviews(dest)
        return _stamp_done()

    # ---- 6. niveau 3 : orthorectification sur MNT ---------------------
    if len(img_xy) < 6:
        _log(feedback, u"  ! moins de 6 points d'appui : ortho MNT impossible.")
        return _stamp_done()

    _log(feedback, u"  interrogation du RGE ALTI...")
    lonlat = georef.transform_points(gnd_xy, opt.out_crs, "EPSG:4326")
    try:
        zs = ign_api.elevations(lonlat)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! RGE ALTI indisponible (%s)" % exc)
        return _stamp_done()

    xyz, uv = [], []
    for (X, Y), z, p in zip(gnd_xy, zs, img_xy):
        if z is None or z < -1000:
            continue
        xyz.append((X, Y, float(z)))
        uv.append(p)
    if len(xyz) < 6:
        _log(feedback, u"  ! altitudes insuffisantes, ortho MNT abandonnee.")
        return _stamp_done()

    try:
        P = georef.solve_dlt(xyz, uv)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! resection spatiale impossible (%s)" % exc)
        return _stamp_done()

    resid = georef.dlt_residuals(P, xyz, uv)
    _log(feedback, u"  resection : residu moyen %.1f px (max %.1f px)"
         % (resid.mean(), resid.max()))
    if resid.mean() > 60:
        _log(feedback, u"  ! resection peu fiable, on conserve le calage 2D.")
        return _stamp_done()

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
        return _stamp_done()

    _log(feedback, u"  orthorectification sur MNT...")
    ortho_dest = os.path.join(out_dir, "%s_ortho.tif" % img_id)
    try:
        georef.orthorectify(cropped, dem, P, ortho_dest, res, bounds, opt.out_crs)
    except Exception as exc:  # noqa: BLE001
        _log(feedback, u"  ! echec de l'orthorectification (%s), calage 2D "
                       u"conserve." % exc)
        return _stamp_done()

    if opt.build_ovr:
        georef.build_overviews(ortho_dest)
    return _stamp_done(ortho_dest)
