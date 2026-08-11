# -*- coding: utf-8 -*-
"""
Compatibilite PyQt5 / PyQt6.

PyQt6 a rendu les enumerations "scopees" : Qt.Checked devient
Qt.CheckState.Checked, QMessageBox.Yes devient
QMessageBox.StandardButton.Yes, etc. La fonction qt_enum() renvoie la bonne
valeur quelle que soit la version, sans imposer de dependance conditionnelle
dans le reste du code.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialogButtonBox, QMessageBox


def qt_enum(owner, scope, name):
    """qt_enum(Qt, 'CheckState', 'Checked') -> Qt.CheckState.Checked ou Qt.Checked."""
    holder = getattr(owner, scope, None)
    if holder is not None and hasattr(holder, name):
        return getattr(holder, name)
    return getattr(owner, name)


# --- Qt.CheckState
CHECKED = qt_enum(Qt, "CheckState", "Checked")
UNCHECKED = qt_enum(Qt, "CheckState", "Unchecked")
PARTIAL = qt_enum(Qt, "CheckState", "PartiallyChecked")

# --- Qt.ItemFlag
ITEM_ENABLED = qt_enum(Qt, "ItemFlag", "ItemIsEnabled")
ITEM_SELECTABLE = qt_enum(Qt, "ItemFlag", "ItemIsSelectable")
ITEM_CHECKABLE = qt_enum(Qt, "ItemFlag", "ItemIsUserCheckable")
ITEM_AUTOTRISTATE = qt_enum(Qt, "ItemFlag", "ItemIsAutoTristate")

# --- Qt.Orientation / zones d'ancrage
HORIZONTAL = qt_enum(Qt, "Orientation", "Horizontal")
RIGHT_DOCK = qt_enum(Qt, "DockWidgetArea", "RightDockWidgetArea")

# --- Qt.ItemDataRole
USER_ROLE = qt_enum(Qt, "ItemDataRole", "UserRole")

# --- QMessageBox
MB_YES = qt_enum(QMessageBox, "StandardButton", "Yes")
MB_NO = qt_enum(QMessageBox, "StandardButton", "No")
MB_CANCEL = qt_enum(QMessageBox, "StandardButton", "Cancel")

# --- QDialogButtonBox (garde pour usages futurs)
DBB_OK = qt_enum(QDialogButtonBox, "StandardButton", "Ok")
