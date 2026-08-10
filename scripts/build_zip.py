#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Construit le ZIP installable dans QGIS a partir des sources du depot.

    python scripts/build_zip.py [--outdir dist]

Le ZIP contient un unique dossier `remonter_le_temps/`, comme l'exige
"Installer depuis un ZIP". Les caches Python, le dossier `libs/` (dependance
installee a la demande cote utilisateur) et les fichiers d'edition sont exclus.
Le README et la licence du depot sont copies dans l'extension.
"""

import argparse
import os
import re
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "remonter_le_temps"
EXCLUDE_DIRS = {"__pycache__", "libs", ".git", ".idea", ".vscode"}
EXCLUDE_EXT = {".pyc", ".pyo", ".swp"}


def plugin_version():
    meta = os.path.join(ROOT, PKG, "metadata.txt")
    with open(meta, encoding="utf-8") as fh:
        match = re.search(r"^version\s*=\s*(.+)$", fh.read(), re.M)
    if not match:
        sys.exit("version introuvable dans metadata.txt")
    return match.group(1).strip()


def collect():
    base = os.path.join(ROOT, PKG)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1] in EXCLUDE_EXT:
                continue
            full = os.path.join(dirpath, name)
            yield full, os.path.join(PKG, os.path.relpath(full, base))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", default=os.path.join(ROOT, "dist"))
    args = parser.parse_args()

    version = plugin_version()
    os.makedirs(args.outdir, exist_ok=True)
    dest = os.path.join(args.outdir, "%s-%s.zip" % (PKG, version))

    extras = []
    for src, arc in (("README.md", "README.md"), ("LICENSE", "LICENSE")):
        path = os.path.join(ROOT, src)
        if os.path.exists(path):
            extras.append((path, os.path.join(PKG, arc)))

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arc in list(collect()) + extras:
            zf.write(full, arc)

    size = os.path.getsize(dest) / 1024.0
    print("OK  %s  (%.0f Ko)" % (dest, size))

    # copie pratique a nom fixe, pour les liens permanents
    latest = os.path.join(args.outdir, "%s.zip" % PKG)
    shutil.copyfile(dest, latest)
    print("OK  %s" % latest)


if __name__ == "__main__":
    main()
