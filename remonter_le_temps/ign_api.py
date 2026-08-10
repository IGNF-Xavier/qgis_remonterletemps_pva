# -*- coding: utf-8 -*-
"""
Acces aux services IGN Geoplateforme utilises par le site remonterletemps.ign.fr.

Points d'entree (verifies sur le code source officiel IGNF/Pompei) :

  * Tableau d'assemblage des MISSIONS (chantiers) :
        WFS  typeName=pva:dataset
  * Tableau d'assemblage des CLICHES d'une mission :
        WFS  typeName=pva:image
  * Telechargement du scan brut :
        https://data.geopf.fr/telechargement/download/pva/<dataset_identifier>/<image_identifier>.tif
  * Ortho actuelle (reference de calage) :  WMS-R  ORTHOIMAGERY.ORTHOPHOTOS
  * MNT RGE ALTI (orthorectification)  :  WMS-R  ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES

Les geometries du WFS pva sont diffusees en EPSG:3857 (Web Mercator).
"""

import json
import os
import ssl
import time
import urllib.parse
import urllib.request

WFS_URL = "https://data.geopf.fr/wfs"
WFS_URL_ALT = "https://data.geopf.fr/wfs/ows"
DOWNLOAD_URL = "https://data.geopf.fr/telechargement/download/pva/{ds}/{img}{ext}"
WMS_URL = "https://data.geopf.fr/wms-r/wms"
ALTI_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"

LAYER_ORTHO = "ORTHOIMAGERY.ORTHOPHOTOS"
LAYER_MNT = "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES"

WFS_SRS = "EPSG:3857"
PAGE_SIZE = 5000
USER_AGENT = "QGIS-RemonterLeTemps/1.0 (+plugin)"

_SSL_CTX = ssl.create_default_context()


class IgnError(Exception):
    pass


# --------------------------------------------------------------------------
# bas niveau
# --------------------------------------------------------------------------
def _open(url, timeout=120, tries=3, pause=3.0):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < tries - 1:
                time.sleep(pause)
    raise IgnError(u"Echec de la requete :\n%s\n\n%s" % (url, last))


def _get_bytes(url, timeout=120, tries=3):
    with _open(url, timeout=timeout, tries=tries) as resp:
        return resp.read()


def _get_json(url, timeout=120, tries=3):
    raw = _get_bytes(url, timeout=timeout, tries=tries)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise IgnError(u"Reponse non JSON (le service a peut-etre change) :\n%s\n%s"
                       % (url, raw[:400]))


# --------------------------------------------------------------------------
# WFS
# --------------------------------------------------------------------------
def _wfs_url(base, typename, cql=None, start=0, count=PAGE_SIZE, srs=WFS_SRS):
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": typename,
        "outputFormat": "json",
        "srsName": srs,
        "count": str(count),
        "startIndex": str(start),
    }
    if cql:
        params["cql_filter"] = cql
    return base + "?" + urllib.parse.urlencode(params)


def wfs_features(typename, cql=None, srs=WFS_SRS, max_features=100000,
                 progress=None, is_canceled=None):
    """Recupere toutes les entites (pagination automatique) sous forme GeoJSON."""
    bases = [WFS_URL, WFS_URL_ALT]
    data = None
    err = None
    for base in bases:
        try:
            data = _get_json(_wfs_url(base, typename, cql, 0, PAGE_SIZE, srs))
            break
        except IgnError as exc:
            err = exc
    if data is None:
        raise err

    total = int(data.get("totalFeatures") or data.get("numberMatched") or
                len(data.get("features", [])))
    feats = list(data.get("features", []))

    fetched = len(feats)
    while fetched < min(total, max_features):
        if is_canceled is not None and is_canceled():
            break
        if progress is not None:
            progress(100.0 * fetched / max(1, min(total, max_features)))
        page = _get_json(_wfs_url(base, typename, cql, fetched, PAGE_SIZE, srs))
        chunk = page.get("features", [])
        if not chunk:
            break
        feats.extend(chunk)
        fetched += len(chunk)

    data["features"] = feats[:max_features]
    data["totalFeatures"] = total
    return data


def bbox_wkt(xmin, ymin, xmax, ymax):
    return ("POLYGON((%f %f,%f %f,%f %f,%f %f,%f %f))"
            % (xmin, ymin, xmin, ymax, xmax, ymax, xmax, ymin, xmin, ymin))


def fetch_missions(bbox3857, year_min=None, year_max=None, **kw):
    """Tableau d'assemblage des missions (chantiers) intersectant la bbox."""
    cql = "INTERSECTS(geom,%s)" % bbox_wkt(*bbox3857)
    if year_min:
        cql += " AND date_mission >= '%04d-01-01'" % int(year_min)
    if year_max:
        cql += " AND date_mission <= '%04d-12-31'" % int(year_max)
    return wfs_features("pva:dataset", cql=cql, **kw)


def fetch_cliches(bbox3857, dataset_identifier=None, **kw):
    """Tableau d'assemblage des cliches (emprises unitaires)."""
    cql = "INTERSECTS(geom,%s)" % bbox_wkt(*bbox3857)
    if dataset_identifier:
        cql += " AND dataset_identifier='%s'" % dataset_identifier
    return wfs_features("pva:image", cql=cql, **kw)


# --------------------------------------------------------------------------
# telechargement des scans
# --------------------------------------------------------------------------
def download_cliche(dataset_identifier, image_identifier, outdir, overwrite=False):
    """Telecharge le scan brut. Retourne le chemin local."""
    os.makedirs(outdir, exist_ok=True)
    last = None
    for ext in (".tif", ".jp2"):
        dest = os.path.join(outdir, image_identifier + ext)
        if os.path.exists(dest) and os.path.getsize(dest) > 0 and not overwrite:
            return dest
        url = DOWNLOAD_URL.format(ds=dataset_identifier, img=image_identifier, ext=ext)
        try:
            with _open(url, timeout=600, tries=2) as resp:
                tmp = dest + ".part"
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
            os.replace(tmp, dest)
            return dest
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise IgnError(u"Cliche %s introuvable au telechargement (%s)"
                   % (image_identifier, last))


# --------------------------------------------------------------------------
# WMS : ortho de reference et MNT
# --------------------------------------------------------------------------
def _wms_url(layer, bbox, crs, width, height, fmt, styles=""):
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": layer,
        "STYLES": styles,
        "CRS": crs,
        "BBOX": "%f,%f,%f,%f" % bbox,
        "WIDTH": str(int(width)),
        "HEIGHT": str(int(height)),
        "FORMAT": fmt,
        "TRANSPARENT": "FALSE",
    }
    return WMS_URL + "?" + urllib.parse.urlencode(params)


def fetch_ortho(bbox, crs, width, height, dest):
    """Extrait de l'ortho actuelle en GeoTIFF (reference pour le recalage)."""
    url = _wms_url(LAYER_ORTHO, bbox, crs, width, height, "image/geotiff")
    data = _get_bytes(url, timeout=300)
    if data[:20].lstrip().startswith(b"<"):
        raise IgnError(u"Le WMS a renvoye une erreur XML pour l'ortho de reference.")
    with open(dest, "wb") as fh:
        fh.write(data)
    return dest


def fetch_dem(bbox, crs, width, height, dest):
    """MNT RGE ALTI en valeurs brutes (float32). Ecrit un GeoTIFF."""
    from osgeo import gdal, osr

    url = _wms_url(LAYER_MNT, bbox, crs, width, height,
                   "image/x-bil;bits=32", styles="normal")
    data = _get_bytes(url, timeout=300)
    if data[:20].lstrip().startswith(b"<"):
        # repli : geotiff brut
        url = _wms_url(LAYER_MNT, bbox, crs, width, height,
                       "image/geotiff", styles="normal")
        data = _get_bytes(url, timeout=300)
        if data[:20].lstrip().startswith(b"<"):
            raise IgnError(u"MNT indisponible sur cette emprise.")
        with open(dest, "wb") as fh:
            fh.write(data)
        return dest

    # BIL brut -> on ecrit l'en-tete ENVI puis on convertit
    bil = dest + ".bil"
    with open(bil, "wb") as fh:
        fh.write(data)
    hdr = dest + ".hdr"
    with open(hdr, "w") as fh:
        fh.write("ENVI\nsamples = %d\nlines = %d\nbands = 1\n"
                 "header offset = 0\nfile type = ENVI Standard\n"
                 "data type = 4\ninterleave = bsq\nbyte order = 0\n"
                 % (int(width), int(height)))
    src = gdal.Open(bil)
    if src is None:
        raise IgnError(u"Lecture du MNT BIL impossible.")
    gt = (bbox[0], (bbox[2] - bbox[0]) / float(width), 0.0,
          bbox[3], 0.0, -(bbox[3] - bbox[1]) / float(height))
    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)
    drv = gdal.GetDriverByName("GTiff")
    out = drv.CreateCopy(dest, src, 0)
    out.SetGeoTransform(gt)
    out.SetProjection(srs.ExportToWkt())
    out.GetRasterBand(1).SetNoDataValue(-99999.0)
    out = None
    src = None
    for f in (bil, hdr):
        try:
            os.remove(f)
        except OSError:
            pass
    return dest


def elevations(lonlat_list, resource="ign_rge_alti_wld"):
    """Altitudes RGE ALTI pour une liste de (lon, lat) WGS84. Renvoie [z]."""
    out = []
    step = 150
    for i in range(0, len(lonlat_list), step):
        chunk = lonlat_list[i:i + step]
        params = {
            "lon": "|".join("%.6f" % p[0] for p in chunk),
            "lat": "|".join("%.6f" % p[1] for p in chunk),
            "resource": resource,
            "delimiter": "|",
            "indent": "false",
            "measures": "false",
            "zonly": "true",
        }
        data = _get_json(ALTI_URL + "?" + urllib.parse.urlencode(params))
        out.extend(data.get("elevations", []))
    return out
