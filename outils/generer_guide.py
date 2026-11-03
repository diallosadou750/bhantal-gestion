r"""Écrit GUIDE_UTILISATEUR.md à partir de gestion/aide.py (source unique du texte).

    .venv\Scripts\python.exe outils\generer_guide.py
"""
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from gestion import aide  # noqa: E402

(RACINE / "GUIDE_UTILISATEUR.md").write_text(aide.guide_markdown(), encoding="utf-8", newline="\n")
print("GUIDE_UTILISATEUR.md écrit :", len(aide.SECTIONS), "sections")
