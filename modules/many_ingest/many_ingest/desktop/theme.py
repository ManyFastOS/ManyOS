"""QSS applying the ManyOS Design Language basics to the desktop shell.

Fase 0: dark-by-default, one accent color, calm typography (hoofdstuk 2-4).
Fase 1 adds: a Caption text role (hoofdstuk 3), a Link button (hoofdstuk 4,
"Andere schijf gebruiken"/"Opnieuw zoeken"), and a Card style for the
multi-volume chooser — hoofdstuk 5 names "schijven" as the canonical Card use
case. Qt style sheets have no real box-shadow, so the card uses a subtle
border instead of the "minimale schaduw" the Design Language describes — an
accepted, documented platform limitation, not a deviation in intent.
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

QLabel#captionLabel {
    font-size: 13px;
    color: #9a9aa0;
}

QPushButton#linkButton {
    background-color: transparent;
    color: #0a84ff;
    border: none;
    padding: 4px 8px;
    font-size: 13px;
    font-weight: 500;
}

QPushButton#linkButton:hover {
    color: #3396ff;
}

QPushButton#volumeCard {
    background-color: #2c2c2e;
    color: #e5e5e7;
    border: 1px solid #3a3a3c;
    border-radius: 10px;
    padding: 14px 18px;
    text-align: left;
    font-size: 14px;
}

QPushButton#volumeCard:hover {
    background-color: #3a3a3c;
}
"""
