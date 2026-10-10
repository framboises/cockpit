# anpr.py -- Blueprint LAPI / ANPR (Lecture Automatique de Plaques)
import os
import re
import time
import socket
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from zoneinfo import ZoneInfo
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request, render_template, Response, send_file
from pymongo import MongoClient, DESCENDING
from bson.objectid import ObjectId
import gridfs

anpr_bp = Blueprint("anpr", __name__)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth helper (import from app at request time to avoid circular import)
# ---------------------------------------------------------------------------
def _check_admin():
    from app import (CODING, JWT_SECRET, JWT_ALGORITHM,
                     ROLE_HIERARCHY, ROLE_ORDER, APP_KEY)
    import jwt as pyjwt
    if CODING:
        sim_role = request.args.get("as", "admin")
        if sim_role not in ROLE_HIERARCHY:
            sim_role = "admin"
        sim_level = ROLE_HIERARCHY[sim_role]
        sim_roles = [r for r in ROLE_ORDER if ROLE_HIERARCHY[r] <= sim_level]
        request.user_payload = {
            "apps": ["cockpit"],
            "roles_by_app": {"cockpit": sim_role},
            "global_roles": [],
            "roles": sim_roles,
            "app_role": sim_role,
            "is_super_admin": False,
            "firstname": "Bruce",
            "lastname": "WAYNE",
            "email": "bruce@wayneenterprise.com",
        }
        return None
    token = request.cookies.get("access_token")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    roles = payload.get("roles_by_app", {}).get(APP_KEY, "")
    if isinstance(roles, str):
        roles = [roles]
    max_level = max((ROLE_HIERARCHY.get(r, 0) for r in roles), default=0)
    # super_admin global = admin partout (meme regle que role_required)
    from app import SUPER_ADMIN_ROLE, request_admin_grant
    if SUPER_ADMIN_ROLE in (payload.get("global_roles") or []):
        max_level = ROLE_HIERARCHY.get("admin", 3)
    # Page d'administration accordee par un groupe (app.ADMIN_PAGE_REGISTRY)
    if max_level < ROLE_HIERARCHY.get("admin", 3) and not request_admin_grant(payload):
        return jsonify({"error": "Admin required"}), 403
    effective_role = "admin" if max_level >= ROLE_HIERARCHY.get("admin", 3) else roles[0] if roles else "user"
    payload["roles"] = [r for r in ROLE_ORDER if ROLE_HIERARCHY.get(r, 0) <= ROLE_HIERARCHY.get(effective_role, 0)]
    payload["app_role"] = effective_role
    request.user_payload = payload
    return None


@anpr_bp.before_request
def _before():
    err = _check_admin()
    if err:
        return err

# ---------------------------------------------------------------------------
# Hikvision vehicle_logo -> marque
# ---------------------------------------------------------------------------
# Source: Hikvision Vehicle Brand Dictionary v2 (579 brands)
BRAND_MAP = {
    1024: "Others", 1025: "AC Schnitzer", 1026: "Alfa Romeo",
    1027: "Aston Martin", 1028: "Audi", 1029: "La Joya",
    1030: "Porsche", 1031: "Buick", 1032: "BAIC",
    1033: "BAW", 1034: "BAIC Weiwang", 1035: "BAIC Yinxiang",
    1036: "Mercedes", 1037: "BMW", 1038: "Baojun",
    1039: "Baolong", 1040: "Bentley", 1041: "Brabus",
    1042: "Bugatti", 1043: "Honda", 1044: "Peugeot",
    1045: "BYD", 1046: "Changhe", 1047: "Changfeng Leopaard",
    1048: "Changcheng", 1049: "Changan Saloon", 1050: "DS",
    1051: "Southeast", 1053: "Volkswagen", 1054: "Dadi",
    1055: "Detroit Electric", 1056: "Dodge", 1057: "Dadi",
    1059: "Dafa", 1060: "Toyota", 1061: "Fuqi",
    1062: "Formasari", 1063: "Ferrari", 1064: "Ford",
    1066: "Foday", 1067: "Fiat", 1068: "Fisker",
    1069: "Mitsuoka", 1070: "Mercury", 1071: "Trumpchi",
    1073: "Guangsheng", 1074: "Qoros", 1075: "Huabei",
    1076: "Huapu", 1077: "Huatai", 1078: "Huafei",
    1079: "Hummer", 1080: "Haima", 1081: "Hongqi",
    1083: "Geely", 1084: "Jeep", 1085: "Jaguar",
    1086: "Jiangnan", 1088: "Chrysler", 1089: "Cadillac",
    1090: "Carlsson", 1091: "Kandi", 1092: "Koenigsegg",
    1093: "Lamborghini", 1094: "Lifan", 1095: "Rolls-Royce",
    1096: "Lincoln", 1097: "Linian", 1098: "Lotus",
    1099: "Lancia", 1100: "Lotus", 1101: "Land Rover",
    1102: "Suzuki", 1103: "Land Wind", 1104: "Lexus",
    1105: "Renault", 1106: "MG", 1107: "Mini",
    1108: "Maserati", 1109: "Meiya", 1110: "McLaren",
    1111: "Maybach", 1112: "Mazda", 1113: "Morgan",
    1114: "Luxgen", 1115: "Nanjing Jinlong", 1116: "Opel",
    1117: "Acura", 1118: "PGO", 1119: "Venucia",
    1120: "Chery", 1121: "Kia", 1122: "Qiantu",
    1123: "Nissan", 1124: "Riich", 1125: "Roewe",
    1126: "RUF", 1127: "Smart", 1128: "Mitsubishi",
    1129: "Maxus", 1130: "Spyker", 1131: "Shuanghuan",
    1132: "Shuanglong", 1133: "Subaru", 1134: "Skoda",
    1135: "Saab", 1136: "Ciimo", 1137: "Startech",
    1138: "Tianma", 1139: "Tesla", 1140: "TechArt",
    1141: "Denza", 1142: "Wiesmann", 1143: "Rely",
    1144: "Volvo", 1145: "Weichai Enranger", 1146: "Xinkai",
    1147: "Xin Da Di", 1148: "Soyat", 1149: "Hyundai",
    1150: "Seat", 1151: "Chevrolet", 1152: "Citroen",
    1154: "Jonway", 1155: "Eterniti", 1156: "Infiniti",
    1157: "Mustang", 1158: "Youxia", 1159: "Yogomo",
    1160: "Zhongxing", 1161: "Zhonghua", 1162: "ZK Huabei",
    1163: "Zotye", 1164: "Zhidou", 1165: "Kaiyi",
    1166: "Huasong", 1167: "Isuzu", 1168: "Borgward",
    1169: "Tongjia", 1170: "Hanjiang", 1171: "Zhinuo",
    1172: "GreenWheel", 1173: "Hanteng", 1174: "Levdeo",
    1175: "Changjiang", 1176: "SWM", 1177: "FQT Motor",
    1178: "Qoros", 1179: "JMC", 1180: "Bisu",
    1181: "Caky", 1182: "Haima", 1183: "Ourui",
    1537: "Ankai", 1538: "Ayvip", 1539: "Beijing Nongyong",
    1540: "Beiben", 1541: "North Bus", 1544: "Balong",
    1546: "Succeeded", 1547: "Changlong", 1548: "Chunlan Motor",
    1549: "Changan Commercial", 1552: "Dongfeng", 1554: "Daewoo",
    1555: "Dayun", 1556: "Dima", 1557: "Dongwo",
    1559: "Foton", 1561: "GMC", 1562: "GAC Gonow",
    1563: "Hino Light Truck", 1564: "Hino Heavy Truck", 1566: "CAMC",
    1568: "CHTC", 1569: "Hentong Bus", 1570: "Huizhong",
    1571: "Higer", 1573: "Haiou", 1574: "Hangtian Yuantong",
    1575: "Space Auto", 1576: "Huanghai", 1577: "Heibao",
    1578: "Jiulong", 1579: "JAC", 1580: "Jianghuan",
    1581: "JMC", 1582: "JMC", 1583: "Golden Dragon",
    1584: "Jinbei", 1585: "King Long", 1586: "Kama",
    1587: "Kawei", 1588: "Karry", 1590: "UAES",
    1592: "MAN", 1594: "Agricultural Vehicle", 1595: "Naveco",
    1596: "Nanjun", 1597: "Isuzu", 1598: "Youngman Bus",
    1599: "Sany Heavy Industry", 1600: "Tri-Ring Shitong", 1602: "Tricycle",
    1603: "Hongyan", 1604: "Shangrao Bus", 1605: "Shili Bus",
    1606: "Shaolin Bus", 1607: "Forland", 1608: "Shifeng",
    1609: "Sunwin", 1610: "Shenlong", 1611: "Shenye",
    1612: "Shuchi Bus", 1613: "Shaanxi Auto", 1614: "Scania",
    1615: "Tangjun", 1616: "Taihu Bus", 1618: "Tongxin Bus",
    1619: "Wanfeng", 1620: "Wuzheng", 1621: "SGMW",
    1622: "Wuyi", 1624: "Wuhuan", 1626: "Xugong",
    1629: "FAW", 1630: "Yaxing", 1631: "Iveco",
    1632: "Youyi Bus", 1633: "Yutong", 1634: "Yangzi",
    1635: "Yantai", 1636: "Yuejin", 1637: "Yingtian",
    1639: "CNHTC", 1641: "Zhongtong Bus", 1642: "Polarsun Motor",
    1643: "CDW", 1644: "Zonda", 1645: "Zonda",
    1646: "Jinggong Heavy Truck", 1647: "Wu Zhou Long", 1648: "Bus",
    1649: "Light Truck", 1650: "Heavy Truck", 1651: "Pickup Truck",
    1652: "Mudan", 1653: "Chufeng Motor", 1654: "Jijiang",
    1655: "SAIC Yizheng", 1656: "Yuexi", 1657: "Shenma",
    1658: "Jiangxi Xiaofang", 1659: "Shunfeng", 1660: "Hengshan",
    1674: "Dong Fang Hong Motor", 1675: "Neoplan", 1676: "Qingqi",
    1677: "Truck", 1678: "Special Vehicle", 1679: "Trailer",
    1681: "Wanda Bus", 1682: "Chang'an Suzuki", 1683: "Guilin",
    1684: "Sichuan Hyundai", 1685: "Aochi", 1686: "Denway Bus",
    1687: "FAW-Liut", 1688: "Wanxiang", 1690: "Sojen",
    1691: "Changan", 1692: "Zoomlion", 1693: "Yinlong",
    1694: "Jiachuan Auto", 1695: "Yixing", 1696: "Xi'an Silver Bus",
    1697: "Yangtse", 1698: "Suitong", 1701: "Qingdao Jiefang",
    1702: "ZTRV", 1703: "Wanda", 1704: "Shangrao",
    1705: "ZEV", 1706: "EVCRRC", 1707: "Zhongtong",
    1708: "Gonglu Bus", 1709: "BAIC", 1710: "Beifang",
    1711: "Neoplan", 1712: "Huachuan", 1713: "Youyi",
    1714: "Tongxin", 1715: "MG", 1716: "Jiachuan",
    1717: "Nvshen", 1718: "Shili", 1719: "Shaolin",
    1720: "Chuanjiao", 1721: "Chuanma", 1722: "GAC",
    1723: "Hino", 1724: "Kandi", 1725: "CHTC",
    1726: "Hentong", 1727: "Forta", 1728: "NLM",
    1729: "Chunlan", 1730: "Chufeng", 1731: "JMMC",
    1732: "JMC", 1733: "Seagull", 1734: "Mudan",
    1735: "Liebao", 1736: "Shenlong", 1737: "Forland",
    1738: "Hongxing", 1739: "Shuchi", 1740: "Shudu",
    1741: "Hengshan", 1742: "Yuexi", 1743: "Yuancheng",
    1744: "Golden Dragon", 1745: "Changan Oushang", 1746: "Youngman",
    1747: "Lynk & Co", 1748: "Feidie", 1749: "Feichi",
    1750: "Lishan", 1751: "Denway", 1752: "Nanjing Auto",
    1753: "Dahan", 1754: "Chunzhou", 1755: "Dearcc",
    1756: "Wanshan", 1757: "Central Europe Benz RV", 1758: "Yudo",
    1759: "Junma", 1760: "Guojin", 1761: "Weltmeister",
    1762: "Ora", 1763: "NIO", 1764: "Lada",
    1765: "Jetour", 1766: "Foro", 1767: "Hicom",
    1768: "JAC", 1769: "Jeep", 1770: "Jeep",
    1771: "Perodua", 1772: "UD", 1773: "Toyota",
    1774: "Toyota", 1775: "Isuzu", 1776: "Rohens",
    1777: "Beiben Heavy Truck", 1778: "SsangYong", 1779: "SsangYong",
    1780: "Haval", 1781: "Daihatsu", 1782: "Daewoo",
    1783: "Proton", 1784: "Proton", 1785: "Proton",
    1786: "Emgrand", 1787: "Hino", 1788: "Unknown",
    1789: "Kia", 1790: "Kia", 1791: "Kia Borrego",
    1792: "Alfa Romeo", 1793: "Equus", 1794: "Renault Samsung",
    1795: "Unknown", 1796: "Oushang", 1797: "Bonluck",
    1798: "Qiling", 1799: "Wanxiang", 1800: "Sate",
    1801: "FLM", 1802: "SRM Xinyuan", 1803: "Geometry",
    1804: "New Baojun", 1805: "Neta", 1806: "XPeng",
    1807: "Jetta", 1808: "Leading Ideal", 1809: "Baic Yunnan Ruili",
    1810: "R Marvel", 1811: "GAC Group", 1812: "SOL",
    1813: "Maple", 1814: "Celis", 1815: "Expedition",
    1816: "Leapmotor", 1817: "HiPhi", 1818: "Nissan",
    1819: "Novat", 1820: "Exeed", 1821: "Aiways",
    1822: "Fuda", 1823: "Hongqi", 1824: "Skyworth",
    1825: "Beijing Hyundai", 1826: "Zedriv", 1827: "Guangzhou Honda",
    1828: "Ouling", 1829: "Zhengzhou Nissan", 1830: "Changan Lincoln",
    1831: "Changan Auto", 1832: "FAW Linghe", 1833: "SGMW",
    1834: "Fxauto", 1835: "BAIC Off-Road", 1836: "Huachen Xinri",
    1837: "Hycan", 1838: "Dorcen", 1839: "Dayun Motor",
    1840: "Isuzu", 1841: "Sitech Dev", 1842: "JAC",
    1843: "Changan Kaicheng", 1844: "Artega", 1845: "Faralli Mazzanti",
    1846: "GTA", 1847: "KTM", 1848: "Lumma",
    1849: "Mini Coupe", 1850: "Noble", 1851: "Wey",
    1852: "Yamaha", 1853: "Beijing", 1854: "FAW Xiali",
    1855: "Besturn", 1856: "SAIC Tangshan Bus", 1857: "SAIC Maxus",
    1858: "SAIC Hongyan", 1859: "CNHTC Wangpai", 1860: "Toyota Crown",
    1861: "Leshi", 1862: "PGO", 1863: "Lingbao",
    1864: "Lifan Junma", 1865: "Lorinser", 1866: "BAIC Ruixiang",
    1867: "NAC Changda", 1868: "Geely Gleagle", 1869: "Geely Emgrand",
    1870: "Geely Englon", 1871: "Taihu", 1872: "Lantu",
    1873: "Pagani", 1874: "Guangma", 1875: "Hengrui Auto",
    1876: "Genesis", 1877: "Man", 1878: "Ranz",
    1879: "Songsan", 1880: "Polestar", 1881: "Zeekr",
    1882: "Arcfox", 1883: "BYD Yuan", 1884: "BYD Tang",
    1885: "BYD Song", 1886: "BYD Han", 1887: "BYD Qin",
    1888: "Bike", 1889: "Weichai", 1890: "Ford Mustang",
    1891: "Koenigsegg", 1892: "Yulu", 1893: "Saleen",
    1894: "Mansory", 1895: "Suda", 1896: "Mustang EV",
    1897: "GWM Huaguan", 1898: "LongRiver EV", 1899: "IAT",
    1900: "Feifan", 1901: "LinkTour", 1902: "Feishen",
    1903: "Qilu", 1904: "Apollo", 1905: "Caterham",
    1906: "Conquest", 1907: "Dacia", 1908: "Zenvo",
    1909: "BAIC Lite", 1910: "AUX", 1911: "Proton",
    1912: "Seat", 1913: "Bluecar", 1914: "Noma",
    1915: "Suzuki", 1916: "Tankar", 1917: "Valle",
    1918: "Veiculo Longo", 1919: "Tata", 1920: "Ashok Leyland",
    1921: "Mahindra", 1922: "Eicher", 1923: "BharatBenz",
    1924: "Force Motors", 1925: "SML Isuzu", 1926: "MAN Trucks",
    1927: "Pocco", 1928: "Aston Martin", 1929: "Yogomo",
    1930: "BAIC Huansu", 1931: "Dongfeng Huashen", 1932: "DMC",
    1933: "Dongfeng Fengshen", 1934: "CNHTC Haoman", 1935: "Dorcen",
    1936: "Nanjun Bus", 1937: "Hyundai Truck & Bus", 1938: "Shacman Commercial",
    1939: "Shacman Light Truck", 1940: "C&C Trucks", 1941: "Horki",
    1942: "Oulang", 1943: "Aston Martin", 1944: "GWM Haval",
    1945: "Shacman Heavy Truck", 1946: "Diandongwu", 1947: "Dongfeng Chenglong",
    1948: "GWM Wey", 1950: "GAC Aion", 1951: "FAW Jiefang",
    1953: "Skyworth", 1954: "Xinyuan", 1956: "Feifan",
    1957: "Dongfeng Fukang", 1958: "Geely Jialong", 1959: "Dongfeng Ruitaite",
    1960: "AC Schnitzer", 1961: "Hennessey", 1962: "FAW Jilin",
    1963: "CNHTC Shandeka", 1964: "FAW Hongta", 1965: "Dongfeng Xiaokang",
    1966: "FAW General Motors", 1967: "Dongfeng Fengguang", 1968: "Foton Rowor",
    1969: "Dongfeng Fengdu", 1970: "FAW Jiefang Light Truck", 1971: "Scion",
    1972: "Jijiang Bus", 1973: "Smart", 1974: "Beijing",
    1975: "Aito", 1976: "Neta", 1977: "Radar",
    1978: "Weltmeister", 1979: "Dark Blue", 1980: "IM",
    1981: "New Gonow", 1982: "Ruilan", 1983: "Radar",
    1984: "DFPV", 1985: "Tank", 1986: "Modern",
    1987: "Gleagle", 1988: "Ruichi EV", 1989: "Avatr",
    1990: "Fuso", 1991: "Bedford", 1992: "Sojen",
    1993: "Honda", 1994: "BAIC BJEV", 1995: "Hino",
    1996: "Mitsubishi", 1997: "Lexus", 1998: "Chery New Energy",
    1999: "Mazda", 2000: "BMW", 2001: "Tesla",
    2002: "Toyota", 2003: "Mercedes", 2004: "Geely Yinhe",
}

VEHICLE_TYPE_LABELS = {
    "vehicle": "Voiture",
    "SUVMPV": "SUV/Monospace",
    "truck": "Camion",
    "bus": "Bus",
    "van": "Utilitaire",
    "pickupTruck": "Pick-up",
    "buggy": "Buggy",
}

COLOR_HEX = {
    "white": "#f0f0f0", "black": "#1a1a2e", "gray": "#6b7280",
    "blue": "#3b82f6", "red": "#ef4444", "green": "#22c55e",
    "yellow": "#eab308", "brown": "#92400e", "pink": "#ec4899",
    "cyan": "#06b6d4",
    # Couleurs francaises (Gemini Vision)
    "blanc": "#f0f0f0", "noir": "#1a1a2e", "gris": "#6b7280",
    "bleu": "#3b82f6", "rouge": "#ef4444", "vert": "#22c55e",
    "jaune": "#eab308", "marron": "#92400e", "rose": "#ec4899",
    "orange": "#f97316", "beige": "#d4a76a", "bordeaux": "#800020",
    "argent": "#c0c0c0",
}

COLOR_FR = {
    "white": "Blanc", "black": "Noir", "gray": "Gris", "blue": "Bleu",
    "red": "Rouge", "green": "Vert", "yellow": "Jaune", "brown": "Marron",
    "pink": "Rose", "cyan": "Cyan", "purple": "Violet",
}

# ---------------------------------------------------------------------------
# Reglages de la page de controle
# ---------------------------------------------------------------------------
PARIS = ZoneInfo("Europe/Paris")
HIK_IMAGE_DIR = "E:/TITAN/production/hik_images"
# Vignettes : dossier "_t" a cote de l'image d'origine, donc dans le dossier du
# jour (purge par ecoutehik2 avec lui). Les originaux font ~800 Ko.
THUMB_DIR_NAME = "_t"
THUMB_BOX = (480, 360)

# Sante des voies. Une voie sans lecture depuis LANE_MUTE_MIN est testee sur le
# reseau (connexion TCP au port web de la camera, sans identifiant) :
#   - ne repond pas                   -> "offline" (alimentation, cable, switch)
#   - repond, d'autres voies lisent   -> "noread"  (camera en ligne qui n'envoie
#                                        plus rien : configuration a verifier)
#   - repond, aucune voie ne lit      -> "idle"    (nuit, circuit ferme)
LANE_OK_MIN = 15
LANE_MUTE_MIN = 30
# Test reseau : au plus une fois par minute et par camera, seulement quand la
# page est ouverte (appel de la vue d'ensemble) et seulement pour les voies
# silencieuses.
PROBE_PORT = 80
PROBE_TIMEOUT_S = 1.0
PROBE_TTL_S = 60
# Taux d'illisibles au-dela duquel la lecture de la voie est signalee degradee
LANE_UNREAD_WARN = 0.30
LANE_UNREAD_MIN_N = 20

# Periodes proposees : pas des barres d'activite
PERIODS = {
    "1h": timedelta(minutes=5),
    "today": timedelta(hours=1),
    "24h": timedelta(hours=1),
    "7d": timedelta(hours=6),
}

VISION_CACHE_TTL_S = 120

# Confusions courantes de lecture (recherche tolerante)
_OCR_TWINS = {
    "0": "0ODQ", "O": "0ODQ", "D": "D0O", "Q": "Q0O",
    "1": "1I", "I": "1I",
    "8": "8B", "B": "B8",
    "5": "5S", "S": "S5",
    "2": "2Z", "Z": "Z2",
    "6": "6G", "G": "G6",
}

# ---------------------------------------------------------------------------
# MongoDB (lazy init)
# ---------------------------------------------------------------------------
_db = None
_col_anpr = None
_fs = None
_col_camera_config = None
_col_site_counter = None
_col_vision_imm = None
_col_vision_bl = None
_col_vision_cfg = None
_col_watchlist = None


def _ensure_db():
    global _db, _col_anpr, _fs, _col_camera_config, _col_site_counter
    global _col_vision_imm, _col_vision_bl, _col_vision_cfg, _col_watchlist
    if _db is not None:
        return
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    titan_env = os.getenv("TITAN_ENV", "dev").strip().lower()
    db_name = "titan" if titan_env in {"prod", "production"} else "titan_dev"
    client = MongoClient(uri)
    db = client[db_name]
    _col_anpr = db["hik_anpr"]
    _fs = gridfs.GridFS(db, collection="hik_images")
    _col_camera_config = db["anpr_camera_config"]
    _col_site_counter = db["anpr_site_counter"]
    _col_vision_imm = db["vision_immatriculations"]
    _col_vision_bl = db["vision_blacklist"]
    _col_vision_cfg = db["vision_config"]
    _col_watchlist = db["cockpit_anpr_watchlist"]

    _col_anpr.create_index([("event_dt", DESCENDING)])
    _col_anpr.create_index([("license_plate", 1)])
    _col_anpr.create_index([("camera_path", 1), ("event_dt", DESCENDING)])
    # Flux en direct : nouveaux passages par ordre de reception
    _col_anpr.create_index([("received_at", DESCENDING)])
    _col_camera_config.create_index("camera_path", unique=True)
    _db = db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(dt):
    """Datetime Mongo (naif = UTC) -> datetime UTC avec fuseau."""
    if not isinstance(dt, datetime):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt):
    dt = _utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _parse_iso(value):
    """ISO -> UTC. Une heure sans fuseau est une heure de Paris (saisie)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PARIS)
    return dt.astimezone(timezone.utc)


def _norm_plate(plate):
    """Plaque normalisee : majuscules, lettres et chiffres seulement."""
    return re.sub(r"[^A-Z0-9]", "", (plate or "").strip().upper())


def _tolerant_regex(norm, anchored=False):
    """Motif tolerant aux confusions de lecture (0/O/D, 1/I, 8/B...)."""
    body = "".join(
        "[" + _OCR_TWINS[ch] + "]" if ch in _OCR_TWINS else re.escape(ch)
        for ch in norm
    )
    return "^" + body + "$" if anchored else body


def _brand(logo_id):
    name = BRAND_MAP.get(logo_id or 0, "")
    return "" if name in ("Others", "Unknown") else name


def _default_label(path):
    return path.replace("/", "").replace("lapi", "LAPI ").strip() or path


def _resolve_dir(raw, forward_role):
    if raw == "forward":
        return "entry" if forward_role == "entry" else "exit"
    if raw == "reverse":
        return "exit" if forward_role == "entry" else "entry"
    return "unknown"


def _raw_dir(target, forward_role):
    """Direction brute de la camera correspondant a entry/exit."""
    if target == "entry":
        return "forward" if forward_role == "entry" else "reverse"
    return "reverse" if forward_role == "entry" else "forward"


def _cameras():
    """Cameras connues (donnees + configuration), triees par chemin."""
    cfgs = {c["camera_path"]: c for c in _col_camera_config.find()}
    paths = set(cfgs) | {p for p in _col_anpr.distinct("camera_path") if p}
    out = []
    for path in sorted(paths):
        cfg = cfgs.get(path, {})
        out.append({
            "path": path,
            "label": cfg.get("label") or _default_label(path),
            "forward_role": cfg.get("forward_role", "entry"),
            "enabled": cfg.get("enabled", True),
            "lieu": cfg.get("lieu", ""),
        })
    return out


def _dir_conditions(cams, target, camera=None):
    return [
        {"camera_path": c["path"], "direction": _raw_dir(target, c["forward_role"])}
        for c in cams if not camera or c["path"] == camera
    ]


def _period(code, day=None):
    """(code, debut UTC aligne sur le pas, fin UTC, pas, nombre de barres).

    code "day" + day "AAAA-MM-JJ" : journee de Paris (consultation d'un jour passe).
    """
    now = datetime.now(timezone.utc)
    if code == "day":
        try:
            d = datetime.strptime(day or "", "%Y-%m-%d").replace(tzinfo=PARIS)
        except ValueError:
            code = "today"
        else:
            start = d.astimezone(timezone.utc)
            end = (d + timedelta(days=1)).astimezone(timezone.utc)
            return "day", start, min(end, now), timedelta(hours=1), 24
    if code not in PERIODS:
        code = "today"
    step = PERIODS[code]
    if code == "1h":
        start = now - timedelta(hours=1)
        start = start.replace(minute=start.minute - start.minute % 5, second=0, microsecond=0)
    elif code == "24h":
        start = (now - timedelta(hours=24)).replace(minute=0, second=0, microsecond=0)
    elif code == "7d":
        start = (now - timedelta(days=7)).replace(minute=0, second=0, microsecond=0)
        start -= timedelta(hours=start.hour % 6)
    else:
        start = datetime.now(PARIS).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    bars = max(1, int(-(-(now - start) // step)))
    return code, start, now, step, bars


def _watch_map():
    """{plaque normalisee: entree de watchlist}."""
    out = {}
    for w in _col_watchlist.find({}, {"plate": 1, "label": 1, "enabled": 1}):
        norm = _norm_plate(w.get("plate"))
        if norm:
            out[norm] = {"id": str(w["_id"]), "label": w.get("label", ""),
                         "enabled": bool(w.get("enabled", True))}
    return out


_probe_cache = {}  # ip -> (instant monotonic, joignable, horodatage UTC du test)


def _probe_one(ip):
    try:
        with socket.create_connection((ip, PROBE_PORT), timeout=PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def _reachability(ips):
    """{ip: (joignable, horodatage)} ; tests en parallele, resultat garde PROBE_TTL_S."""
    now_m = time.monotonic()
    stale = [ip for ip in set(ips) if ip and (ip not in _probe_cache
                                             or now_m - _probe_cache[ip][0] >= PROBE_TTL_S)]
    if stale:
        with ThreadPoolExecutor(max_workers=min(8, len(stale))) as pool:
            results = list(pool.map(_probe_one, stale))
        at = datetime.now(timezone.utc)
        for ip, ok in zip(stale, results):
            _probe_cache[ip] = (time.monotonic(), ok, at)
    return {ip: (_probe_cache[ip][1], _probe_cache[ip][2]) for ip in ips if ip in _probe_cache}


_vision_cache = {"at": 0.0, "key": None, "event": None, "map": {}, "seen": 0}


def _vision_active():
    cfg = _col_vision_cfg.find_one({"_id": "current"})
    if not cfg or not cfg.get("evenement"):
        return None
    return {"evenement": cfg["evenement"], "annee": cfg.get("annee")}


def _vision_map():
    """(evenement actif, {plaque: {lieu, billets}}, plaques deja lues par la LAPI).

    Cache court : ~10 000 fiches par evenement, lues au plus toutes les 2 min.
    """
    event = _vision_active()
    key = (event["evenement"], event["annee"]) if event else None
    c = _vision_cache
    if c["key"] == key and time.monotonic() - c["at"] < VISION_CACHE_TTL_S:
        return c["event"], c["map"], c["seen"]
    vmap = {}
    seen = 0
    if event:
        for d in _col_vision_imm.find(event, {"plaque_norm": 1, "lieu": 1, "billets": 1}):
            norm = d.get("plaque_norm")
            if norm:
                vmap[norm] = {"lieu": d.get("lieu", ""), "billets": len(d.get("billets") or [])}
        if vmap:
            seen = len(_col_anpr.distinct("license_plate", {"license_plate": {"$in": list(vmap)}}))
    _vision_cache.update({"at": time.monotonic(), "key": key, "event": event, "map": vmap, "seen": seen})
    return event, vmap, seen


def _image_urls(doc):
    path = doc.get("vehicle_image_path")
    if path:
        q = quote(path)
        return "/api/anpr/image/" + q, "/api/anpr/thumb/" + q
    oid = doc.get("vehicle_image_id")
    if oid:
        url = "/api/anpr/image/" + str(oid)
        return url, url
    return None, None


def _item(doc, cams_by_path, watch, vmap):
    """Passage LAPI pour l'interface."""
    plate = doc.get("license_plate", "")
    norm = _norm_plate(plate) if plate != "UNKNOWN" else ""
    path = doc.get("camera_path", "")
    cam = cams_by_path.get(path, {})
    color = doc.get("vehicle_color", "")
    vtype = doc.get("vehicle_type", "")
    img, thumb = _image_urls(doc)
    plate_img = doc.get("plate_image_path")
    return {
        "id": str(doc["_id"]),
        "plate": plate,
        "unread": plate == "UNKNOWN",
        "conf": doc.get("confidence", 0) or 0,
        "color": color,
        "color_hex": COLOR_HEX.get(color, ""),
        "color_label": COLOR_FR.get(color, ""),
        "brand": _brand(doc.get("vehicle_logo")),
        "type": VEHICLE_TYPE_LABELS.get(vtype, vtype if vtype != "unknown" else ""),
        "camera": path,
        "cam_label": cam.get("label") or _default_label(path),
        "dir": _resolve_dir(doc.get("direction", ""), cam.get("forward_role", "entry")),
        "dt": _iso(doc.get("event_dt")),
        "allow": doc.get("vehicle_list_name") == "allowList",
        "img": img,
        "thumb": thumb,
        "plate_img": "/api/anpr/image/" + quote(plate_img) if plate_img else None,
        "watch": watch.get(norm) if norm else None,
        "vision": vmap.get(norm) if norm else None,
    }


def _safe_image_path(rel):
    safe = os.path.normpath(rel)
    if ".." in safe or os.path.isabs(safe):
        return None
    full = os.path.abspath(os.path.join(HIK_IMAGE_DIR, safe))
    if not full.startswith(os.path.abspath(HIK_IMAGE_DIR) + os.sep):
        return None
    return full


def _blank_png():
    return Response(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
        mimetype="image/png",
        status=404,
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@anpr_bp.route("/anpr")
def anpr_page():
    payload = getattr(request, "user_payload", {})
    return render_template(
        "anpr.html",
        user_roles=payload.get("roles", []),
        user_firstname=payload.get("firstname", ""),
        user_lastname=payload.get("lastname", ""),
        user_email=payload.get("email", ""),
    )


# ---------------------------------------------------------------------------
# Vue d'ensemble : sante des voies, flux, sur site, plaques surveillees
# ---------------------------------------------------------------------------

@anpr_bp.route("/api/anpr/overview")
def anpr_overview():
    _ensure_db()
    code, start, end, step, bars = _period(request.args.get("period", "today"), request.args.get("date"))
    now = datetime.now(timezone.utc)
    # Pas de borne haute en direct : l'horloge d'une camera peut avancer un peu
    window = {"$gte": start, "$lt": end} if code == "day" else {"$gte": start}
    cams = _cameras()
    by_path = {c["path"]: c for c in cams}

    if step < timedelta(hours=1):
        trunc = {"$dateTrunc": {"date": "$event_dt", "unit": "minute", "binSize": 5}}
    else:
        trunc = {"$dateTrunc": {"date": "$event_dt", "unit": "hour"}}

    def blank():
        return {"count": 0, "unread": 0, "entries": 0, "exits": 0, "spark": [0] * bars}

    lanes = {c["path"]: blank() for c in cams}
    flow_in, flow_out = [0] * bars, [0] * bars
    for r in _col_anpr.aggregate([
        {"$match": {"event_dt": window}},
        {"$group": {
            "_id": {"c": "$camera_path", "d": "$direction",
                    "u": {"$eq": ["$license_plate", "UNKNOWN"]}, "t": trunc},
            "n": {"$sum": 1},
        }},
    ]):
        k, n = r["_id"], r["n"]
        lane = lanes.setdefault(k["c"], blank())
        lane["count"] += n
        if k["u"]:
            lane["unread"] += n
        d = _resolve_dir(k["d"], by_path.get(k["c"], {}).get("forward_role", "entry"))
        if d == "entry":
            lane["entries"] += n
        elif d == "exit":
            lane["exits"] += n
        t = _utc(k["t"])
        i = min(bars - 1, max(0, int((t - start) // step))) if t else 0
        lane["spark"][i] += n
        if d == "entry":
            flow_in[i] += n
        elif d == "exit":
            flow_out[i] += n

    # Derniere lecture par voie (index camera_path + event_dt)
    last = {}
    for c in cams:
        doc = _col_anpr.find_one({"camera_path": c["path"]}, sort=[("event_dt", DESCENDING)],
                                 projection={"event_dt": 1, "ip_address": 1})
        plate_doc = _col_anpr.find_one({"camera_path": c["path"], "license_plate": {"$ne": "UNKNOWN"}},
                                       sort=[("event_dt", DESCENDING)],
                                       projection={"event_dt": 1, "license_plate": 1})
        last[c["path"]] = (_utc(doc.get("event_dt")) if doc else None,
                           plate_doc.get("license_plate") if plate_doc else "",
                           (doc or {}).get("ip_address", ""))

    def age_min(path):
        dt = last.get(path, (None, "", ""))[0]
        return (now - dt).total_seconds() / 60 if dt else None

    def silent(path):
        age = age_min(path)
        return age is None or age > LANE_MUTE_MIN

    # Test reseau des seules cameras silencieuses (adresse IP lue dans leurs passages)
    reach = _reachability([last[c["path"]][2] for c in cams if c["enabled"] and silent(c["path"])])

    lane_list = []
    for c in cams:
        if not c["enabled"]:
            continue
        s = lanes.get(c["path"], blank())
        age = age_min(c["path"])
        ip = last[c["path"]][2]
        others_active = any(
            not silent(o["path"]) for o in cams if o["enabled"] and o["path"] != c["path"]
        )
        probe = reach.get(ip) if silent(c["path"]) else None
        if age is not None and age <= LANE_OK_MIN:
            status = "ok"
        elif probe is not None and not probe[0]:
            status = "offline"
        elif silent(c["path"]) and others_active:
            status = "noread"
        else:
            status = "idle"
        unread_rate = s["unread"] / s["count"] if s["count"] else 0
        lane_list.append({
            "path": c["path"], "label": c["label"], "status": status, "ip": ip,
            "reach": {"ok": probe[0], "at": _iso(probe[1])} if probe else None,
            "last_dt": _iso(last[c["path"]][0]), "last_plate": last[c["path"]][1],
            "count": s["count"], "unread": s["unread"],
            "read_warn": s["count"] >= LANE_UNREAD_MIN_N and unread_rate > LANE_UNREAD_WARN,
            "entries": s["entries"], "exits": s["exits"], "spark": s["spark"],
        })

    # Plaques de la periode : uniques, surveillees, connues de Vision
    watch = _watch_map()
    vision_event, vmap, vision_seen_all = _vision_map()
    plates = list(_col_anpr.aggregate([
        {"$match": {"event_dt": window, "license_plate": {"$ne": "UNKNOWN"}}},
        {"$group": {"_id": "$license_plate", "n": {"$sum": 1}, "last": {"$max": "$event_dt"}}},
    ]))
    watch_hits = []
    vision_seen = 0
    for p in plates:
        norm = _norm_plate(p["_id"])
        if norm in vmap:
            vision_seen += 1
        w = watch.get(norm)
        if w and w["enabled"]:
            watch_hits.append({"plate": p["_id"], "label": w["label"], "n": p["n"], "last": _iso(p["last"])})
    watch_hits.sort(key=lambda h: h["last"] or "", reverse=True)

    # Repartition des vehicules de la periode
    facets = next(_col_anpr.aggregate([
        {"$match": {"event_dt": window, "license_plate": {"$ne": "UNKNOWN"}}},
        {"$facet": {
            "brands": [{"$group": {"_id": "$vehicle_logo", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 12}],
            "types": [{"$group": {"_id": "$vehicle_type", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
            "colors": [{"$group": {"_id": "$vehicle_color", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
        }},
    ]), {"brands": [], "types": [], "colors": []})
    brands = [{"label": _brand(b["_id"]), "n": b["n"]} for b in facets["brands"] if _brand(b["_id"])][:6]
    types = [{"label": VEHICLE_TYPE_LABELS.get(t["_id"], t["_id"]), "n": t["n"]}
             for t in facets["types"] if t["_id"] and t["_id"] != "unknown"]
    colors = [{"label": COLOR_FR.get(c["_id"], c["_id"]), "hex": COLOR_HEX.get(c["_id"], "#888"), "n": c["n"]}
              for c in facets["colors"] if c["_id"] and c["_id"] != "unknown"]

    # Vehicules sur site : entrees - sorties depuis la remise a zero
    reset = _col_site_counter.find_one({"_id": "reset"})
    reset_at = _utc(reset["reset_at"]) if reset else datetime(2000, 1, 1, tzinfo=timezone.utc)
    on_in = on_out = 0
    for r in _col_anpr.aggregate([
        {"$match": {"event_dt": {"$gte": reset_at}}},
        {"$group": {"_id": {"c": "$camera_path", "d": "$direction"}, "n": {"$sum": 1}}},
    ]):
        d = _resolve_dir(r["_id"]["d"], by_path.get(r["_id"]["c"], {}).get("forward_role", "entry"))
        if d == "entry":
            on_in += r["n"]
        elif d == "exit":
            on_out += r["n"]

    total = sum(l["count"] for l in lanes.values())
    unread = sum(l["unread"] for l in lanes.values())
    return jsonify({
        "now": _iso(now),
        "period": {"code": code, "from": _iso(start), "to": _iso(end),
                   "step_min": int(step.total_seconds() // 60), "bars": bars},
        "lanes": lane_list,
        "totals": {
            "passages": total, "unread": unread, "plates": len(plates),
            "entries": sum(flow_in), "exits": sum(flow_out),
        },
        "flow": {"in": flow_in, "out": flow_out},
        "onsite": {"value": max(0, on_in - on_out), "entries": on_in, "exits": on_out, "reset_at": _iso(reset_at)},
        "watch": {"listed": sum(1 for w in watch.values() if w["enabled"]), "hits": watch_hits[:20]},
        "vision": {
            "event": vision_event, "fiches": len(vmap),
            "seen_period": vision_seen, "seen_all": vision_seen_all,
        },
        "mix": {"brands": brands, "types": types, "colors": colors},
    })


# ---------------------------------------------------------------------------
# Passages : flux en direct, filtres, recherche (pagination par curseur)
# ---------------------------------------------------------------------------

@anpr_bp.route("/api/anpr/passages")
def anpr_passages():
    _ensure_db()
    a = request.args
    cams = _cameras()
    by_path = {c["path"]: c for c in cams}
    watch = _watch_map()
    _event, vmap, _seen = _vision_map()

    query = {}
    conds = []
    q = _norm_plate(a.get("q", ""))
    if q:
        # Recherche : tout l'historique, tolerante aux confusions de lecture
        query["license_plate"] = {"$regex": _tolerant_regex(q)}
    else:
        code, start, end, _step, _bars = _period(a.get("period", "today"), a.get("date"))
        query["event_dt"] = {"$gte": start, "$lt": end} if code == "day" else {"$gte": start}
        query["license_plate"] = "UNKNOWN" if a.get("unread") == "1" else {"$ne": "UNKNOWN"}

    camera = a.get("camera", "").strip()
    if camera:
        query["camera_path"] = camera
    direction = a.get("dir", "")
    if direction in ("entry", "exit"):
        conds.append({"$or": _dir_conditions(cams, direction, camera or None) or [{"_id": None}]})
    if a.get("watch") == "1":
        conds.append({"license_plate": {"$in": [p for p, w in watch.items() if w["enabled"]]}})
    if a.get("vision") == "1":
        conds.append({"license_plate": {"$in": list(vmap)}})

    base = dict(query)
    if conds:
        base["$and"] = list(conds)

    try:
        limit = max(1, min(200, int(a.get("limit", 60))))
    except ValueError:
        limit = 60

    after = _parse_iso(a.get("after", ""))
    before = a.get("before", "")
    final = dict(base)
    extra = []
    sort = [("event_dt", DESCENDING), ("_id", DESCENDING)]
    if after:
        # Direct : passages recus apres le dernier vu
        extra.append({"received_at": {"$gt": after}})
        sort = [("received_at", DESCENDING)]
    elif before:
        b_dt, _, b_id = before.partition("|")
        b_dt = _parse_iso(b_dt)
        try:
            b_oid = ObjectId(b_id)
        except Exception:
            b_oid = None
        if b_dt and b_oid:
            extra.append({"$or": [{"event_dt": {"$lt": b_dt}}, {"event_dt": b_dt, "_id": {"$lt": b_oid}}]})
    if extra:
        final["$and"] = list(conds) + extra

    docs = list(_col_anpr.find(final).sort(sort).limit(limit + 1))
    has_more = len(docs) > limit and not after
    docs = docs[:limit]

    total = None
    if not after and not before:
        try:
            total = _col_anpr.count_documents(base, maxTimeMS=4000)
        except Exception:
            total = None

    received = [_utc(d.get("received_at")) for d in docs if d.get("received_at")]
    received_max = max(received) if received else None
    if not after and received_max is None:
        received_max = datetime.now(timezone.utc)

    nxt = None
    if has_more and docs:
        last_doc = docs[-1]
        nxt = "%s|%s" % (_iso(last_doc.get("event_dt")), last_doc["_id"])

    return jsonify({
        "items": [_item(d, by_path, watch, vmap) for d in docs],
        "total": total,
        "next": nxt,
        "received_max": _iso(received_max),
    })


# ---------------------------------------------------------------------------
# Fiche plaque : passages, sejours, lectures proches, Vision, watchlist
# ---------------------------------------------------------------------------

@anpr_bp.route("/api/anpr/plate/<path:plate>")
def anpr_plate(plate):
    _ensure_db()
    raw = (plate or "").strip().upper()
    norm = _norm_plate(raw)
    if not norm:
        return jsonify({"error": "Plaque invalide"}), 400
    cams = _cameras()
    by_path = {c["path"]: c for c in cams}
    watch = _watch_map()
    _event, vmap, _seen = _vision_map()

    match = {"license_plate": {"$in": sorted({raw, norm})}}
    count = _col_anpr.count_documents(match)
    docs = list(_col_anpr.find(match).sort("event_dt", DESCENDING).limit(300))
    items = [_item(d, by_path, watch, vmap) for d in docs]

    # Sejours : une entree ouvre, la sortie suivante ferme
    stays = []
    open_in = None
    for it in reversed(items):
        if it["dir"] == "entry":
            if open_in:
                stays.append({"in": open_in, "out": None})
            open_in = it["dt"]
        elif it["dir"] == "exit":
            stays.append({"in": open_in, "out": it["dt"]})
            open_in = None
    if open_in:
        stays.append({"in": open_in, "out": None, "open": True})
    stays.reverse()

    # Lectures proches (meme longueur, confusions de lecture), hors la plaque elle-meme
    similar = []
    if len(norm) >= 5:
        rx = _tolerant_regex(norm, anchored=True)
        for r in _col_anpr.aggregate([
            {"$match": {"license_plate": {"$regex": rx}}},
            {"$group": {"_id": "$license_plate", "n": {"$sum": 1}, "last": {"$max": "$event_dt"}}},
            {"$sort": {"n": -1}},
            {"$limit": 9},
        ]):
            if _norm_plate(r["_id"]) != norm:
                similar.append({"plate": r["_id"], "n": r["n"], "last": _iso(r["last"])})

    vision = []
    for v in _col_vision_imm.find({"plaque_norm": norm}).sort("date", DESCENDING).limit(10):
        vision.append({
            "evenement": v.get("evenement", ""), "annee": v.get("annee"),
            "lieu": v.get("lieu", ""), "billets": v.get("billets") or [],
            "date": v.get("date", ""), "marque": v.get("marque", ""),
            "modele": v.get("modele", ""), "couleur": v.get("couleur", ""),
            "commentaire": v.get("commentaire", ""),
            "photo": v.get("photo_vehicule", ""),
        })
    bl = _col_vision_bl.find_one({"plaque_norm": norm})

    by_cam = {}
    for it in items:
        by_cam[it["cam_label"]] = by_cam.get(it["cam_label"], 0) + 1

    return jsonify({
        "plate": docs[0]["license_plate"] if docs else raw,
        "norm": norm,
        "count": count,
        "first": items[-1]["dt"] if items else None,
        "last": items[0]["dt"] if items else None,
        "on_site": bool(items) and items[0]["dir"] == "entry",
        "by_camera": by_cam,
        "items": items,
        "stays": stays[:30],
        "similar": similar[:8],
        "vision": vision,
        "blacklist": {"raison": bl.get("raison", ""), "date_ajout": bl.get("date_ajout", "")} if bl else None,
        "watch": watch.get(norm),
    })


# ---------------------------------------------------------------------------
# Images : originale (disque ou ancien GridFS) et vignette
# ---------------------------------------------------------------------------

@anpr_bp.route("/api/anpr/image/<path:image_ref>")
def anpr_image(image_ref):
    """Sert une image : chemin disque (nouveau) ou ObjectId GridFS (ancien)."""
    _ensure_db()
    if "/" in image_ref:
        full = _safe_image_path(image_ref)
        if full and os.path.isfile(full):
            return send_file(full, mimetype="image/jpeg", max_age=86400)
    else:
        try:
            grid_file = _fs.get(ObjectId(image_ref))
            return Response(grid_file.read(), mimetype="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})
        except Exception:
            pass
    return _blank_png()


@anpr_bp.route("/api/anpr/thumb/<path:rel>")
def anpr_thumb(rel):
    """Vignette JPEG (~25 Ko) generee a la demande puis gardee sur disque."""
    full = _safe_image_path(rel)
    if not full or not os.path.isfile(full):
        return _blank_png()
    folder, name = os.path.split(full)
    thumb_dir = os.path.join(folder, THUMB_DIR_NAME)
    thumb = os.path.join(thumb_dir, name)
    if not os.path.isfile(thumb):
        try:
            from PIL import Image
            os.makedirs(thumb_dir, exist_ok=True)
            with Image.open(full) as im:
                im.draft("RGB", (THUMB_BOX[0] * 2, THUMB_BOX[1] * 2))
                small = im.convert("RGB")
                small.thumbnail(THUMB_BOX)
                tmp = "%s.%s.tmp" % (thumb, os.urandom(4).hex())
                small.save(tmp, "JPEG", quality=72, optimize=True)
                os.replace(tmp, thumb)
        except Exception as e:
            logger.warning("Vignette LAPI impossible pour %s : %s", rel, e)
            return send_file(full, mimetype="image/jpeg", max_age=86400)
    return send_file(thumb, mimetype="image/jpeg", max_age=7 * 86400)


# ---------------------------------------------------------------------------
# Configuration des cameras : page Cameras (cameras.py, usage LAPI), qui
# tient anpr_camera_config a jour (chemin, libelle, sens, parking, affichage).
# ---------------------------------------------------------------------------

@anpr_bp.route("/api/anpr/onsite/reset", methods=["POST"])
def anpr_onsite_reset():
    """Remet le compteur de vehicules sur site a zero (horodatage courant)."""
    _ensure_db()
    now = datetime.now(timezone.utc)
    _col_site_counter.update_one({"_id": "reset"}, {"$set": {"reset_at": now}}, upsert=True)
    return jsonify({"ok": True, "reset_at": _iso(now)})
