"""Délégué au module partagé TITAN (D11) : titan_shared, dépôt home, dossier partage/.

Le module est remplacé dans sys.modules : tous les noms (y compris privés), les
imports des appelants et les monkeypatch des tests visent le module partagé.
"""
import sys

from titan_shared.meteo import etat as _partage

sys.modules[__name__] = _partage
