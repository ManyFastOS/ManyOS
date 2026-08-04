"""Minimal QSS applying the ManyOS Design Language basics to the desktop shell.

Only what Fase 0 needs: dark-by-default, one accent color, calm typography
(see docs/MANYOS_DESIGN_LANGUAGE.md, hoofdstuk 2-4). Extended as later phases
add more widget kinds (cards, secondary/ghost/link buttons, ...).
"""

from __future__ import annotations

STYLE_SHEET = """
QWidget {
    background-color: #1c1c1e;
    color: #e5e5e7;
    font-family: -apple-system, "Helvetica Neue", sans-serif;
    font-size: 14px;
}

QLabel#statusLabel {
    font-size: 17px;
    color: #e5e5e7;
}

QPushButton#primaryButton {
    background-color: #0a84ff;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 10px 28px;
    font-size: 14px;
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background-color: #3396ff;
}

QPushButton#primaryButton:pressed {
    background-color: #086bd1;
}
"""
