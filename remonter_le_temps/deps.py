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


class _LogSink(object):
    """Remplace un flux standard absent : sous Windows, QGIS demarre sans
    console et sys.stderr vaut None. Toute bibliotheque qui tente d'ecrire un
    avertissement leve alors AttributeError: 'NoneType' has no attribute
    'write', ce qui masque completement le message d'origine."""

    def __init__(self, tag):
        self.tag = tag
        self._buffer = ""

    def write(self, text):
        try:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    _log_line(line, self.tag)
        except Exception:  # noqa: BLE001
            pass
        return len(text or "")

    def flush(self):
        if self._buffer.strip():
            _log_line(self._buffer, self.tag)
        self._buffer = ""

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("flux virtuel")


def _log_line(line, tag):
    try:
        from qgis.core import Qgis, QgsMessageLog
        level = Qgis.Warning if tag == "stderr" else Qgis.Info
        QgsMessageLog.logMessage(line, "Remonter le temps", level)
    except Exception:  # noqa: BLE001
        pass


def ensure_stdio():
    """Garantit que sys.stdout / sys.stderr sont ecrivables."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "write"):
            setattr(sys, name, _LogSink(name))


def ensure_path():
    """
    Rend les paquets locaux importables.

    Le dossier est ajoute en FIN de sys.path, jamais au debut : s'il contenait
    par accident une copie de numpy, la placer en tete masquerait celle de
    QGIS et provoquerait un conflit d'ABI avec les extensions C compilees
    contre elle (GDAL, scipy...).
    """
    ensure_stdio()
    if os.path.isdir(LIBS_DIR) and LIBS_DIR not in sys.path:
        sys.path.append(LIBS_DIR)


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
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"      # n'ecrit jamais dans le site utilisateur
    env.pop("PIP_USER", None)
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  universal_newlines=True, env=env)
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
    # --no-deps est essentiel : sans lui, pip installe SA version de numpy a
    # cote d'OpenCV, qui entre alors en conflit avec celle de QGIS.
    args = ["-m", "pip", "install", "--upgrade", "--target", LIBS_DIR,
            "--only-binary=:all:", "--no-deps", "--no-input",
            "--disable-pip-version-check", package]

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


# --------------------------------------------------------------------------
# diagnostic
# --------------------------------------------------------------------------
def _module_locations(name):
    """Toutes les copies d'un module presentes sur sys.path, dans l'ordre."""
    found = []
    for entry in sys.path:
        if not entry:
            entry = os.getcwd()
        for candidate in (os.path.join(entry, name, "__init__.py"),
                          os.path.join(entry, name + ".py")):
            if os.path.exists(candidate):
                path = os.path.dirname(candidate)
                if path not in found:
                    found.append(path)
                break
    return found


def check_numpy_abi():
    """
    Verifie que numpy et les extensions C qui en dependent s'entendent.
    Retourne (ok, message).
    """
    try:
        import numpy
    except Exception as exc:  # noqa: BLE001
        return False, u"numpy inutilisable : %s" % exc
    try:
        from osgeo import gdal_array  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, (u"Les bindings GDAL refusent numpy %s : %s\n"
                       u"C'est la signature d'un conflit de versions."
                       % (numpy.__version__, exc))
    return True, u"numpy %s et GDAL s'entendent." % numpy.__version__


def diagnose():
    """Rapport lisible sur l'environnement Python, pour le journal du panneau."""
    lines = [u"--- Diagnostic de l'environnement ---",
             u"Python   : %s" % sys.version.split()[0],
             u"Executable : %s" % (sys.executable or u"inconnu"),
             u"stdout/stderr : %s / %s"
             % (type(sys.stdout).__name__, type(sys.stderr).__name__)]

    numpys = _module_locations("numpy")
    lines.append(u"numpy : %d copie(s) sur sys.path" % len(numpys))
    for path in numpys:
        lines.append(u"   %s" % path)
    if len(numpys) > 1:
        lines.append(u"   ! Plusieurs numpy : c'est la cause classique de "
                     u"l'erreur \"NoneType has no attribute 'write'\". "
                     u"Supprimez celui qui n'est pas fourni par QGIS.")
    try:
        import numpy
        lines.append(u"numpy actif : %s (%s)"
                     % (numpy.__version__, os.path.dirname(numpy.__file__)))
    except Exception as exc:  # noqa: BLE001
        lines.append(u"numpy inutilisable : %s" % exc)

    if os.path.isdir(LIBS_DIR):
        content = sorted(n for n in os.listdir(LIBS_DIR)
                         if not n.endswith(("dist-info", ".pyc")))
        lines.append(u"libs/ : %s" % (u", ".join(content) or u"vide"))
        if any(n == "numpy" for n in content):
            lines.append(u"   ! numpy est present dans libs/ alors qu'il ne "
                         u"devrait pas. Cliquez sur Retirer, puis "
                         u"reinstallez OpenCV.")
    else:
        lines.append(u"libs/ : absent")

    ok, msg = check_numpy_abi()
    lines.append((u"OK   " if ok else u"! ") + msg)

    for name in ("cv2", "osgeo"):
        locs = _module_locations(name)
        lines.append(u"%s : %s" % (name, locs[0] if locs else u"absent"))
    try:
        from .georef import opencv_report
        lines.append(opencv_report())
    except Exception as exc:  # noqa: BLE001
        lines.append(u"OpenCV : rapport indisponible (%s)" % exc)

    user_site = [p for p in sys.path
                 if "Roaming" in p and "site-packages" in p]
    if user_site:
        lines.append(u"Site utilisateur present sur sys.path :")
        for path in user_site:
            lines.append(u"   %s" % path)
        lines.append(u"   Tout paquet installe la prend le pas sur celui de "
                     u"QGIS. En cas de doute : pip uninstall numpy, ou "
                     u"suppression manuelle du dossier numpy qui s'y trouve.")
    return u"\n".join(lines)
