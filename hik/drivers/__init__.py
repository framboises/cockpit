"""Pilotes des cameras : une classe HikCamera, un module par marque.

    from hik.drivers import HikCamera
    cam = HikCamera(name, ip, port, user, password, channel=1, brand="dahua")

base.py      HTTP Digest, ONVIF, repartition par marque (infos, presets,
             capture, masques)
hikvision.py ISAPI (et Uniview) : PTZ, essuie-glace, Smart Events...
dahua.py, bosch.py, hanwha.py, axis.py

Le nom HikCamera est historique (multi-marques depuis 2026) ; il reste
importable depuis hik.hik_control. Cockpit le cree par
cameras.make_hik_camera (identifiants dechiffres, verrou piste).
"""
from .axis import AxisMixin
from .base import BaseCamera, PRIVACY_BRANDS
from .bosch import BoschMixin
from .dahua import DahuaMixin
from .hanwha import HanwhaMixin
from .hikvision import HikvisionMixin


class HikCamera(HikvisionMixin, DahuaMixin, BoschMixin, HanwhaMixin, AxisMixin, BaseCamera):
    """Classe de controle d'une camera multi-marques."""


__all__ = ["HikCamera", "PRIVACY_BRANDS"]
