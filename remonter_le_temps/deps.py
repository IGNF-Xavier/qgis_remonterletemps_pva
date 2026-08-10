# -*- coding: utf-8 -*-
"""
Gestion des dependances optionnelles (OpenCV).

QGIS n'a pas de mecanisme de dependances pour les extensions : un ZIP
d'extension ne declare pas de requirements. Deux approches sont possibles :

  a) embarquer les wheels dans le ZIP  -> impossible en pratique : il faudrait
     une roue par OS et par version de Python (~90 Mo chacune) ;
  b) installer a la demande, dans un dossier prive de l'extension, sans
     toucher a l'installation Python de QGIS  -> c'est ce qui est fait ici.

Le dossier <extension>/libs est ajoute a sys.path au demarrage. Si OpenCV y est
installe, le plugin l'utilise ; sinon il bascule sur un apparieur de secours en
numpy pur (voir georef.match_on_ortho_fft).
"""

import os
import shutil
import subprocess
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
LIBS_DIR = os.path.join(PLUGIN_DIR, "libs")
PACKAGE = "opencv-python-headless"


def ensure_path():
    """A appeler tres tot : rend les paquets locaux importables."""
    if os.path.isdir(LIBS_DIR) and LIBS_DIR not in sys.path:
        sys.path.insert(0, LIBS_DIR)


def have_cv2():
    ensure_path()
    try:
        import cv2  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def cv2_version():
    try:
        import cv2
        return cv2.__version__
    except Exception:  # noqa: BLE001
        return None


def _python_candidates():
    """Interpreteur capable de lancer pip (sys.executable est souvent qgis-bin)."""
    cands = []
    exe = sys.executable or ""
    base = os.path.basename(exe).lower()
    if base.startswith("python"):
        cands.append(exe)
    if os.name == "nt":
        cands += [
            os.path.join(sys.prefix, "python.exe"),
            os.path.join(sys.prefix, "bin", "python.exe"),
            os.path.join(sys.exec_prefix, "python.exe"),
        ]
        for name in ("python-qgis.bat", "python-qgis-ltr.bat", "python3.exe"):
            found = shutil.which(name)
            if found:
                cands.append(found)
    else:
        for name in ("python3", "python"):
            found = shutil.which(name)
            if found:
                cands.append(found)
        cands += [
            os.path.join(sys.prefix, "bin", "python3"),
            os.path.join(sys.exec_prefix, "bin", "python3"),
        ]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen and os.path.exists(c.split()[0] if " " not in c else c):
            seen.add(c)
            out.append(c)
    return out


def _run(cmd, feedback):
    if feedback:
        feedback(u"  $ " + u" ".join(cmd))
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  universal_newlines=True)
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
    proc = subprocess.Popen(cmd, **kwargs)
    out = []
    for line in proc.stdout:
        line = line.rstrip()
        out.append(line)
        if feedback and line:
            feedback(u"  " + line[:300])
    proc.wait()
    return proc.returncode, u"\n".join(out)


def install_opencv(feedback=None, package=PACKAGE):
    """
    Installe OpenCV dans <extension>/libs. Retourne (ok, message).
    N'ecrit rien en dehors du dossier de l'extension.
    """
    os.makedirs(LIBS_DIR, exist_ok=True)
    args = ["-m", "pip", "install", "--upgrade", "--target", LIBS_DIR,
            "--only-binary=:all:", package]

    errors = []
    for exe in _python_candidates():
        try:
            code, out = _run([exe] + args, feedback)
            if code == 0:
                ensure_path()
                _purge_import_cache()
                if have_cv2():
                    return True, u"OpenCV %s installe dans %s" % (
                        cv2_version(), LIBS_DIR)
                errors.append(u"%s : pip a reussi mais l'import echoue" % exe)
            else:
                errors.append(u"%s : code %s\n%s" % (exe, code, out[-500:]))
        except Exception as exc:  # noqa: BLE001
            errors.append(u"%s : %s" % (exe, exc))

    # dernier recours : pip dans le processus QGIS
    try:
        from pip._internal.cli.main import main as pip_main
        if feedback:
            feedback(u"  repli : pip en interne")
        if pip_main(args[2:]) == 0:
            ensure_path()
            _purge_import_cache()
            if have_cv2():
                return True, u"OpenCV %s installe dans %s" % (
                    cv2_version(), LIBS_DIR)
    except Exception as exc:  # noqa: BLE001
        errors.append(u"pip interne : %s" % exc)

    return False, (u"Installation impossible. Installez OpenCV manuellement :\n"
                   u"  python -m pip install %s\n\nDetails :\n%s"
                   % (package, u"\n".join(errors[-3:])))


def uninstall_opencv():
    if os.path.isdir(LIBS_DIR):
        shutil.rmtree(LIBS_DIR, ignore_errors=True)
        return True
    return False


def _purge_import_cache():
    import importlib
    importlib.invalidate_caches()
    for mod in [m for m in sys.modules if m == "cv2" or m.startswith("cv2.")]:
        sys.modules.pop(mod, None)
