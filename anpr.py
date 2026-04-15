# anpr.py -- Blueprint LAPI / ANPR (Lecture Automatique de Plaques)
import os
import re
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request, render_template, Response, send_file, abort
from pymongo import MongoClient, DESCENDING, ASCENDING
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
    if max_level < ROLE_HIERARCHY.get("admin", 3):
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


def _ensure_db():
    global _db, _col_anpr, _fs, _col_camera_config, _col_site_counter
    global _col_vision_imm, _col_vision_bl, _col_vision_cfg
    if _db is not None:
        return
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    titan_env = os.getenv("TITAN_ENV", "dev").strip().lower()
    db_name = "titan" if titan_env in {"prod", "production"} else "titan_dev"
    client = MongoClient(uri)
    dev_mode = os.getenv("TITAN_ENV", "dev") != "prod"
    _db = client["titan_dev" if dev_mode else "titan"]
    _col_anpr = _db["hik_anpr"]
    _fs = gridfs.GridFS(_db, collection="hik_images")
    _col_camera_config = _db["anpr_camera_config"]
    _col_site_counter = _db["anpr_site_counter"]
    _col_vision_imm = _db["vision_immatriculations"]
    _col_vision_bl = _db["vision_blacklist"]
    _col_vision_cfg = _db["vision_config"]

    # Index
    _col_anpr.create_index([("event_dt", DESCENDING)])
    _col_anpr.create_index([("license_plate", 1)])
    _col_anpr.create_index([("camera_path", 1), ("event_dt", DESCENDING)])
    _col_camera_config.create_index("camera_path", unique=True)


def _brand(logo_id):
    return BRAND_MAP.get(logo_id, "Autre")


def _get_cam_configs():
    """Return {camera_path: config_doc} dict, cached per-request."""
    return {c["camera_path"]: c for c in _col_camera_config.find()}


def _resolve_direction(raw_direction, camera_path, cam_configs):
    """Resolve raw forward/reverse into entry/exit using camera config."""
    cfg = cam_configs.get(camera_path, {})
    fwd_role = cfg.get("forward_role", "entry")  # default: forward = entry
    if raw_direction == "forward":
        return "entry" if fwd_role == "entry" else "exit"
    elif raw_direction == "reverse":
        return "exit" if fwd_role == "entry" else "entry"
    return "unknown"


def _serialize(doc, cam_configs=None):
    """Serialize a single ANPR document for JSON."""
    if cam_configs is None:
        cam_configs = _get_cam_configs()
    raw_dir = doc.get("direction", "")
    camera = doc.get("camera_path", "")
    return {
        "id": str(doc["_id"]),
        "plate": doc.get("license_plate", ""),
        "original_plate": doc.get("original_plate", ""),
        "confidence": doc.get("confidence", 0),
        "color": doc.get("vehicle_color", ""),
        "color_hex": COLOR_HEX.get(doc.get("vehicle_color", ""), "#888"),
        "type": doc.get("vehicle_type", ""),
        "type_label": VEHICLE_TYPE_LABELS.get(doc.get("vehicle_type", ""), doc.get("vehicle_type", "")),
        "brand": _brand(doc.get("vehicle_logo", 0)),
        "brand_id": doc.get("vehicle_logo", 0),
        "camera": camera,
        "direction": raw_dir,
        "resolved_dir": _resolve_direction(raw_dir, camera, cam_configs),
        "event_dt": (doc["event_dt"].replace(tzinfo=timezone.utc).isoformat() if doc["event_dt"].tzinfo is None else doc["event_dt"].isoformat()) if isinstance(doc.get("event_dt"), datetime) else str(doc.get("event_dt", "")),
        "plate_image_id": str(doc["plate_image_id"]) if doc.get("plate_image_id") else None,
        "vehicle_image_id": str(doc["vehicle_image_id"]) if doc.get("vehicle_image_id") else None,
        "plate_image_path": doc.get("plate_image_path"),
        "vehicle_image_path": doc.get("vehicle_image_path"),
        "list_name": doc.get("vehicle_list_name", ""),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@anpr_bp.route("/anpr")
def anpr_page():
    payload = getattr(request, "user_payload", {})
    user_roles = payload.get("roles", [])
    return render_template(
        "anpr.html",
        user_roles=user_roles,
        user_firstname=payload.get("firstname", ""),
        user_lastname=payload.get("lastname", ""),
        user_email=payload.get("email", ""),
    )


@anpr_bp.route("/api/anpr/search")
def anpr_search():
    """Search ANPR records with filters, pagination."""
    _ensure_db()
    cam_cfgs = _get_cam_configs()
    query = {}

    # Plate search (partial match)
    plate = request.args.get("plate", "").strip().upper()
    if plate:
        query["license_plate"] = {"$regex": plate, "$options": "i"}

    # Exclude UNKNOWN plates unless explicitly searching
    if not plate:
        query["license_plate"] = {"$ne": "UNKNOWN"}

    # Color filter
    color = request.args.get("color", "").strip()
    if color:
        query["vehicle_color"] = color

    # Type filter
    vtype = request.args.get("type", "").strip()
    if vtype:
        query["vehicle_type"] = vtype

    # Brand filter
    brand = request.args.get("brand", "").strip()
    if brand:
        # Reverse lookup brand -> logo IDs
        logo_ids = [k for k, v in BRAND_MAP.items() if v == brand]
        if logo_ids:
            query["vehicle_logo"] = {"$in": logo_ids}

    # Camera filter
    camera = request.args.get("camera", "").strip()
    if camera:
        query["camera_path"] = camera

    # Direction filter (resolved with camera config)
    direction = request.args.get("direction", "").strip()
    if direction in ("entry", "exit"):
        # Get camera configs
        configs = {c["camera_path"]: c for c in _col_camera_config.find()}
        entry_cameras = []
        exit_cameras = []
        for path, cfg in configs.items():
            fwd = cfg.get("forward_role", "entry")
            if fwd == "entry":
                entry_cameras.append(path)
                # backward = exit (implicit)
            else:
                exit_cameras.append(path)

        if direction == "entry":
            # forward on entry cameras OR reverse on exit cameras
            dir_conds = []
            if entry_cameras:
                dir_conds.append({"camera_path": {"$in": entry_cameras}, "direction": "forward"})
                dir_conds.append({"camera_path": {"$in": entry_cameras}, "direction": {"$ne": "forward"}})
            if exit_cameras:
                dir_conds.append({"camera_path": {"$in": exit_cameras}, "direction": {"$ne": "forward"}})
            # Simplify: entry = forward on entry_cams + reverse on exit_cams
            dir_conds = []
            if entry_cameras:
                dir_conds.append({"camera_path": {"$in": entry_cameras}, "direction": "forward"})
            if exit_cameras:
                dir_conds.append({"camera_path": {"$in": exit_cameras}, "direction": "reverse"})
            if dir_conds:
                query["$or"] = dir_conds
        elif direction == "exit":
            dir_conds = []
            if entry_cameras:
                dir_conds.append({"camera_path": {"$in": entry_cameras}, "direction": "reverse"})
            if exit_cameras:
                dir_conds.append({"camera_path": {"$in": exit_cameras}, "direction": "forward"})
            if dir_conds:
                query["$or"] = dir_conds

    # Date range
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    if date_from or date_to:
        dt_filter = {}
        if date_from:
            try:
                dt_filter["$gte"] = datetime.fromisoformat(date_from)
            except ValueError:
                pass
        if date_to:
            try:
                dt_filter["$lte"] = datetime.fromisoformat(date_to)
            except ValueError:
                pass
        if dt_filter:
            query["event_dt"] = dt_filter

    # Confidence min
    conf_min = request.args.get("conf_min", "").strip()
    if conf_min:
        try:
            query["confidence"] = {"$gte": int(conf_min)}
        except ValueError:
            pass

    # If ANPR-specific filters are active, skip Vision results
    has_anpr_filter = any([color, vtype, brand, camera, direction, conf_min])

    # Source filter: "", "anpr", "vision", "cross"
    source_filter = request.args.get("source", "").strip()
    if has_anpr_filter and source_filter == "":
        source_filter = "anpr"

    # For "cross" mode: find plates present in both ANPR and Vision
    cross_plates = None
    if source_filter == "cross":
        # Get all distinct normalized ANPR plates
        anpr_plates_raw = _col_anpr.distinct("license_plate", query)
        anpr_norms = set(_normalize_plate(p) for p in anpr_plates_raw if p and p != "UNKNOWN")
        # Get all Vision normalized plates
        vision_norms = set(d["plaque_norm"] for d in _col_vision_imm.find({}, {"plaque_norm": 1}) if d.get("plaque_norm"))
        # Intersection
        cross_norms = anpr_norms & vision_norms
        cross_plates = cross_norms

    # Pagination
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, int(request.args.get("per_page", 50)))

    # Collect ANPR results
    anpr_results = []
    anpr_total = 0
    if source_filter not in ("vision",):
        if cross_plates is not None:
            # Only ANPR records matching cross plates
            if cross_plates:
                query["license_plate"] = {"$in": [re.compile(n, re.IGNORECASE) for n in cross_plates]}
            else:
                query["license_plate"] = {"$in": []}
        anpr_total = _col_anpr.count_documents(query)
        docs = list(_col_anpr.find(query).sort("event_dt", DESCENDING).limit(500))
        for d in docs:
            r = _serialize(d, cam_cfgs)
            r["source"] = "anpr"
            anpr_results.append(r)

    # Collect Vision results
    vision_results = []
    vision_total = 0
    if source_filter not in ("anpr",):
        vq = {}
        if plate:
            vq["plaque_norm"] = {"$regex": _normalize_plate(plate), "$options": "i"}
        if cross_plates is not None:
            if cross_plates:
                vq["plaque_norm"] = {"$in": list(cross_plates)}
            else:
                vq["plaque_norm"] = {"$in": []}
        # Apply date filter on Vision date field (ISO string)
        if date_from:
            vq.setdefault("date", {})
            vq["date"]["$gte"] = date_from
        if date_to:
            vq.setdefault("date", {})
            vq["date"]["$lte"] = date_to

        vision_total = _col_vision_imm.count_documents(vq)
        vdocs = list(_col_vision_imm.find(vq).sort("date", -1).limit(500))
        for vd in vdocs:
            v_couleur = vd.get("couleur", "")
            vision_results.append({
                "id": str(vd["_id"]),
                "source": "vision",
                "plate": vd.get("plaque", ""),
                "original_plate": vd.get("plaque", ""),
                "confidence": 0,
                "color": v_couleur,
                "color_hex": COLOR_HEX.get(v_couleur, "") if v_couleur else "",
                "type": "",
                "type_label": vd.get("modele", ""),
                "brand": vd.get("marque", ""),
                "brand_id": 0,
                "camera": "",
                "direction": "",
                "resolved_dir": "",
                "event_dt": vd.get("date", ""),
                "plate_image_id": None,
                "vehicle_image_id": None,
                "plate_image_path": None,
                "vehicle_image_path": None,
                "list_name": "",
                "lieu": vd.get("lieu", ""),
                "billets": vd.get("billets", []),
                "billets_count": len(vd.get("billets", [])),
                "commentaire": vd.get("commentaire", ""),
                "photo_vehicule": vd.get("photo_vehicule", ""),
            })

    # Merge and sort by date descending
    merged = anpr_results + vision_results
    merged.sort(key=lambda r: r.get("event_dt", "") or "", reverse=True)

    total = anpr_total + vision_total
    skip = (page - 1) * per_page
    page_results = merged[skip:skip + per_page]

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "results": page_results,
    })


@anpr_bp.route("/api/anpr/stats")
def anpr_stats():
    """Aggregate statistics for the dashboard."""
    _ensure_db()

    # Base query (exclude unknowns for stats)
    base_match = {"license_plate": {"$ne": "UNKNOWN"}}

    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    if date_from or date_to:
        dt_filter = {}
        if date_from:
            try:
                dt_filter["$gte"] = datetime.fromisoformat(date_from)
            except ValueError:
                pass
        if date_to:
            try:
                dt_filter["$lte"] = datetime.fromisoformat(date_to)
            except ValueError:
                pass
        if dt_filter:
            base_match["event_dt"] = dt_filter

    pipeline_total = [{"$match": base_match}, {"$count": "n"}]
    total_res = list(_col_anpr.aggregate(pipeline_total))
    total = total_res[0]["n"] if total_res else 0

    pipeline_unique = [
        {"$match": base_match},
        {"$group": {"_id": "$license_plate"}},
        {"$count": "n"},
    ]
    unique_res = list(_col_anpr.aggregate(pipeline_unique))
    unique_plates = unique_res[0]["n"] if unique_res else 0

    # By color
    pipeline_color = [
        {"$match": base_match},
        {"$group": {"_id": "$vehicle_color", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_color = {d["_id"]: d["count"] for d in _col_anpr.aggregate(pipeline_color) if d["_id"]}

    # By brand (top 15)
    pipeline_brand = [
        {"$match": base_match},
        {"$group": {"_id": "$vehicle_logo", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15},
    ]
    by_brand = [{"brand": _brand(d["_id"]), "count": d["count"]}
                for d in _col_anpr.aggregate(pipeline_brand)]

    # By type
    pipeline_type = [
        {"$match": base_match},
        {"$group": {"_id": "$vehicle_type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_type = [{"type": d["_id"], "label": VEHICLE_TYPE_LABELS.get(d["_id"], d["_id"]), "count": d["count"]}
               for d in _col_anpr.aggregate(pipeline_type) if d["_id"]]

    # By camera
    pipeline_camera = [
        {"$match": base_match},
        {"$group": {"_id": "$camera_path", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_camera = {d["_id"]: d["count"] for d in _col_anpr.aggregate(pipeline_camera) if d["_id"]}

    # Hourly distribution
    pipeline_hourly = [
        {"$match": base_match},
        {"$group": {"_id": {"$hour": "$event_dt"}, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    by_hour = {d["_id"]: d["count"] for d in _col_anpr.aggregate(pipeline_hourly)}

    # Allowlist count
    pipeline_allow = [
        {"$match": {**base_match, "vehicle_list_name": "allowList"}},
        {"$count": "n"},
    ]
    allow_res = list(_col_anpr.aggregate(pipeline_allow))
    allowlist_count = allow_res[0]["n"] if allow_res else 0

    # Avg confidence
    pipeline_conf = [
        {"$match": {**base_match, "confidence": {"$gt": 0}}},
        {"$group": {"_id": None, "avg": {"$avg": "$confidence"}}},
    ]
    conf_res = list(_col_anpr.aggregate(pipeline_conf))
    avg_confidence = round(conf_res[0]["avg"], 1) if conf_res else 0

    return jsonify({
        "total": total,
        "unique_plates": unique_plates,
        "allowlist_count": allowlist_count,
        "avg_confidence": avg_confidence,
        "by_color": by_color,
        "by_brand": by_brand,
        "by_type": by_type,
        "by_camera": by_camera,
        "by_hour": by_hour,
        "color_hex": COLOR_HEX,
    })


@anpr_bp.route("/api/anpr/live")
def anpr_live():
    """Last N detections for live feed (ANPR + Vision merged)."""
    _ensure_db()
    cam_cfgs = _get_cam_configs()
    n = min(50, int(request.args.get("n", 20)))

    # ANPR detections
    anpr_docs = list(_col_anpr.find({"license_plate": {"$ne": "UNKNOWN"}}).sort("event_dt", DESCENDING).limit(n))
    results = []
    for d in anpr_docs:
        r = _serialize(d, cam_cfgs)
        r["source"] = "anpr"
        results.append(r)

    # Vision detections (recent, same window)
    vq = {}
    vf = _vision_active_filter()
    if vf:
        vq.update(vf)
    vdocs = list(_col_vision_imm.find(vq).sort("date", -1).limit(n))
    for vd in vdocs:
        v_couleur = vd.get("couleur", "")
        results.append({
            "id": str(vd["_id"]),
            "source": "vision",
            "plate": vd.get("plaque", ""),
            "original_plate": vd.get("plaque", ""),
            "confidence": 0,
            "color": v_couleur,
            "color_hex": COLOR_HEX.get(v_couleur, "") if v_couleur else "",
            "type": "",
            "type_label": vd.get("modele", ""),
            "brand": vd.get("marque", ""),
            "brand_id": 0,
            "camera": "",
            "direction": "",
            "resolved_dir": "",
            "event_dt": vd.get("date", ""),
            "plate_image_id": None,
            "vehicle_image_id": None,
            "plate_image_path": None,
            "vehicle_image_path": None,
            "list_name": "",
            "lieu": vd.get("lieu", ""),
            "billets": vd.get("billets", []),
            "billets_count": len(vd.get("billets", [])),
            "commentaire": vd.get("commentaire", ""),
            "photo_vehicule": vd.get("photo_vehicule", ""),
        })

    # Sort all by date descending, take top N
    results.sort(key=lambda r: r.get("event_dt", "") or "", reverse=True)
    return jsonify(results[:n])


@anpr_bp.route("/api/anpr/plate/<plate>")
def anpr_plate_history(plate):
    """All detections for a given plate (ANPR + Vision)."""
    _ensure_db()
    cam_cfgs = _get_cam_configs()
    plate = plate.strip().upper()
    norm = _normalize_plate(plate)

    records = []

    # ANPR detections
    docs = list(_col_anpr.find({"license_plate": plate}).sort("event_dt", DESCENDING).limit(200))
    for d in docs:
        r = _serialize(d, cam_cfgs)
        r["source"] = "anpr"
        records.append(r)

    # Vision entries (matching normalized plate)
    if norm:
        vdocs = list(_col_vision_imm.find({"plaque_norm": norm}))
        for vd in vdocs:
            v_couleur = vd.get("couleur", "")
            records.append({
                "source": "vision",
                "event_dt": vd.get("date", ""),
                "camera": "",
                "color": v_couleur,
                "color_hex": COLOR_HEX.get(v_couleur, "") if v_couleur else "",
                "resolved_dir": "",
                "lieu": vd.get("lieu", ""),
                "billets_count": len(vd.get("billets", [])),
                "marque": vd.get("marque", ""),
                "modele": vd.get("modele", ""),
                "evenement": vd.get("evenement", ""),
                "annee": vd.get("annee", 0),
            })

    # Sort all by date descending
    records.sort(key=lambda r: r.get("event_dt", "") or "", reverse=True)

    return jsonify({
        "plate": plate,
        "count": len(records),
        "records": records,
    })


HIK_IMAGE_DIR = "E:/TITAN/production/hik_images"


@anpr_bp.route("/api/anpr/image/<path:image_ref>")
def anpr_image(image_ref):
    """Sert une image : chemin disque (nouveau) ou ObjectId GridFS (ancien)."""
    _ensure_db()

    # Nouveau format : chemin relatif sur disque
    if "/" in image_ref:
        safe = os.path.normpath(image_ref)
        if ".." in safe:
            abort(400)
        full_path = os.path.join(HIK_IMAGE_DIR, safe)
        if not os.path.abspath(full_path).startswith(os.path.abspath(HIK_IMAGE_DIR)):
            abort(400)
        if os.path.isfile(full_path):
            resp = send_file(full_path, mimetype="image/jpeg")
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp

    # Ancien format : ObjectId GridFS
    else:
        try:
            oid = ObjectId(image_ref)
            grid_file = _fs.get(oid)
            return Response(
                grid_file.read(),
                mimetype="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        except Exception:
            pass

    # Fallback : pixel transparent 1x1
    return Response(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
        mimetype="image/png",
        status=404,
    )


# ---------------------------------------------------------------------------
# Camera config
# ---------------------------------------------------------------------------

@anpr_bp.route("/api/anpr/cameras")
def anpr_cameras():
    """Get camera list with config."""
    _ensure_db()
    # Get distinct cameras from data
    cameras = _col_anpr.distinct("camera_path")
    configs = {c["camera_path"]: c for c in _col_camera_config.find()}

    result = []
    for cam in sorted(cameras):
        cfg = configs.get(cam, {})
        result.append({
            "camera_path": cam,
            "label": cfg.get("label", cam.replace("/", "").replace("lapi", "LAPI ")),
            "forward_role": cfg.get("forward_role", "entry"),
            "enabled": cfg.get("enabled", True),
            "lieu": cfg.get("lieu", ""),
        })
    return jsonify(result)


@anpr_bp.route("/api/anpr/cameras/config", methods=["POST"])
def anpr_cameras_config():
    """Update camera configuration."""
    _ensure_db()
    data = request.get_json()
    if not data or "camera_path" not in data:
        return jsonify({"error": "camera_path required"}), 400

    _col_camera_config.update_one(
        {"camera_path": data["camera_path"]},
        {"$set": {
            "camera_path": data["camera_path"],
            "label": data.get("label", ""),
            "forward_role": data.get("forward_role", "entry"),
            "enabled": data.get("enabled", True),
            "lieu": data.get("lieu", ""),
        }},
        upsert=True,
    )
    return jsonify({"ok": True})


@anpr_bp.route("/api/anpr/flow")
def anpr_flow():
    """Entries vs exits over time (15-min buckets)."""
    _ensure_db()

    base_match = {"license_plate": {"$ne": "UNKNOWN"}}
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    if date_from:
        try:
            base_match.setdefault("event_dt", {})["$gte"] = datetime.fromisoformat(date_from)
        except ValueError:
            pass
    if date_to:
        try:
            base_match.setdefault("event_dt", {})["$lte"] = datetime.fromisoformat(date_to)
        except ValueError:
            pass

    # Get camera configs for entry/exit resolution
    configs = {c["camera_path"]: c for c in _col_camera_config.find()}

    # Build per-camera direction classification
    entry_match = []
    exit_match = []
    for cam in _col_anpr.distinct("camera_path"):
        cfg = configs.get(cam, {})
        fwd_role = cfg.get("forward_role", "entry")
        if fwd_role == "entry":
            entry_match.append({"camera_path": cam, "direction": "forward"})
            exit_match.append({"camera_path": cam, "direction": "reverse"})
        else:
            exit_match.append({"camera_path": cam, "direction": "forward"})
            entry_match.append({"camera_path": cam, "direction": "reverse"})

    def bucket_pipeline(dir_conditions):
        if not dir_conditions:
            return []
        return list(_col_anpr.aggregate([
            {"$match": {**base_match, "$or": dir_conditions}},
            {"$group": {
                "_id": {
                    "$dateTrunc": {"date": "$event_dt", "unit": "minute", "binSize": 15}
                },
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]))

    entries = bucket_pipeline(entry_match)
    exits = bucket_pipeline(exit_match)

    return jsonify({
        "entries": [{"t": d["_id"].isoformat(), "n": d["count"]} for d in entries],
        "exits": [{"t": d["_id"].isoformat(), "n": d["count"]} for d in exits],
    })


# ---------------------------------------------------------------------------
# On-site vehicle counter (entries - exits since last reset)
# ---------------------------------------------------------------------------

def _count_direction(cam_configs, since, target_dir):
    """Count detections resolved as 'entry' or 'exit' since a given datetime."""
    match_conds = []
    for cam_path, cfg in cam_configs.items():
        fwd_role = cfg.get("forward_role", "entry")
        if target_dir == "entry":
            raw = "forward" if fwd_role == "entry" else "reverse"
        else:
            raw = "reverse" if fwd_role == "entry" else "forward"
        match_conds.append({"camera_path": cam_path, "direction": raw})

    # Also include cameras with no config (default forward=entry)
    all_cams = _col_anpr.distinct("camera_path")
    for cam in all_cams:
        if cam not in cam_configs:
            raw = "forward" if target_dir == "entry" else "reverse"
            match_conds.append({"camera_path": cam, "direction": raw})

    if not match_conds:
        return 0

    base = {"license_plate": {"$ne": "UNKNOWN"}, "event_dt": {"$gte": since}}
    pipeline = [
        {"$match": {**base, "$or": match_conds}},
        {"$count": "n"},
    ]
    res = list(_col_anpr.aggregate(pipeline))
    return res[0]["n"] if res else 0


@anpr_bp.route("/api/anpr/onsite")
def anpr_onsite():
    """Vehicles currently on site = entries - exits since last reset."""
    _ensure_db()
    cam_configs = _get_cam_configs()

    # Get reset timestamp (or epoch if never reset)
    doc = _col_site_counter.find_one({"_id": "reset"})
    reset_at = doc["reset_at"] if doc else datetime(2000, 1, 1)

    entries = _count_direction(cam_configs, reset_at, "entry")
    exits = _count_direction(cam_configs, reset_at, "exit")
    on_site = max(0, entries - exits)

    return jsonify({
        "on_site": on_site,
        "entries": entries,
        "exits": exits,
        "reset_at": reset_at.isoformat() if isinstance(reset_at, datetime) else str(reset_at),
    })


@anpr_bp.route("/api/anpr/onsite/reset", methods=["POST"])
def anpr_onsite_reset():
    """Reset the on-site counter to 0 (stores current timestamp)."""
    _ensure_db()
    now = datetime.now(timezone.utc)
    _col_site_counter.update_one(
        {"_id": "reset"},
        {"$set": {"reset_at": now}},
        upsert=True,
    )
    return jsonify({"ok": True, "reset_at": now.isoformat()})


# ---------------------------------------------------------------------------
# Vision cross-reference (reads from MongoDB synced by vision_sync.py)
# ---------------------------------------------------------------------------

def _normalize_plate(plate):
    """Normalize plate: uppercase, alphanumeric only."""
    return re.sub(r"[^A-Z0-9]", "", (plate or "").strip().upper())


def _vision_active_filter():
    """Return the event/year filter for the active Vision event, or None."""
    cfg = _col_vision_cfg.find_one({"_id": "current"})
    if not cfg or not cfg.get("evenement"):
        return None
    return {"evenement": cfg["evenement"], "annee": cfg["annee"]}


@anpr_bp.route("/api/anpr/vision/lookup/<plate>")
def anpr_vision_lookup(plate):
    """Look up a single plate in Vision synced data."""
    _ensure_db()
    norm = _normalize_plate(plate)
    if not norm:
        return jsonify({"found": False})

    vf = _vision_active_filter()
    if not vf:
        return jsonify({"found": False, "reason": "no_config"})

    doc = _col_vision_imm.find_one({"plaque_norm": norm, **vf})
    if doc:
        return jsonify({
            "found": True,
            "plaque": doc.get("plaque", ""),
            "lieu": doc.get("lieu", ""),
            "billets": doc.get("billets", []),
            "commentaire": doc.get("commentaire", ""),
            "date": doc.get("date", ""),
            "evenement": doc.get("evenement", ""),
            "annee": doc.get("annee", 0),
            "photo_vehicule": doc.get("photo_vehicule", ""),
            "couleur": doc.get("couleur", ""),
            "marque": doc.get("marque", ""),
            "modele": doc.get("modele", ""),
        })

    # Check blacklist
    bl = _col_vision_bl.find_one({"plaque_norm": norm})
    blacklisted = None
    if bl:
        blacklisted = {"raison": bl.get("raison", ""), "date_ajout": bl.get("date_ajout", "")}

    return jsonify({"found": False, "blacklisted": blacklisted})


@anpr_bp.route("/api/anpr/vision/batch")
def anpr_vision_batch():
    """Batch lookup of plates in Vision. Returns {plate: info} for matches."""
    _ensure_db()
    plates_raw = request.args.get("plates", "").split(",")
    plates_raw = [p.strip() for p in plates_raw if p.strip()][:100]
    if not plates_raw:
        return jsonify({})

    vf = _vision_active_filter()
    if not vf:
        return jsonify({})

    # Build norm -> original plate mapping
    norm_map = {}
    for p in plates_raw:
        norm = _normalize_plate(p)
        if norm:
            norm_map.setdefault(norm, p)

    if not norm_map:
        return jsonify({})

    # Single query for all norms
    docs = _col_vision_imm.find({"plaque_norm": {"$in": list(norm_map.keys())}, **vf})
    results = {}
    for doc in docs:
        orig = norm_map.get(doc["plaque_norm"])
        if orig:
            results[orig] = {
                "lieu": doc.get("lieu", ""),
                "billets_count": len(doc.get("billets", [])),
                "commentaire": doc.get("commentaire", ""),
            }

    return jsonify(results)


@anpr_bp.route("/api/anpr/vision/search")
def anpr_vision_search():
    """Search Vision plates and cross-reference with ANPR detections."""
    _ensure_db()

    query = {}
    evt = request.args.get("evenement", "").strip()
    annee = request.args.get("annee", "").strip()
    if evt:
        query["evenement"] = evt
    if annee:
        try:
            query["annee"] = int(annee)
        except ValueError:
            pass

    lieu = request.args.get("lieu", "").strip()
    if lieu:
        query["lieu"] = lieu
    search_plate = request.args.get("plate", "").strip().upper()
    if search_plate:
        query["plaque_norm"] = {"$regex": _normalize_plate(search_plate)}

    docs = list(_col_vision_imm.find(query).sort("date", -1))

    results = []
    for doc in docs:
        norm = doc.get("plaque_norm", "")
        # Count ANPR detections for this plate (using regex to match normalized)
        anpr_count = _col_anpr.count_documents(
            {"license_plate": {"$regex": norm, "$options": "i"}}
        ) if norm else 0

        results.append({
            "plaque": doc.get("plaque", ""),
            "lieu": doc.get("lieu", ""),
            "billets": doc.get("billets", []),
            "commentaire": doc.get("commentaire", ""),
            "date": doc.get("date", ""),
            "photo_vehicule": doc.get("photo_vehicule", ""),
            "anpr_detections": anpr_count,
        })

    # Sort: matched first
    results.sort(key=lambda x: x["anpr_detections"], reverse=True)
    return jsonify({"results": results, "total": len(results)})


@anpr_bp.route("/api/anpr/vision/stats")
def anpr_vision_stats():
    """Cross-reference statistics between Vision and ANPR.
    Optional query params: evenement, annee (filter by event/year).
    """
    _ensure_db()

    # Optional event/year filter
    filt = {}
    evt = request.args.get("evenement", "").strip()
    annee = request.args.get("annee", "").strip()
    if evt:
        filt["evenement"] = evt
    if annee:
        try:
            filt["annee"] = int(annee)
        except ValueError:
            pass

    docs = list(_col_vision_imm.find(filt, {"plaque_norm": 1, "lieu": 1}))
    vision_total = len(docs)
    matched = 0
    by_lieu = {}
    by_lieu_all = {}

    # Get all distinct ANPR normalized plates for fast lookup
    anpr_norms = set(_normalize_plate(p) for p in _col_anpr.distinct("license_plate") if p and p != "UNKNOWN")

    for doc in docs:
        norm = doc.get("plaque_norm", "")
        lieu = doc.get("lieu", "")
        by_lieu_all[lieu] = by_lieu_all.get(lieu, 0) + 1
        if norm and norm in anpr_norms:
            matched += 1
            by_lieu[lieu] = by_lieu.get(lieu, 0) + 1

    # List of available events
    events = list(_col_vision_imm.aggregate([
        {"$group": {"_id": {"evenement": "$evenement", "annee": "$annee"}, "count": {"$sum": 1}}},
        {"$sort": {"_id.annee": -1, "_id.evenement": 1}},
    ]))
    events_list = [{"evenement": e["_id"]["evenement"], "annee": e["_id"]["annee"], "count": e["count"]} for e in events]

    return jsonify({
        "vision_total": vision_total,
        "matched": matched,
        "unmatched": vision_total - matched,
        "by_lieu": by_lieu,
        "by_lieu_all": by_lieu_all,
        "events": events_list,
    })
