#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pcorg_report_reportcss_fixed_v2.py (v2.1)
- 1 graphique par carte (lisibilité)
- Camembert sous-classifications (Top10 + "Autres"), EXCLUANT "Inconnu"
- Affiche sous le camembert : nb de sous-classifications "Inconnu / non renseigné"
- Top 5 opérateurs ayant OUVERT le plus d'incidents (Nom Prénom, [..] supprimés)
- Graphique "Délais de traitement par opérateur" EXCLUANT "Inconnu"
- Utilise report.css existant
"""

import os
import argparse
import locale
import re
import numpy as np
import pandas as pd
from datetime import datetime
from dateutil import tz
from pymongo import MongoClient
import plotly.graph_objects as go
import plotly.express as px
import plotly.io as pio
import json
import html
import unicodedata
from html import escape
from datetime import timezone

# --------- Réglages ---------
try:
    locale.setlocale(locale.LC_TIME, "fr_FR.UTF-8")
except Exception:
    try:
        locale.setlocale(locale.LC_TIME, "French_France.1252")
    except Exception:
        pass

PARIS = tz.gettz("Europe/Paris")
MONGO_URI = "mongodb://localhost:27017"
DB_NAME   = "titan"
COLL_NAME = "pcorg"
TEMPLATE  = "plotly_dark"
EVENT_CHOICES = ["24H AUTOS","24H MOTOS","GPF","GP EXPLORER","SUPERBIKE","LE MANS CLASSIC","24H CAMIONS"]
pio.templates.default = TEMPLATE

# --------- Helpers ---------
def minutes_between(a,b):
    if a is None or b is None: return None
    try: return (b - a).total_seconds()/60.0
    except Exception: return None

def pct(n,d): return round(100.0*n/d,1) if d and d>0 else 0.0
def sanitize(s): return None if s is None else str(s).strip()
def safe_len(s): return len(s) if isinstance(s,str) else 0
def human_td_minutes(m):
    if m is None or (isinstance(m,float) and np.isnan(m)): return "N/A"
    if m<60: return f"{int(round(m))} min"
    h=int(m//60); r=int(round(m-60*h)); return f"{h}h{r:02d}"
    
def human_hhmm(m):
    """Convertit des minutes en format HH:MM lisible."""
    if m is None or (isinstance(m, float) and np.isnan(m)):
        return "N/A"
    h = int(m // 60)
    mn = int(round(m % 60))
    return f"{h:02d}h{mn:02d}"

def clean_operator_name(x: str) -> str:
    """Nettoie un nom d'opérateur : 
    - supprime les [..] à la fin,
    - sépare NOM(S) et prénom,
    - met le NOM en majuscule et le prénom en Capitalisé.
    """
    if not x:
        return "Inconnu"
    
    s = re.sub(r"\s*\[.*\]\s*$", "", str(x)).strip()
    if not s:
        return "Inconnu"
    
    parts = s.split()
    if len(parts) == 1:
        return parts[0].upper()  # juste un token, on considère que c’est le nom
    
    prenom = parts[-1].capitalize()
    nom = " ".join(parts[:-1]).upper()
    return f"{nom} {prenom}"

def df_counts(series, top=None, name_key="label"):
    """Retourne DataFrame avec colonnes [name_key, 'n'] à partir d'une Series/array-like."""
    s = pd.Series(series)
    vc = s.value_counts(dropna=False)
    dfc = vc.rename_axis(name_key).reset_index(name='n')
    if top:
        dfc = dfc.head(top)
    # Remplacer None/NaN/vides par 'Inconnu'
    def _norm(x):
        if x is None:
            return "Inconnu"
        try:
            from math import isnan
            if isinstance(x, float) and isnan(x):
                return "Inconnu"
        except Exception:
            pass
        xs = str(x).strip()
        return xs if xs else "Inconnu"
    dfc[name_key] = dfc[name_key].apply(_norm)
    return dfc

def pie_top_other(df_counts_full: pd.DataFrame, label_col: str, value_col: str = "n", topn: int = 10, title: str = ""):
    """Camembert TopN + 'Autres' à partir d'un DF de comptes (label_col, value_col)."""
    if df_counts_full is None or df_counts_full.empty:
        return go.Figure().update_layout(title=title or "Répartition", template=TEMPLATE)
    d = df_counts_full.sort_values(value_col, ascending=False).reset_index(drop=True)
    if len(d) > topn:
        top = d.iloc[:topn].copy()
        autres_val = d.iloc[topn:][value_col].sum()
        autres = pd.DataFrame({label_col: ["Autres"], value_col: [autres_val]})
        pie_df = pd.concat([top, autres], ignore_index=True)
    else:
        pie_df = d
    fig = px.pie(pie_df, names=label_col, values=value_col, title=title, template=TEMPLATE)
    fig.update_traces(textinfo="percent+label")
    return fig

def sanitize_text(s):
    if s is None: 
        return None
    s = str(s).strip()
    return s if s else None

def esc_html(s):
    if s is None: return ""
    return (str(s)
            .replace("&","&amp;")
            .replace("<","&lt;")
            .replace(">","&gt;")
            .replace('"',"&quot;")
            .replace("'","&#39;"))

def fmt_date_local(ts):
    if pd.isna(ts): return "N/A"
    try:
        return pd.to_datetime(ts).tz_convert("Europe/Paris").strftime("%d/%m/%Y")
    except Exception:
        return pd.to_datetime(ts, errors="coerce").strftime("%d/%m/%Y")

def fmt_time_local(ts):
    if pd.isna(ts): return "N/A"
    try:
        return pd.to_datetime(ts).tz_convert("Europe/Paris").strftime("%H:%M")
    except Exception:
        return pd.to_datetime(ts, errors="coerce").strftime("%H:%M")

def _clip_text(s: str, max_chars: int = 220) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip()
    return s if len(s) <= max_chars else s[:max_chars].rstrip() + "…"

def _fmt_day_hour(ts) -> str:
    # ts est UTC dans ton DF ; on l’affiche en Europe/Paris
    try:
        ts = pd.to_datetime(ts, utc=True).tz_convert("Europe/Paris")
        return ts.strftime("%d/%m %H:%M")
    except Exception:
        return "—"
    
# --- helper pour "jolifier" la zone (AreaDesc) ---
def prettify_area(area) -> str:
    if not area:
        return "—"
    s = str(area).strip().lstrip("_")
    # garde le dernier segment après les '/'
    if "/" in s:
        s = s.split("/")[-1]
    return s or "—"

# --------- Pipeline ---------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--css", default="report.css", help="Chemin vers report.css (copié à côté du HTML)")
    args = ap.parse_args()

    print("Sélectionne l'événement :")
    for i,e in enumerate(EVENT_CHOICES, start=1): print(f"  {i}. {e}")
    choice = input("Ton choix (1-7) ou libellé exact : ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(EVENT_CHOICES):
        event = EVENT_CHOICES[int(choice)-1]
    else:
        event = choice if choice in EVENT_CHOICES else EVENT_CHOICES[0]
    try:
        year = int(input("Année (AAAA) : ").strip())
    except Exception:
        year = datetime.now().year

    client = MongoClient(MONGO_URI)
    col = client[DB_NAME][COLL_NAME]
    docs = list(col.find({"event": event, "year": year}))
    if not docs:
        print(f"⛔ Aucun document pour {event} {year}"); return

    def flatten(doc):
        ts = doc.get("ts"); close_ts = doc.get("close_ts")
        xml = (doc.get("xml_struct") or {}); caller=xml.get("caller") or {}; flags=xml.get("flags") or {}
        cl = xml.get("classification") or {}; res = xml.get("resource") or {}
        cc = (doc.get("content_category") or {})
        delay_min = minutes_between(ts, close_ts)
        return {
            "_id": doc.get("_id"),
            "ts": ts, "close_ts": close_ts, "delay_min": delay_min,
            "source": sanitize(doc.get("source")), "category": sanitize(doc.get("category")),
            "area_id": sanitize((doc.get("area") or {}).get("id")), "area_desc": sanitize((doc.get("area") or {}).get("desc")),
            "group_names": sanitize((doc.get("group") or {}).get("names")), "group_desc": sanitize((doc.get("group") or {}).get("desc")),
            "status_code": doc.get("status_code"), "severity": doc.get("severity"),
            "is_incident": bool(doc.get("is_incident")) if doc.get("is_incident") is not None else None,
            "operator_create": sanitize(doc.get("operator")), "operator_close": sanitize(doc.get("operator_close")),
            "text_len": safe_len(doc.get("text")), "text_full_len": safe_len(doc.get("text_full")), "comment_len": safe_len(doc.get("comment")),
            "sous_classification": sanitize((cl or {}).get("sous")), "appelant": sanitize((caller or {}).get("appelant")),
            "flag_tel": bool(flags.get("telephone")) if "telephone" in flags else False,
            "flag_radio": bool(flags.get("radio")) if "radio" in flags else False,
            "carroye": sanitize((res or {}).get("carroye")),
            "text": sanitize_text(doc.get("text")),
            "text_full": sanitize_text(doc.get("text_full")),
            "comment_text": sanitize_text(doc.get("comment")),
            "services_contactes_raw": sanitize( (xml.get("service_contacte")) or (cc.get("service_contacte")) ),
        }
    df = pd.DataFrame([flatten(d) for d in docs])
    
    # ===== Services contactés : normalisation & explosion =====
    def normalize_services(s):
        """
        Décode les entités XML/HTML (&amp;), nettoie les artefacts '\x3B' / 'x3B'
        et scinde proprement en liste de services.
        """
        if not s:
            return []
        s = str(s)

        # 1) Décode les entités HTML (ex: "&amp;" -> "&")
        s = html.unescape(s)

        # 2) Corrige les artefacts issus de l'import XML : \x3B, \\x3B, x3B collé, etc.
        #    Remplace toutes ces variantes par un séparateur ';'
        s = re.sub(r"(?:&amp;)?\\x3B", ";", s, flags=re.IGNORECASE)  # "\\x3B" -> ";"
        s = re.sub(r"(?:&amp;)?\bx3B\b", ";", s, flags=re.IGNORECASE) # " x3B " -> ";"
        s = s.replace("\x3B", ";")  # si la séquence est déjà interprétée en char

        # 3) Uniformise les séparateurs éventuels
        s = (s.replace("\\", ";")
            .replace("/", ";")
            .replace("|", ";")
            .replace(",", ";"))

        # 4) Nettoyage typographique simple
        parts = [p.strip(" .\t\r\n") for p in s.split(";") if p and p.strip(" .\t\r\n")]

        # 5) Option : normalisation légère (majuscule initiale, espaces)
        parts = [" ".join(p.split()) for p in parts]  # collapse espaces multiples

        return parts
    
    def clean_service_label(lbl: str) -> str:
        if not lbl: return "Inconnu"
        s = str(lbl).strip()
        s = s.rstrip(".")          # retire le point final
        s = " ".join(s.split())    # espaces propres
        return s

    df["services_list"] = df["services_contactes_raw"].apply(normalize_services)
    df["services_list_filled"] = df["services_list"].apply(lambda L: L if L else ["Inconnu"])

    # ⚠️ Exploser en emportant directement les colonnes utiles,
    #    pas de réalignement via df.loc[...] !
    svc_df = (
        df[["services_list_filled", "delay_min", "ts", "close_ts"]]
        .explode("services_list_filled")
        .rename(columns={"services_list_filled": "service"})
        .reset_index(drop=True)  # index neuf, plus de loc(...) derrière
    )
    svc_df["service"] = svc_df["service"].fillna("Inconnu")

    # (Option) Si tu veux ignorer "Inconnu" pour les délais/SLA :
    svc_df_known = svc_df[svc_df["service"] != "Inconnu"].copy()
    svc_df["service"] = svc_df["service"].map(clean_service_label)
    svc_df_known["service"] = svc_df_known["service"].map(clean_service_label)
    
    # Normalisation carroyés : majuscules + suppression espaces internes
    df["carroye"] = df["carroye"].astype(str).str.upper().str.replace(" ", "", regex=False).replace("NONE", np.nan)

    # Dates robustes
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
    df["close_ts"] = pd.to_datetime(df["close_ts"], errors="coerce", utc=True)

    # Nettoyage opérateurs (Nom Prénom, crochets supprimés)
    df["operator_create"] = df["operator_create"].apply(clean_operator_name)
    df["operator_close"]  = df["operator_close"].apply(clean_operator_name)

    # Dérivés temporels sur lignes valides
    dft = df.dropna(subset=["ts"]).copy()
    ts_paris = dft["ts"].dt.tz_convert("Europe/Paris")
    dft["date"] = ts_paris.dt.date
    dft["hour"] = ts_paris.dt.hour
    try:
        dft["dow_name"] = ts_paris.dt.day_name(locale="fr_FR")
    except Exception:
        dft["dow_name"] = ts_paris.dt.day_name()
        
    # ===== FCR: First Contact Resolution (bouclage au premier contact) =====
    df["delay_min"] = pd.to_numeric(df["delay_min"], errors="coerce")
    same_operator = (df["operator_create"].fillna("Inconnu") == df["operator_close"].fillna("Inconnu"))
    fast_close = df["delay_min"].le(30)  # seuil ajustable
    fcr_mask = same_operator & fast_close
    fcr_count = int(fcr_mask.sum())
    fcr_rate = pct(fcr_count, len(df))
    fig_fcr = px.pie(pd.DataFrame({"etat":["FCR","Non FCR"], "n":[fcr_count, max(0, len(df)-fcr_count)]}),
                    names="etat", values="n", hole=0.55, title="Bouclage au premier contact (≤ 30 min)")
    fig_fcr.update_traces(textinfo="percent+label")

    # KPIs
    N = len(df); N_closed = int(pd.notna(df["delay_min"]).sum())
    median_delay = float(np.nanmedian(df["delay_min"])) if N_closed else float("nan")
    p90_delay = float(np.nanpercentile(df["delay_min"], 90)) if N_closed else float("nan")
    # incident_rate = pct(int(pd.Series(df["is_incident"]).fillna(False).sum()), N)
    tel_share = pct(int(pd.Series(df["flag_tel"]).sum()), N)
    radio_share = pct(int(pd.Series(df["flag_radio"]).sum()), N)
        
    # =======================
    # Intervenants (niveau 1 à 5)
    # =======================
    intervs = []
    for d in docs:
        cc = d.get("content_category") or {}
        for i in range(1, 6):
            val = cc.get(f"intervenant{i}")
            if val and str(val).strip():
                intervs.append({
                    "fiche_id": d.get("_id"),
                    "niveau": f"Niveau {i}",
                    "intervenant": str(val).strip()
                })

    df_interv = pd.DataFrame(intervs)

    if not df_interv.empty:
        # Stats globales
        avg_interv_per_fiche = round(df_interv.groupby("fiche_id").size().mean(), 2)
        total_engagements = len(df_interv)

        # Top 20 intervenants
        top_global = (df_interv["intervenant"]
                    .value_counts()
                    .head(20)
                    .reset_index())
        top_global.columns = ["intervenant","n"]

        fig_interv_top = px.bar(
            top_global, x="n", y="intervenant",
            orientation="h",
            title="Top 20 des intervenants (tous niveaux confondus)"
        )
        fig_interv_top.update_traces(text=top_global["n"], textposition="outside", cliponaxis=False)
        fig_interv_top.update_layout(yaxis=dict(categoryorder='total ascending'))

        # Répartition par niveau (stacked bar)
        repart_niveaux = (df_interv.groupby(["intervenant","niveau"])
                                    .size().reset_index(name="n"))
        repart_niveaux_top = repart_niveaux[repart_niveaux["intervenant"].isin(top_global["intervenant"])]

        fig_interv_levels = px.bar(
            repart_niveaux_top,
            x="n", y="intervenant", color="niveau",
            orientation="h", barmode="stack",
            title="Répartition des intervenants par niveau (Top 20)"
        )
        
        # Camembert global — parts relatives des intervenants (tous niveaux confondus)
        interv_counts = df_counts(df_interv["intervenant"], top=None, name_key="intervenant")
        fig_interv_pie = pie_top_other(
            interv_counts,
            label_col="intervenant",
            value_col="n",
            topn=10,
            title="Intervenants — part relative (Top 10 + Autres)"
        )
    else:
        avg_interv_per_fiche = 0
        total_engagements = 0
        fig_interv_top = go.Figure().update_layout(title="Top 20 des intervenants (aucune donnée)")
        fig_interv_levels = go.Figure().update_layout(title="Répartition des intervenants par niveau (aucune donnée)")
        fig_interv_pie = go.Figure().update_layout(title="Intervenants — part relative (aucune donnée)")
    
    # =======================
    # Nuage de mots (text_full + commentaires) — nettoyage statuts & opérateurs
    # =======================
    import unicodedata

    WORD_MIN_LEN = 3

    STOPWORDS_FR = {
        "a","à","afin","ai","ainsi","après","attn","au","aucun","aucune","aura","auront","aussi","autre","aux","avec","avoir","avons","demande",
        "bon","car","cela","ces","cet","cette","ceci","ce","ça","ci","comme","comment","contre","d","dans","de","des","du","donc","dos",
        "déjà","elle","elles","en","encore","entre","est","et","étaient","était","étant","étais","été","être",
        "fait","faut","fois","font",
        "grand","grande","grandes","grands",
        "hors","hui",
        "ici","il","ils",
        "je","jusqu","jusque",
        "l","la","le","les","leur","leurs","là",
        "ma","mais","me","mes","moi","mon",
        "ne","ni","non","nos","notre","nous",
        "on","ou","où",
        "par","parce","pas","peu","peut","peuvent","plus","pour","pourra","pourrait","près","pris","prend","prendrait","prendre",
        "qu","quand","que","quel","quelle","quelles","quels","qui",
        "s","sa","sans","se","ses","si","sont","sous","sur",
        "ta","te","tes","toi","ton","tous","tout","toute","toutes","très","tu",
        "un","une","vers","vos","votre","vous","y",
        # fillers / domaine
        "pc","org","pcorg","incident","incidents","appel","appels","radio","telephone","téléphone",
        "etc","svp","mr","mme","m","km","kmh","km/h","h","mn","min",
        # statuts / bruit
        "statut","termine","terminé","terminee","en","cours","classe","vu","anciennete","ancienneté"
    }

    # stoplist dynamique : tokens des noms d’opérateurs (create/close)
    def _strip_accents(text):
        return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

    def _tokenize_basic(s):
        s = s.lower()
        s = _strip_accents(s)
        s = re.sub(r"https?://\S+|www\.\S+|\S+@\S+", " ", s)     # urls/emails
        s = re.sub(r"[^a-zàâäçéèêëîïôöùûüñ' -]", " ", s)        # enlève nombres/symboles
        s = re.sub(r"[-_’']", " ", s)
        toks = [t for t in s.split() if len(t) >= WORD_MIN_LEN]
        return toks

    op_name_tokens = set()
    for coln in ["operator_create","operator_close"]:
        if coln in df.columns:
            for name in df[coln].dropna().unique():
                op_name_tokens.update(_tokenize_basic(str(name)))

    # motifs de lignes de statut / horodatage dans les commentaires
    _re_status_line = re.compile(r"^\s*statut\s*:", re.I)
    _re_stamp_line  = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*,", re.I)

    def _clean_text_blob(s):
        """Supprime lignes de statut/horodatage et IDs [....]."""
        if not s: return ""
        # supprime lignes de statut / lignes horodatées opérateur
        lines = []
        for ln in str(s).splitlines():
            if _re_status_line.search(ln): 
                continue
            if _re_stamp_line.search(ln):
                continue
            lines.append(ln)
        s = "\n".join(lines)
        # supprime crochets d'ID opérateur
        s = re.sub(r"\[[^\]]+\]", " ", s)
        return s

    # source principale = text_full ; fallback text ; + commentaires nettoyés
    text_sources = []
    if "text_full" in df.columns:
        text_sources += [t for t in df["text_full"].dropna().map(_clean_text_blob).tolist()]
    if "text" in df.columns:
        text_sources += [t for t in df["text"].dropna().map(_clean_text_blob).tolist()]
    if "comment_text" in df.columns:
        text_sources += [t for t in df["comment_text"].dropna().map(_clean_text_blob).tolist()]

    # tokenisation + filtre
    raw_tokens = _tokenize_basic(" ".join(text_sources))
    tokens = [t for t in raw_tokens if t not in STOPWORDS_FR and t not in op_name_tokens]

    if len(tokens):
        freq = pd.Series(tokens).value_counts()
        freq = freq.head(120)
        wordcloud_words = [{"t": w, "n": int(c)} for w, c in freq.items()]
    else:
        wordcloud_words = []

    def sla_share(thr):
        if N_closed==0: return 0.0
        s = pd.Series(df["delay_min"]).dropna()
        within = (s <= thr).sum()
        return pct(int(within), int(len(s)))
    SLA10,SLA30,SLA60 = sla_share(10), sla_share(30), sla_share(60)

    # =======================
    # Graphes (1 par carte)
    # =======================
    # Chronologie par heure
    if not dft.empty:
        by_hour = dft.groupby(["date","hour"]).size().reset_index(name="count")
        by_hour["dt"] = pd.to_datetime(by_hour["date"]) + pd.to_timedelta(by_hour["hour"], unit="h")
        fig_timeline = go.Figure([go.Bar(x=by_hour["dt"], y=by_hour["count"], name="Interventions/h")])
        fig_timeline.update_layout(title="Chronologie des créations (par heure)", xaxis_title="Date/Heure", yaxis_title="Volume")
    else:
        fig_timeline = go.Figure().update_layout(title="Chronologie des créations (par heure)")

    # Heatmap jour x heure
    if not dft.empty:
        hm = dft.groupby(["dow_name","hour"]).size().reset_index(name="n")
        days_order = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
        hm["dow_name"] = hm["dow_name"].str.lower()
        hm["dow_name"] = pd.Categorical(hm["dow_name"], categories=days_order, ordered=True)
        pivot = hm.pivot(index="dow_name", columns="hour", values="n").fillna(0)
        fig_heat = px.imshow(pivot.values, labels=dict(x="Heure", y="Jour", color="Volume"),
                             x=list(range(0,24)), y=pivot.index.tolist(), aspect="auto",
                             title="Carte de chaleur — activité (jour x heure)")
    else:
        fig_heat = go.Figure().update_layout(title="Carte de chaleur — activité (jour x heure)")
        
    # ===== Backlog dans le temps =====
    events_open = dft[["ts"]].copy()
    events_open["delta"] = 1
    events_open = events_open.rename(columns={"ts":"t"})

    events_close = df[pd.notna(df["close_ts"])][["close_ts"]].copy()
    events_close["delta"] = -1
    events_close = events_close.rename(columns={"close_ts":"t"})

    flow = pd.concat([events_open[["t","delta"]], events_close[["t","delta"]]], ignore_index=True)
    flow = flow.sort_values("t")
    flow["backlog"] = flow["delta"].cumsum()

    fig_backlog = go.Figure()
    fig_backlog.add_trace(go.Scatter(x=flow["t"], y=flow["backlog"], mode="lines", name="Backlog"))
    fig_backlog.update_layout(title="Backlog au fil du temps (ouvertes non closes)",
                            xaxis_title="Temps", yaxis_title="Fiches ouvertes")
    
    # ===== Suggestion de capacité horaire (baseline) =====
    if not dft.empty:
        cap_hour = dft.groupby("hour").size().reset_index(name="n")
        p90_hour = int(np.percentile(cap_hour["n"], 90))
        # Petit texte affichable :
        staffing_hint = f"Capacité conseillée : viser ~{p90_hour} prises en charge/heure au pic (P90), ajuster selon SLA ciblé."
    else:
        staffing_hint = "Capacité conseillée : N/A (pas de données temporelles)."

    # Comptages normalisés
    source_series = df["source"].dropna().astype(str)
    source_counts = df_counts(
        source_series[source_series.str.startswith("PCO")],
        top=15,
        name_key="source"
    )
    
    # ===== Hotspots : carroyés à risque (volume x relances x délais) =====
    df_hot = pd.DataFrame({
        "carroye": df["carroye"],
        "relances": df["relance_count"] if "relance_count" in df.columns else 0,
        "delay_min": df["delay_min"]
    })
    df_hot = df_hot.dropna(subset=["carroye"]).copy()
    agg_hot = (df_hot.groupby("carroye")
                .agg(volume=("carroye","count"),
                    relance_rate=("relances", lambda s: pct(int((s>0).sum()), len(s))),
                    p90_delay=("delay_min", lambda s: np.percentile(pd.to_numeric(s, errors="coerce").dropna(),90) if pd.notna(s).any() else np.nan))
                .reset_index())

    # Score simple (0-100) : normaliser chaque axe puis moyenne
    def _norm_col(s):
        s = s.astype(float)
        if s.max() == s.min(): return pd.Series([0.0]*len(s), index=s.index)
        return (s - s.min()) / (s.max() - s.min())

    agg_hot["score_risque"] = round(100 * ( _norm_col(agg_hot["volume"])
                                        + _norm_col(agg_hot["relance_rate"])
                                        + _norm_col(agg_hot["p90_delay"].fillna(0)) ) / 3.0, 1)
    top_hot = agg_hot.sort_values("score_risque", ascending=False).head(20)

    fig_hot = px.scatter(top_hot, x="p90_delay", y="relance_rate", size="volume", color="score_risque",
                        hover_data=["carroye","volume","relance_rate","p90_delay","score_risque"],
                        title="Hotspots carroyés (taille=volume, couleur=score de risque)")
    fig_hot.update_layout(xaxis_title="P90 délai (min)", yaxis_title="Taux de relance (%)")

    # Sous-classifications sans "Inconnu"
    sc_series = df["sous_classification"]
    
    mask_known = ~(sc_series.isna() | sc_series.astype(str).str.strip().eq("") | sc_series.astype(str).str.strip().eq("Inconnu"))
    sous_counts_all = df_counts(sc_series[mask_known], top=None, name_key="sous_classification")\
                        .sort_values("n", ascending=True)
    area_counts   = df_counts(df["area_desc"], top=15, name_key="area_desc")

    fig_source = px.bar(source_counts, x="n", y="source", orientation="h",
                        title="Top catégories 'source' (PCO.*)")
    
    fig_sous = px.bar(
        sous_counts_all,
        x="n",
        y="sous_classification",
        orientation="h",
        title="Sous-classifications"
    )

    # Ordre lisible et hauteur dynamique selon le nb de lignes
    n_items = len(sous_counts_all)
    fig_sous.update_layout(
        yaxis=dict(categoryorder='total ascending'),
        height=min(2400, max(500, 22 * n_items))  # ~22px par catégorie (bornes 500–2400)
    )
    # Afficher la valeur à droite de chaque barre
    fig_sous.update_traces(text=sous_counts_all["n"], textposition="outside", cliponaxis=False)

    fig_area   = px.bar(area_counts,   x="n", y="area_desc", orientation="h",
                        title="Top zones (AreaDesc)")
        
    # --- Carroyés : tous les comptes, sans seuil ---
    car_counts_all = df_counts(
        df["carroye"].dropna(), 
        top=None, 
        name_key="carroye"
    ).sort_values("n", ascending=True)
    
    # --- Descriptions groupées par carroyé (pour popups) ---
    def _clip(s, max_chars=240):
        s = str(s)
        return s if len(s) <= max_chars else s[:max_chars].rstrip() + "…"

    desc_df = (
        df.loc[pd.notna(df["carroye"]) & pd.notna(df["text"]), ["carroye", "text"]]
        .groupby("carroye")["text"]
        .apply(list)
        .reset_index(name="descs")
    )
    # Allège un peu la page
    desc_df["descs"] = desc_df["descs"].apply(lambda L: [_clip(t) for t in L])

    # --- Données pour la carte Leaflet (bulle = n, couleur ~ n) ---
    grid_docs = list(client[DB_NAME]["grid_ref"].find({}, {"_id": 0, "grid_ref": 1, "latitude": 1, "longitude": 1}))
    grid_df = pd.DataFrame(grid_docs)

    if not grid_df.empty and not car_counts_all.empty:
        grid_df["grid_ref"] = grid_df["grid_ref"].astype(str).str.upper().str.replace(" ", "", regex=False)

        # Jointure avec coord + descriptions
        car_map_df = (
            car_counts_all
            .merge(grid_df, left_on="carroye", right_on="grid_ref", how="left")
            .merge(desc_df, on="carroye", how="left")
        )

        missing_coords = int(car_map_df["latitude"].isna().sum())

        car_map_df = car_map_df.dropna(subset=["latitude", "longitude"]).copy()
        car_map_df["descs"] = car_map_df["descs"].apply(lambda x: x if isinstance(x, list) else [])

        car_points = [
            {
                "ref": r["carroye"],
                "n": int(r["n"]),
                "lat": float(r["latitude"]),
                "lon": float(r["longitude"]),
                "descs": r["descs"],  # <— on pousse les descriptions au front
            }
            for _, r in car_map_df.iterrows()
        ]
    else:
        missing_coords = 0
        car_points = []

    car_points_json = json.dumps(car_points)  # injecté dans le HTML
    
    # ===== Services contactés =====
    # Ne garder que les délais connus pour les stats de temps
    svc_closed = svc_df[pd.notna(svc_df["delay_min"])].copy()

    if not svc_df_known.empty:
        repart = (svc_df_known.groupby("service").size()
                .reset_index(name="n")
                .sort_values("n", ascending=False))

        # Camembert TopN + "Autres" (réutilise ta helper pie_top_other)
        fig_svc_split = pie_top_other(
            repart, label_col="service", value_col="n", topn=12,
            title="Répartition des services contactés (hors 'Inconnu')"
        )
        fig_svc_split.update_traces(textinfo="percent+label")
    else:
        fig_svc_split = go.Figure().update_layout(
            title="Répartition des services contactés (aucun service connu)"
        )

    if not svc_closed.empty:
        # Agrégats (on ajoute juste la moyenne)
        svc_agg = (svc_closed.groupby("service")["delay_min"]
            .agg(
                n="count",
                mediane="median",
                moyenne="mean",
                p90=lambda s: np.percentile(s.dropna(), 90),
                sla10=lambda s: pct(int((s<=10).sum()), len(s)),
                sla30=lambda s: pct(int((s<=30).sum()), len(s)),
                sla60=lambda s: pct(int((s<=60).sum()), len(s))
            )
            .reset_index()
            .sort_values("p90", ascending=True)
        )

        # Cap visuel (évite que 1-2 services écrasent l’échelle)
        cap_p90 = float(np.percentile(svc_agg["p90"].dropna(), 95))
        svc_agg["p90_cap"]     = svc_agg["p90"].clip(upper=cap_p90)
        svc_agg["mediane_cap"] = svc_agg["mediane"].clip(upper=cap_p90)
        svc_agg["moyenne_cap"] = svc_agg["moyenne"].clip(upper=cap_p90)

        # Étiquettes HH:MM visibles
        def _hhmm(x): return human_hhmm(float(x)) if pd.notna(x) else "N/A"
        svc_agg["p90_txt"]     = svc_agg["p90"].map(_hhmm)
        svc_agg["mediane_txt"] = svc_agg["mediane"].map(_hhmm)
        svc_agg["moyenne_txt"] = svc_agg["moyenne"].map(_hhmm)

        # Axe en minutes mais ticks HH:MM (pas = 30 min)
        x_max     = int(np.ceil(cap_p90 / 30.0) * 30)
        tick_vals = list(range(0, x_max + 1, 30))
        tick_text = [human_hhmm(v) for v in tick_vals]

        # ----- Graphe P90 lisible sans hover -----
        fig_svc_p90 = go.Figure()

        # Barre = P90 avec étiquette HH:MM au bout
        fig_svc_p90.add_trace(go.Bar(
            x=svc_agg["p90_cap"],
            y=svc_agg["service"],
            orientation="h",
            name="P90",
            text=svc_agg["p90_txt"],
            textposition="outside",
            cliponaxis=False,
            hovertemplate=(
                "Service=%{y}<br>"
                "P90=%{x:.0f} min<br>"
                "Médiane=%{customdata[0]:.0f} min<br>"
                "Moyenne=%{customdata[1]:.0f} min<br>"
                "N clos=%{customdata[2]}"
            ),
            customdata=np.stack([svc_agg["mediane"], svc_agg["moyenne"], svc_agg["n"]], axis=1)
        ))

        # Épingles Médiane (◯) & Moyenne (◆) avec leurs labels HH:MM
        fig_svc_p90.add_trace(go.Scatter(
            x=svc_agg["mediane_cap"],
            y=svc_agg["service"],
            mode="markers+text",
            name="Médiane",
            marker_symbol="circle",
            marker_size=10,
            text=svc_agg["mediane_txt"],
            textposition="middle right",
            textfont=dict(size=11),
            hovertemplate="Service=%{y}<br>Médiane=%{x:.0f} min"
        ))
        fig_svc_p90.add_trace(go.Scatter(
            x=svc_agg["moyenne_cap"],
            y=svc_agg["service"],
            mode="markers+text",
            name="Moyenne",
            marker_symbol="diamond",
            marker_size=10,
            text=svc_agg["moyenne_txt"],
            textposition="middle right",
            textfont=dict(size=11),
            hovertemplate="Service=%{y}<br>Moyenne=%{x:.0f} min"
        ))

        # Lignes de repère SLA (si dans l’échelle)
        shapes = []
        for thr, color, label in [(10, "#4CAF50", "SLA 10 min"),
                                (30, "#FFC107", "SLA 30 min"),
                                (60, "#F44336", "SLA 60 min")]:
            if thr <= x_max:
                shapes.append(dict(type="line", x0=thr, x1=thr, y0=-0.5, y1=len(svc_agg)-0.5,
                                line=dict(width=1, dash="dot", color=color)))
        fig_svc_p90.update_layout(shapes=shapes)

        fig_svc_p90.update_layout(
            title=f"Délais par service — P90 avec cap {human_hhmm(cap_p90)}",
            xaxis_title="Durée (minutes) — lecture HH:MM en ticks/labels",
            yaxis_title="Service",
            xaxis=dict(tickmode="array", tickvals=tick_vals, ticktext=tick_text, range=[0, x_max]),
            yaxis=dict(categoryorder="array", categoryarray=svc_agg["service"].tolist()),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            height=min(3200, max(520, 26 * len(svc_agg)))
        )
        
        # Axe en heures (arrondi supérieur à l’heure)
        x_max_hours = int(np.ceil(cap_p90 / 60.0))
        tick_vals = [h*60 for h in range(0, x_max_hours+1)]
        tick_text = [f"{h:02d}h" for h in range(0, x_max_hours+1)]

        fig_svc_p90.update_layout(
            xaxis=dict(
                tickmode="array",
                tickvals=tick_vals,
                ticktext=tick_text,
                range=[0, x_max_hours*60]
            )
        )
        
        # Supprimer les labels des axes
        fig_svc_p90.update_xaxes(showticklabels=False)

        # ----- Ton graphe SLA d'origine : inchangé -----
        sla_melt = svc_agg.melt(id_vars=["service","n"],
                                value_vars=["sla10","sla30","sla60"],
                                var_name="SLA", value_name="pct")
        sla_melt["SLA"] = sla_melt["SLA"].map({"sla10":"≤ 10 min","sla30":"≤ 30 min","sla60":"≤ 60 min"})

        fig_svc_sla = px.bar(
            sla_melt, x="pct", y="service", color="SLA", barmode="group",
            title="SLA par service (fiches closes, hors 'Inconnu')"
        )
        fig_svc_sla.update_layout(xaxis_title="%", yaxis_title="", yaxis=dict(categoryorder='total ascending'))

    else:
        fig_svc_p90 = go.Figure().update_layout(
            title="Services contactés — P90 des délais (aucune fiche close connue)"
        )
        fig_svc_sla = go.Figure().update_layout(
            title="SLA par service (aucune fiche close connue)"
        )

    # Répartition par canal (tel/radio/autre)
    chan_counts = df_counts(np.where(df["flag_tel"], "Téléphone",
                             np.where(df["flag_radio"], "Radio", "Autre")), name_key="canal")
    fig_chan = px.pie(chan_counts, names="canal", values="n", title="Répartition par canal")
    
    # =======================
    # Appelants (normalisés + alias + regroupements sociétés)
    # =======================
    def _strip_accents(text: str) -> str:
        return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

    def normalize_spaces(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    # Alias explicites (clé = libellé canonique -> variantes)
    APPELANT_ALIASES = {
        # PC / Direction course / Helpdesk
        "PCORG": [
            "PCO", "PC ORG", "PC ORGANISATION",
            "PC ORG A CD HYPER CENTRE",
            "PC ORG > RESP PANORAMA",
            "PC ORG S BATTEUX",
            "PC ORG BENOIT COULBAUT", "CGO", "OSE", "GROUPE WHATSAPP PCORG"
        ],
        "OPV": [
            "VIDEO PC ORG",
            "CAMERA PCORG",
            "CAMERA PC ORG",
            "PCORG OPERATEUR VIDEOAK",
            "OPV",
            "OPERATEUR VIDEO",
            "VIDEO PC"
        ],
        "DIRECTION DE COURSE": [
            "DIRECTION COURSE", "PC COURSE", "DIRECTION DE COURSE LOIC"
        ],
        "HELP DESK": ["HELP DESCK"],

        # Personnes
        "LAR": ["L ARNAULT", "LUDO", "LUDOVIC", "LUDOVIC ARNAULT", "LUDO PCA"],
        "TCH": [
            "T CHEVALLIER", "TONY CHEVALLIER", "TONY C", "MESSAGE DE T CHEVALIER VIA OPERATEUR SECURITE PCORG",
            "T CHEVALIER", "TONY", "CHEVALLIER TONY"
        ],
        "FERNANDO THIERRY": ["THIERRY FERNANDO", "T FERNANDO"],
        "RIAUDEL ANTHONY": [
            "ANTONY RIAUDEL", "ANTHONY RIAUDEL", "A RIAUDEL",
            "RIAUDEL ANTONY", "RIAUDEL ANTONY A EVENTS", "RIAUDEL ANTONY EVENTS",
            "RIAUDEL ANTONY PROD EVENT", "MR ANTOINE RIAUDEL"
        ],
        "NAVARRE PIERRICK": [
            "PIERRICK NAVARRE", "PIERRICK NAVARE", "MR NAVARRE P", "PIERREK NAVARRE", "P NAVARRE"
        ],
        "DEROUET ANITA": ["ANITA DEROUET", "A DEROUET", "ANITA DU CLUB"],
        "PACAUD NATACHA": ["NATACHA PACAUD", "N PACAUD", "NATASHA PACAUD"],
        "CHAPERON AMANDINE": [
            "AMANDINE", "AMANDINE ACO ET POLICIER PCA", "A CHAPERON"
        ],
        "ELGHOZI MAXIME": [
            "MAXIME ELGHOZI", "MAXIME ELHOZI", "MAXIME ELGHOZY", "MAXIME ELHOZY", "M ELGHOZI"
        ],
        "JARRY CHRISTELLE": [
            "CHRISTELLE JARRY",
            "C JARRY",
            "14H36 CHRISTELLE JARRY",
            "JARRY CHRISTELLE",
            "JARRY PROD EVENNT"
        ],
        "DONNET STEPHANE": [
            "S DONNET",
            "STEPHANE DONNET",
            "19H51 STEPHANE DONNET"
        ],
        "LANNOO ANTOINE": [
            "ANTOINE LANNOO",
            "A LANNOO",
            "A LANOO"
        ],
        "JEUSSELIN CELINE": [
            "CELINE JEUSSELIN",
            "C JEUSSELIN",
            "C JEUSSELIN CLUB ACO"
        ],
        "TANGO": [
            "TANGO",
            "MANU TANGO",
            "FLORIANT TANGO",
            "TANGOS NO"
        ],
        "HERVE PATRICK": [
            "P HERVE",
            "PATRICK HERVE",
            "PATRICK HERVE / ZONE PADDOCK / 06 65 68 76 55",
            "PATRICK HERVE RESP HYPERCENTRE"
        ],
        "BEAUMESNIL VINCENT": [
            "VINCENT BEAUMESNIL",
            "MR V BEAUSMENIL",
        ],
        "PCA": [
            "PCA",
            "GUY CAMERA PCA",
            "PC FLUX",
            "DIRECTRICE DE CABINET PREFECTURE",
        ],
        "POLICE GENDARMERIE": [
            "POLICE NATIONEL ET CD EST / SUD EST",
            "CADET DE LA GENDARMERIE",
            "GENDARMERIE D'ECOMMOY",
            "MAJOR HUARD PMR"
        ],
        "RESPONSABLE JALONNEMENT": [
            "RESPONSABLE JALONNEMENT",
            "RESP JALONNEMENT",
            "JAL"
        ],
        "NAVARRE MATHIEU": [
            "MATTHIEU NAVARRE",
            "MAIL DE MATHIEU NAVARRE POUR INFORMATION HELICOPTERE",
            "M NAVARRE"
        ],

        # Rôles récurrents
        "RESPONSABLE ZONE TRIBUNES": [
            "RESP TRIBUNES", "RESP TRIBUNE", "RESP TRIBUNE BASSE"
        ],
        "RESPONSABLE ZONE SUD": [
            "RESPONSABLE ZONE SUD", "RESP ZONE SUD", "RESP SUD",
            "RZ SUD", "CZ SUD", "CZSUD",
            "CHEF DE ZONE SUD"
        ],
        "RESPONSABLE ZONE OUEST": [
            "RESPONSABLE ZONE OUEST", "RESP ZONE OUEST", "RESP OUEST",
            "RZ OUEST", "CZ OUEST", "CHEF DE ZONE OUEST"
        ],
        "RESPONSABLE ZONE NORD": [
            "RESPONSABLE ZONE NORD", "RESP ZONE NORD", "RESP NORD",
            "RZ NORD", "CZ NORD", "CHEF DE ZONE NORD",
            "RESPONBLE DE ZONE"
        ],
        "RESPONSABLE ZONE EST": [
            "RESPONSABLE ZONE EST", "RESP ZONE EST", "RESP EST",
            "RZ EST", "RZEST", "CZ EST",
            "CHEF DE ZONE EST"
        ],
        "RESPONSABLE ZONE ARNAGE MULSANNE": [
            "RESP ARNAGE MULSANNE", "RESPONSABLE ARNAGE MULSANNE", "RESPONSABLE MULSANNE ARNAGE",
            "RESPONSABLE ARNAGE / MULSANNE", "CZ ARNAGE MULSANNE", "MR MINIER RZ ARNAGE / MULSANNE"
        ],
        "RESPONSABLE ZONE 92": [
            "RESPONSABLE ZONE 92", "RESP ZONE 92", "RESP Z92",
            "RZ 92", "RZONE92", "Z92",
            "ZONE 92", "CZ 92", "CD92",
            "CP ZONE 92"
        ],
        "RESPONSABLE ZONE VILLAGE": [
            "RESPONSABLE ZONE VILLAGE", "RESP ZONE VILLAGE", "RESP VILLAGE",
            "CZ VILLAGE", "CHEF DE ZONE VILLAGE", "SECURITE VILLAGE"
        ],
        "RESPONSABLE ZONE GRAND PADDOCK": [
            "RESPONSABLE ZONE GRAND PADDOCK", "RESP ZONE GRAND PADDOCK", "ZONE GRAND PADDOCK",
            "GRAND PADDOCK", "RESPONSABLE ZONE PADDOCK", "RESP ZONE PADDOCK",
            "ZONE PADDOCK", "CZ PADDOCK", "CZ PADDOCKS",
            "CHEF DE ZONE PADDOCK", "CHEF DE SECTEUR PADDOCK"
        ],
        "RESPONSABLE ZONE BUGATTI": [
            "RESPONSABLE ZONE BUGATTI", "RESP ZONE BUGATTI", "CZ BUGATTI",
            "CHEF DE ZONE BUGATTI"
        ],
        "RESPONSABLE PORTE SUD": ["RESP PORTE SUD"],
        "RESPONSABLE PORTE NORD": ["RESP PORTE NORD"],
        "RESPONSABLE PRAIRIE": ["RESP PRAIRIE", "RESPONSABLE PRAIRIES", "RESPONSABLE PRAIRIE ET HIPPODROME"],
        "RESPONSABLE VILLAGE": ["RESP VILLAGE", "SECURITE VILLAGE"],
        "RESPONSABLE CIK": ["RESP CIK"],
        "APPUI FLUX MOBILE": [
            "APPUI FLUX MOBILE MOTO 1", "APPUI FLUX MOBILE", "APPUI MOBIL MOTO",
            "APPUI MOBIL MOTO3", "APPUI FLUX MOTO 2", "MOTO 4 APPUI FLUX", "APPUI FLUX MOBIL", "MOTO 4", "MOTO 2"
        ],
        "PATROUILLE 1": ["PATROUILLE1", "PATROUILE 1"],
        "PATROUILLE 2": ["PATROUILLE2"],
        "PATROUILLE 3": ["PATROUILLE3", "PATROUILLE 3"],
        "PATROUILLE 4": ["PATROUILLE4"],
        "PATROUILLE 5": ["PATROUILLE5"],
        "PATROUILLE 6": ["PATROUILLE6"],
    }

    # =======================
    # Entreprises de sécurité (canon -> motif souple)
    #   -> couvre variantes avec/ sans espaces/ tirets/typos simples
    # =======================
    SECURITY_PATTERNS = {
        # PROGUARD / PROGARD / PRO-GARD / PRO GARD
        "PROGUARD": re.compile(r"\bPROG\s*U?\s*ARD\b", re.I),

        # A-TEAM / A TEAM / ATEAM
        "A-TEAM": re.compile(r"\bA\s*[- ]?\s*TEAM\b", re.I),

        # TERANGA (couvre COORDO/COORDONNATEUR TERANGA)
        "TERANGA": re.compile(r"\bTERANGA\b", re.I),

        # REFLEX
        "REFLEX": re.compile(r"\bREFLEX\b", re.I),

        # S3M
        "S3M": re.compile(r"\bS\s*3\s*M\b", re.I),

        # BPS
        "BPS": re.compile(r"\bBPS\b", re.I),

        # ACA
        "ACA": re.compile(r"\bACA\b", re.I),

        # (exemple) ROYAL SECURITE / SECURTITE
        "ROYAL SECURITE": re.compile(r"\bROYAL\s*SECUR[EI]TE\b", re.I),
    }

    # (Facultatif) Liste simple pour fallback "substring" strict si besoin
    SECURITY_COMPANIES = list(SECURITY_PATTERNS.keys())

    # Pré-calcul des variantes normalisées (aliases)
    _alias_lookup = {}
    for canon, variants in APPELANT_ALIASES.items():
        normset = {
            normalize_spaces(_strip_accents(v).upper().replace(".", " ").replace("-", " "))
            for v in variants + [canon]  # on ajoute le canon lui-même
        }
        _alias_lookup[canon] = normset

    # Normalizers génériques (orthographe/abréviations)
    ROLE_NORMALIZERS = [
        (re.compile(r"\bHELP\s*DESK\b", re.I), "HELP DESK"),
        (re.compile(r"\bPATROU+I?L?LE?\s*([0-9])\b", re.I), lambda m: f"PATROUILLE {m.group(1)}"),
        (re.compile(r"\bAPPUI\s*MOBI?L?E?\s*MOTO\b", re.I), "APPUI FLUX MOBILE"),
        (re.compile(r"\bVIDEO\s*PC\s*ORG?\b", re.I), "PCORG"),
        (re.compile(r"\bCAMERA\s*PC\s*ORG?\b", re.I), "PCORG"),
        (re.compile(r"\bDIR(ECT)?\.*\s*COURSE\b", re.I), "DIRECTION DE COURSE"),
        # Uniformisation CP/CZ/CS + secteur (on garde l’abréviation)
        (re.compile(r"\b(CP|CZ|CS)\s+(.+)", re.I),
        lambda m: f"{m.group(1).upper()} {normalize_spaces(m.group(2).upper())}"),
    ]

    def _apply_role_normalizers(s: str) -> str:
        for rx, repl in ROLE_NORMALIZERS:
            if rx.search(s):
                s = rx.sub(repl, s)
        return s

    def canonicalize_appelant(x: str):
        if not isinstance(x, str) or not x.strip():
            return None

        # Normalisation brute
        s = _strip_accents(x).upper()
        s = s.replace(".", " ").replace("-", " ")
        s = normalize_spaces(s)

        # 0) Normalizers génériques (orthographe/abréviation)
        s = _apply_role_normalizers(s)

        # 1) Aliases exacts (après normalisation)
        for canon, normset in _alias_lookup.items():
            if s in normset:
                return canon

        # 2) Entreprises de sécurité (regex souples)
        for canon, rx in SECURITY_PATTERNS.items():
            if rx.search(s):
                return canon

        # 3) Fallback: sous-chaîne stricte + version "sans espace" (au cas où)
        s_no_space = s.replace(" ", "")
        for company in SECURITY_COMPANIES:
            c1 = company
            c2 = company.replace(" ", "")
            if c1 in s or c2 in s_no_space:
                return company

        # 4) Par défaut, version normalisée
        return s or None

    df_app = df.copy()
    df_app["appelant_clean"] = df_app["appelant"].apply(canonicalize_appelant)

    # Compteurs manquants
    m_appelant_missing_local = df_app["appelant_clean"].isna()
    n_appelants_missing = int(m_appelant_missing_local.sum())
    p_appelants_missing = pct(n_appelants_missing, len(df_app)) if len(df_app) else 0.0

    df_app_known = df_app.loc[~m_appelant_missing_local].copy()

    if not df_app_known.empty:
        # Comptage global
        appelants_counts = (
            df_app_known["appelant_clean"]
            .value_counts()
            .reset_index()
        )
        appelants_counts.columns = ["appelant", "n"]
        n_appelants_uniques = len(appelants_counts) if not appelants_counts.empty else 0

        # ---- Top 20 bar dynamique ----
        top_appelants = appelants_counts.head(20)
        n_bars = len(top_appelants)
        dyn_height = min(1600, max(420, 28 * n_bars))

        fig_appelants_top = px.bar(
            top_appelants, x="n", y="appelant",
            orientation="h",
            title="Top 20 des appelants (normalisés)"
        )
        fig_appelants_top.update_traces(text=top_appelants["n"], textposition="outside", cliponaxis=False)
        fig_appelants_top.update_layout(
            yaxis=dict(categoryorder='total ascending'),
            height=dyn_height
        )

        # ---- Treemap filtré (min 2 appels) ----
        appelants_treemap_data = appelants_counts[appelants_counts["n"] >= 2].copy()

        if not appelants_treemap_data.empty:
            # Hauteur dynamique plafonnée : 20 px par case, bornée entre 500 et 900
            n_nodes = len(appelants_treemap_data)
            treemap_height = min(700, max(500, 20 * n_nodes))

            # % du total pour l’infobulle
            total_n = appelants_treemap_data["n"].sum()
            appelants_treemap_data["pct"] = 100 * appelants_treemap_data["n"] / total_n

            fig_appelants_treemap = px.treemap(
                appelants_treemap_data,
                path=["appelant"],
                values="n",
                title="Répartition des appelants (≥ 2 appels, Treemap)",
            )

            fig_appelants_treemap.update_traces(
                textinfo="label+value",
                hovertemplate="<b>%{label}</b><br>Appels: %{value}<br>Part: %{customdata:.1f}%",
                customdata=appelants_treemap_data["pct"],
                marker=dict(line=dict(width=0.5)),
                tiling=dict(pad=2),
            )

            fig_appelants_treemap.update_layout(
                height=treemap_height,
                uniformtext=dict(minsize=10, mode="show"),
                margin=dict(l=10, r=10, t=60, b=10),
            )
        else:
            fig_appelants_treemap = go.Figure().update_layout(
                title="Répartition des appelants (≥ 2 appels) — aucun groupe"
            )

        # ---- Tableau complet HTML (popup) ----
        table_rows = "\n".join(
            f"<tr><td>{escape(str(row['appelant']))}</td><td>{int(row['n'])}</td></tr>"
            for _, row in appelants_counts.iterrows()
        )
        appelants_table_html = f"""
    <button class="open-modal-btn" onclick="openModal('popup-appelants')">
    Voir la liste complète des appelants
    </button>

    <div id="popup-appelants" class="modal" onclick="closeModal(event, 'popup-appelants')">
    <div class="modal-content" onclick="event.stopPropagation()">
        <h3>Liste complète des appelants</h3>
        <table>
        <thead>
            <tr><th>Appelant</th><th>Nombre d'appels</th></tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
        </table>
        <button class="close-btn" onclick="closeModal(event, 'popup-appelants')">Fermer</button>
    </div>
    </div>

    <script>
    function openModal(id) {{
    document.getElementById(id).style.display = 'block';
    }}
    function closeModal(event, id) {{
    document.getElementById(id).style.display = 'none';
    }}
    </script>
    """
    else:
        fig_appelants_top = go.Figure().update_layout(title="Top 20 des appelants (aucune donnée)")
        fig_appelants_treemap = go.Figure().update_layout(title="Répartition des appelants (aucune donnée)")
        appelants_table_html = "<em>Aucun appelant connu.</em>"

    # ------ Camembert sous-classifications : EXCLURE "Inconnu" ------
    sc_series = df["sous_classification"]
    unknown_mask = sc_series.isna() | sc_series.astype(str).str.strip().eq("") | sc_series.astype(str).str.strip().eq("Inconnu")
    unknown_sc_count = int(unknown_mask.sum())
    sc_known = sc_series[~unknown_mask]
    sous_all_known = df_counts(sc_known, top=None, name_key="sous_classification")
    fig_sous_pie = pie_top_other(sous_all_known, "sous_classification", "n", topn=10, title="Répartition des sous-classifications (hors 'Inconnu')")

    # Top opérateurs (créations) — hauteur dynamique
    op_create = df_counts(df["operator_create"], top=None, name_key="operateur")\
                .sort_values("n", ascending=True)

    fig_op_c = px.bar(
        op_create,
        x="n",
        y="operateur",
        orientation="h",
        title="Top opérateurs (créations de fiche)"
    )

    n_ops = len(op_create)
    fig_op_c.update_layout(
        yaxis=dict(categoryorder='total ascending'),
        height=min(2400, max(500, 22 * n_ops))  # ~22 px par opérateur, borné 500–2400
    )
    fig_op_c.update_traces(text=op_create["n"], textposition="outside", cliponaxis=False)

    # ------ Délais (clos) & SLA : EXCLURE "Inconnu" ------
    df_delays = pd.DataFrame(df.loc[pd.notna(df["delay_min"]), ["operator_close","delay_min"]]).copy()

    # 1) On s'assure que delay_min est bien en minutes (déjà le cas dans le pipeline, mais on bétonne)
    df_delays["delay_min"] = pd.to_numeric(df_delays["delay_min"], errors="coerce")

    # 2) Exclure opérateur "Inconnu" (lisibilité)
    df_delays = df_delays[df_delays["operator_close"].astype(str).str.strip() != "Inconnu"]

    if not df_delays.empty:
        # 3) Stats par opérateur (toujours en minutes)
        op_delay = (
            df_delays.groupby("operator_close")["delay_min"]
            .agg(mediane="median",
                p90=lambda s: np.percentile(s.dropna(), 90),
                n="count")
            .reset_index()
        )

        # 4) Cap visuel (anti-outliers) = P95 global des délais
        cap = float(np.percentile(df_delays["delay_min"].dropna(), 95))
        op_delay["mediane_capped"] = op_delay["mediane"].clip(upper=cap)
        op_delay["p90_capped"]     = op_delay["p90"].clip(upper=cap)
        # Tri par médiane cappée pour l’ordre d’affichage
        op_delay = op_delay.sort_values("mediane_capped", ascending=True)

        # 5) Hover clair en HH:MM + mention cappée si besoin
        def mk_hover(row):
            med = row["mediane"]; p90v = row["p90"]; n = int(row["n"])
            med_c = row["mediane_capped"]; p90_c = row["p90_capped"]
            med_txt = human_hhmm(med) + (" (cappé)" if med_c < med else "")
            p90_txt = human_hhmm(p90v) + (" (cappé)" if p90_c < p90v else "")
            return (
                f"Opérateur: {row['operator_close']}<br>"
                f"Médiane: {med_txt}<br>"
                f"P90: {p90_txt}<br>"
                f"N clos: {n}"
            )
        op_delay["hover"] = op_delay.apply(mk_hover, axis=1)

        # 6) Graphe barres horizontales (toujours en minutes pour l’échelle interne,
        #    mais les hovers montrent HH:MM)
        fig_op_delay = go.Figure()
        fig_op_delay.add_trace(go.Bar(
            x=op_delay["mediane_capped"],
            y=op_delay["operator_close"],
            orientation="h",
            name="Médiane",
            hovertext=op_delay["hover"],
            hoverinfo="text"
        ))
        fig_op_delay.add_trace(go.Scatter(
            x=op_delay["p90_capped"],
            y=op_delay["operator_close"],
            mode="markers",
            name="P90",
            hovertext=op_delay["hover"],
            hoverinfo="text"
        ))
        fig_op_delay.update_layout(
            title=f"Délais de traitement par opérateur (HH:MM) — cap visuel P95 = {human_hhmm(cap)}",
            xaxis_title="Durée (minutes, échelle interne)",
            yaxis_title="",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            height=400 + 20 * len(op_delay)  # 20px par opérateur supplémentaire
        )

        # 7) SLA (on garde pareil mais sans 'Inconnu')
        sla10 = df_delays.groupby("operator_close")["delay_min"].apply(lambda s: pct(int((s<=10).sum()), len(s)))
        sla30 = df_delays.groupby("operator_close")["delay_min"].apply(lambda s: pct(int((s<=30).sum()), len(s)))
        sla60 = df_delays.groupby("operator_close")["delay_min"].apply(lambda s: pct(int((s<=60).sum()), len(s)))
        sla_all = pd.concat([sla10, sla30, sla60], axis=1)
        sla_all.columns=["SLA_10min","SLA_30min","SLA_60min"]
        sla_all = sla_all.reset_index()

        fig_sla = go.Figure()
        fig_sla.add_trace(go.Bar(x=sla_all["SLA_10min"], y=sla_all["operator_close"], orientation="h", name="≤ 10 min"))
        fig_sla.add_trace(go.Bar(x=sla_all["SLA_30min"], y=sla_all["operator_close"], orientation="h", name="≤ 30 min"))
        fig_sla.add_trace(go.Bar(x=sla_all["SLA_60min"], y=sla_all["operator_close"], orientation="h", name="≤ 60 min"))
        n_ops_sla = len(sla_all)
        fig_sla.update_layout(
            title="Niveau de service de clôture par opérateur (SLA)",
            barmode="group",
            xaxis_title="%",
            yaxis_title="",
            yaxis=dict(categoryorder='total ascending'),
            height=min(3000, max(500, 35 * n_ops_sla)),  # plus d’espace vertical (~35 px par opérateur)
            bargap=0.3,        # espace entre opérateurs
            bargroupgap=0.15   # espace entre les 3 barres d’un opérateur
        )

        # 8) Table “Anomalies” (opérateurs > cap)
        anomalies = op_delay[(op_delay["mediane"] > cap) | (op_delay["p90"] > cap)].copy()
        anomalies = anomalies.sort_values(["mediane","p90"], ascending=False)
    else:
        fig_op_delay = go.Figure().update_layout(title="Délais de traitement par opérateur (minutes)")
        fig_sla      = go.Figure().update_layout(title="SLA de clôture par opérateur")
        anomalies = pd.DataFrame(columns=["operator_close","mediane","p90","n"])
        
    # ===== Indice de relance (friction) =====
    # Compte les lignes/commentaires contenant 'relance' (insensible à la casse)
    def count_relances(s):
        if not isinstance(s, str) or not s.strip():
            return 0
        return len(re.findall(r"\brelance(s)?\b", s, flags=re.I))

    relances = df["comment_text"].fillna("").apply(count_relances)
    df["relance_count"] = relances
    relance_any = int((relances > 0).sum())
    relance_rate = pct(relance_any, len(df))

    # Distribution
    relance_dist = relances.value_counts().sort_index().reset_index()
    relance_dist.columns = ["nb_relances", "n"]

    fig_relance = px.bar(relance_dist, x="nb_relances", y="n",
                        title="Distribution du nombre de relances par fiche",
                        labels={"nb_relances":"Relances détectées","n":"Nb de fiches"})
    fig_relance.update_layout(xaxis=dict(dtick=1))
    
    # TOP 10 des fiches par catégories (cartes enrichies)
    def top10_cards(df_all: pd.DataFrame, source_prefix: str, title: str) -> str:
        """
        1re ligne  : text (tronqué)
        2e ligne   : commentaires (tronqués, tooltip = complet)
        Sous la 2e : chips [Zone], [Carroyé], [Durée]
        """
        if df_all is None or df_all.empty:
            return f"<div class='card'><h3>{esc_html(title)}</h3><em>Aucune donnée</em></div>"

        src = df_all.get("source")
        if src is None:
            return f"<div class='card'><h3>{esc_html(title)}</h3><em>Aucune donnée</em></div>"

        mask = src.fillna("").astype(str).str.startswith(str(source_prefix))
        df_cat = df_all.loc[mask].copy()

        # Types/tri/sélection
        df_cat["delay_min"] = pd.to_numeric(df_cat["delay_min"], errors="coerce")
        df_cat["ts"] = pd.to_datetime(df_cat["ts"], errors="coerce", utc=True)
        df_cat = df_cat.dropna(subset=["delay_min", "ts"])
        df_cat = df_cat.sort_values("delay_min", ascending=False).head(10)

        if df_cat.empty:
            return f"<div class='card'><h3>{esc_html(title)}</h3><em>Aucune donnée</em></div>"

        # Helpers locaux
        def _area_tail(s):
            if isinstance(s, str):
                s = s.strip()
                return s.split("/")[-1].strip() if s else ""
            return ""

        def _safe_str(x):
            return x.strip() if isinstance(x, str) else ""

        cards = []
        for _, r in df_cat.iterrows():
            jour_heure = _fmt_day_hour(r.get("ts"))
            source     = r.get("source") or ""
            duree      = human_hhmm(r.get("delay_min"))

            # 1) Ligne titre = text (tronqué)
            desc       = r.get("text") or ""
            desc_short = _clip_text(desc, 260)

            # 2) Ligne commentaire = comment (tronqué + tooltip complet)
            comment_full_raw = (r.get("comment_text") or r.get("comment") or "")
            comment_full     = comment_full_raw.strip()
            comment_short    = _clip_text(comment_full, 240) if comment_full else "—"
            # On laisse de vrais "\n" dans title (les navigateurs les rendent généralement à la ligne)
            comment_tip      = comment_full.replace("\r\n", "\n").replace("\r", "\n")

            # 3) Zone / Carroyé / Durée (sous le commentaire)
            area_tail = _area_tail(r.get("area_desc"))
            carroye   = _safe_str(r.get("carroye"))

            chips = []
            if area_tail:
                chips.append(f"<span class='tc-chip tc-area'>{esc_html(area_tail)}</span>")
            if carroye:
                chips.append(f"<span class='tc-chip tc-car'>{esc_html(carroye)}</span>")
            chips.append(f"<span class='tc-chip tc-dur'><span class='tc-dur-label'>Durée :</span>{esc_html(duree)}</span>")
            chips_html = "".join(chips)

            cards.append(f"""
    <div class="tc">
    <div class="tc-hdr">
        <span class="tc-badge">{esc_html(source)}</span>
        <span class="tc-time">{esc_html(jour_heure)}</span>
    </div>

    <div class="tc-title">{esc_html(desc_short)}</div>

    <div class="tc-desc2" title="{esc_html(comment_tip)}">{esc_html(comment_short)}</div>

    <div class="tc-meta-row">
        {chips_html}
    </div>
    </div>
    """)

        return f"<div class='card'><h3>{esc_html(title)}</h3><div class='topcards'>{''.join(cards)}</div></div>"

    # Qualité de saisie — distributions des tailles (cap visuel P95)
    def _len_stats(col):
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        cap = float(np.percentile(s, 95)) if len(s) else np.nan
        out = int((s > cap).sum()) if len(s) else 0
        return s, cap, out

    desc_s,  desc_cap,  desc_out  = _len_stats("text_len")
    full_s,  full_cap,  full_out  = _len_stats("text_full_len")
    comm_s,  comm_cap,  comm_out  = _len_stats("comment_len")

    fig_len = go.Figure()
    if len(desc_s): fig_len.add_trace(go.Box(x=desc_s, name="Description"))
    if len(full_s): fig_len.add_trace(go.Box(x=full_s, name="Texte enrichi"))
    if len(comm_s): fig_len.add_trace(go.Box(x=comm_s, name="Commentaires"))

    # borne l'axe X au P95 max pour éviter que quelques valeurs extrêmes écrasent tout
    cap_len_max = np.nanmax([desc_cap, full_cap, comm_cap]) if not all(np.isnan([desc_cap, full_cap, comm_cap])) else None
    if cap_len_max and np.isfinite(cap_len_max):
        fig_len.update_xaxes(range=[0, cap_len_max])

    fig_len.update_layout(
        title=f"Qualité de saisie — distributions des tailles (caractères) — cap visuel P95 = {int(round(cap_len_max)) if cap_len_max and np.isfinite(cap_len_max) else 'N/A'}",
        xaxis_title="Nombre de caractères"
    )

    # Texte d'aide pour afficher les outliers écartés visuellement
    cap_len_txt = int(round(cap_len_max)) if (cap_len_max and np.isfinite(cap_len_max)) else "N/A"
    qual_hint = (
        f"Cap visuel au P95 = <strong>{cap_len_txt}</strong> caractères. "
        f"Valeurs au-delà (non visibles) — "
        f"Description : <strong>{desc_out}</strong> • "
        f"Texte enrichi : <strong>{full_out}</strong> • "
        f"Commentaires : <strong>{comm_out}</strong>."
    )
    
    # =======================
    # Amélioration / Qualité des données
    # =======================
    def _is_empty(s):
        return s.isna() | s.astype(str).str.strip().eq("")

    # Masques de données manquantes / faibles
    m_close_missing   = df["close_ts"].isna()
    m_carroye_missing = df["carroye"].isna() | df["carroye"].astype(str).str.strip().eq("")
    m_area_missing    = _is_empty(df["area_desc"])
    m_sc_unknown      = df["sous_classification"].isna() | df["sous_classification"].astype(str).str.strip().eq("") | df["sous_classification"].astype(str).str.strip().eq("Inconnu")
    m_op_close_miss   = _is_empty(df["operator_close"]) | df["operator_close"].astype(str).str.strip().eq("Inconnu")
    m_appelant_miss   = _is_empty(df["appelant"])

    # Compteurs & pourcentages
    quality_items = [
        ("Fiches non fermées",          int(m_close_missing.sum())),
        ("Sans carroyé",                int(m_carroye_missing.sum())),
        ("Sans zone (Area)",            int(m_area_missing.sum())),
        ("Sous-class. manquante/Inc.",  int(m_sc_unknown.sum())),
        ("Sans opérateur de clôture",   int(m_op_close_miss.sum())),
        ("Sans appelant",               int(m_appelant_miss.sum())),
    ]
    quality_table_df = pd.DataFrame([{
        "champ": name,
        "nb_manquants": n,
        "pct_manquants": pct(n, N)
    } for (name, n) in quality_items]).sort_values("pct_manquants", ascending=False)

    # Figure : Taux par type (barres horizontales)
    fig_missing_fields = px.bar(
        quality_table_df,
        x="pct_manquants", y="champ",
        orientation="h",
        title="Taux de champs manquants / faibles (en %)"
    )
    fig_missing_fields.update_traces(text=quality_table_df["pct_manquants"].map(lambda v: f"{v}%"), textposition="outside", cliponaxis=False)
    fig_missing_fields.update_layout(yaxis=dict(categoryorder='total ascending'), xaxis_title="%", yaxis_title="")

    # Score global de qualité (thermomètre)
    # ------------------------------------
    # 1) Poids ramenés sur 100 (proportionnels à 50/30/15/30/12/30)
    weights = {
        "close_missing":   29.9,  # fermetures
        "carroye_missing": 18.0,
        "area_missing":     9.0,
        "sc_unknown":      18.0,
        "op_close_miss":    7.2,
        "appelant_miss":   18.0,
    }

    # 2) % manquants (déjà calculés avec les masques)
    p_close = pct(int(m_close_missing.sum()), N)
    p_car   = pct(int(m_carroye_missing.sum()), N)
    p_area  = pct(int(m_area_missing.sum()), N)
    p_sc    = pct(int(m_sc_unknown.sum()), N)
    p_opc   = pct(int(m_op_close_miss.sum()), N)
    p_app   = pct(int(m_appelant_miss.sum()), N)

    # 3) A) Indice linéaire (historique, vraiment sur 100 — pas besoin de /sum(weights))
    raw_penalty = (
        weights["close_missing"]   * (p_close/100.0) +
        weights["carroye_missing"] * (p_car  /100.0) +
        weights["area_missing"]    * (p_area /100.0) +
        weights["sc_unknown"]      * (p_sc   /100.0) +
        weights["op_close_miss"]   * (p_opc  /100.0) +
        weights["appelant_miss"]   * (p_app  /100.0)
    )
    quality_score_linear = max(0.0, round(100.0 - raw_penalty, 1))

    # 3) B) Indice géométrique (plus strict, pénalise la multi-défaillance)
    import math
    eps = 1e-6
    presence = {
        "close_missing":   max(0.0, 1.0 - p_close/100.0),
        "carroye_missing": max(0.0, 1.0 - p_car  /100.0),
        "area_missing":    max(0.0, 1.0 - p_area /100.0),
        "sc_unknown":      max(0.0, 1.0 - p_sc   /100.0),
        "op_close_miss":   max(0.0, 1.0 - p_opc  /100.0),
        "appelant_miss":   max(0.0, 1.0 - p_app  /100.0),
    }
    w_exp = {k: v/100.0 for k, v in weights.items()}  # exposants qui somment à 1
    geo_log = sum(w_exp[k] * math.log(max(presence[k], eps)) for k in presence)
    quality_score_geom = round(100.0 * math.exp(geo_log), 1)

    # 3) C) Indice critique (fiches complètes sur les champs “indispensables”)
    must_have_ok = (~m_carroye_missing) & (~m_sc_unknown) & (~m_appelant_miss)
    quality_score_critical = round(100.0 * float(must_have_ok.mean()), 1)

    # 4) Choix de l’indice affiché sur la jauge
    #    -> Mets "linear" si tu veux rester indulgent ; "geom" pour être plus exigeant.
    score_mode = "geom"  # "geom" | "linear" | "critical"
    quality_score = (
        quality_score_geom if score_mode == "geom" else
        quality_score_linear if score_mode == "linear" else
        quality_score_critical
    )

    # 5) Jauge Plotly
    fig_quality_gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=quality_score,
        number={'suffix': " %"},
        gauge={
            'axis': {'range': [0, 100]},
            'bar': {'thickness': 0.25},
            'steps': [
                {'range': [0, 50],  'color': '#8B0000'},
                {'range': [50, 75], 'color': '#B8860B'},
                {'range': [75, 90], 'color': '#2E8B57'},
                {'range': [90,100], 'color': '#00A36C'}
            ],
            'threshold': {'line': {'color': '#fff', 'width': 3}, 'thickness': 0.75, 'value': quality_score}
        },
        title={'text': f"Indice global de qualité des prises en charge"}
    ))
    fig_quality_gauge.update_layout(height=380)
    
    # ======= Fiches parfaites / complétude indispensable =======
    # Champs indispensables: carroyé, zone (area), description, commentaires, sous-classification, appelant.

    # Masques "manquant" pour description & commentaires (taille = 0 -> manquant)
    m_desc_missing = pd.to_numeric(df["text_len"], errors="coerce").fillna(0).eq(0)
    m_comm_missing = pd.to_numeric(df["comment_len"], errors="coerce").fillna(0).eq(0)

    # Fiche parfaite = aucun manque sur les 6 champs indispensables
    m_perfect = (
        (~m_carroye_missing)
        & (~m_area_missing)
        & (~m_sc_unknown)
        & (~m_appelant_miss)
        & (~m_desc_missing)
        & (~m_comm_missing)
    )

    n_perfect = int(m_perfect.sum())
    pct_perfect = pct(n_perfect, N)

    # Donut global parfait / non parfait
    df_complete = pd.DataFrame({
        "etat": ["Fiches parfaites", "Fiches non parfaites"],
        "n": [n_perfect, max(0, N - n_perfect)]
    })
    fig_perfect_donut = px.pie(
        df_complete, names="etat", values="n", hole=0.55,
        title="Complétude globale — fiches parfaites (6 champs indispensables)"
    )
    fig_perfect_donut.update_traces(textinfo="percent+label")

    # Répartition selon le nombre de champs manquants (0..6)
    miss_matrix = pd.DataFrame({
        "carroyé":        m_carroye_missing.astype(int),
        "zone":           m_area_missing.astype(int),
        "sous-classif":   m_sc_unknown.astype(int),
        "appelant":       m_appelant_miss.astype(int),
        "description":    m_desc_missing.astype(int),
        "commentaires":   m_comm_missing.astype(int),
    })
    nb_miss = miss_matrix.sum(axis=1)
    dist_miss = nb_miss.value_counts().sort_index().reset_index()
    dist_miss.columns = ["nb_champs_manquants", "n"]
    fig_miss_dist = px.bar(
        dist_miss, x="nb_champs_manquants", y="n",
        title="Répartition par nombre de champs manquants (sur 6 indispensables)",
        labels={"nb_champs_manquants": "Champs manquants", "n": "Nb de fiches"},
    )
    fig_miss_dist.update_layout(xaxis=dict(dtick=1))

    # Manques par champ indispensable (barres horizontales)
    missing_required = pd.DataFrame({
        "champ": ["Carroyé", "Zone", "Sous-classification", "Appelant", "Description", "Commentaires"],
        "nb_manquants": [
            int(m_carroye_missing.sum()),
            int(m_area_missing.sum()),
            int(m_sc_unknown.sum()),
            int(m_appelant_miss.sum()),
            int(m_desc_missing.sum()),
            int(m_comm_missing.sum()),
        ],
    })
    missing_required["pct"] = missing_required["nb_manquants"].map(lambda n: pct(n, N))
    fig_miss_required = px.bar(
        missing_required.sort_values("pct", ascending=True),
        x="pct", y="champ", orientation="h",
        title="Taux de manque par champ indispensable (en %)"
    )
    fig_miss_required.update_traces(text=missing_required.sort_values("pct", ascending=True)["pct"].map(lambda v: f"{v}%"),
                                    textposition="outside", cliponaxis=False)
    fig_miss_required.update_layout(xaxis_title="%", yaxis_title="")

    # Manquants par opérateur (focus actionnable)
    # Attribution : on regroupe par operator_create (responsable de la saisie initiale)
    df_ops = pd.DataFrame({
        "operateur": df["operator_create"].fillna("Inconnu"),
        "non_ferme": m_close_missing.astype(int),
        "sans_carroye": m_carroye_missing.astype(int),
        "sans_area": m_area_missing.astype(int),
        "sc_inconnue": m_sc_unknown.astype(int),
        "sans_appelant": m_appelant_miss.astype(int),
    })
    op_missing = (
        df_ops.groupby("operateur")[["non_ferme","sans_carroye","sans_area","sc_inconnue","sans_appelant"]]
        .sum()
        .reset_index()
    )
    op_missing["total"] = op_missing[["non_ferme","sans_carroye","sans_area","sc_inconnue","sans_appelant"]].sum(axis=1)
    op_missing = op_missing.sort_values("total", ascending=True).tail(15)  # Top 15 pour lisibilité

    fig_missing_by_operator = go.Figure()
    for col, label in [
        ("non_ferme",    "Non fermées"),
        ("sans_carroye", "Sans carroyé"),
        ("sans_area",    "Sans zone"),
        ("sc_inconnue",  "Sous-class. inconnue"),
        ("sans_appelant","Sans appelant"),
    ]:
        fig_missing_by_operator.add_trace(go.Bar(
            x=op_missing[col],
            y=op_missing["operateur"],
            orientation="h",
            name=label
        ))
    fig_missing_by_operator.update_layout(
        title="Champs manquants par opérateur (saisie initiale) — cumul et détail",
        barmode="stack",
        yaxis=dict(categoryorder='total ascending'),
        xaxis_title="Nombre de fiches",
        height=min(3000, max(520, 28 * len(op_missing)))  # hauteur adaptative
    )

    # Mini KPI pour la section Amélioration (réutilise quality_table_df)
    quality_kpis = [
        ("Fiches non fermées",        f"{p_close}%"),
        ("Sans carroyé",              f"{p_car}%"),
        ("Sans zone",                 f"{p_area}%"),
        ("Sous-class. inc./vides",    f"{p_sc}%"),
        ("Sans opérateur de clôture", f"{p_opc}%"),
        ("Sans appelant",             f"{p_app}%"),
        ("Indice qualité (global)",   f"{quality_score}%")
    ]
    
    # Ajout des 3 variantes d'indice (après avoir calculé quality_score_linear / _geom / _critical)
    quality_kpis.extend([
        ("Indice qualité — linéaire",    f"{quality_score_linear}%"),
        ("Indice qualité — géométrique", f"{quality_score_geom}%"),
        ("Indice qualité — critique",    f"{quality_score_critical}%"),
    ])

    def fig_html(fig): return pio.to_html(fig, full_html=False, include_plotlyjs="cdn")

    out_dir = f"pcorg_report_{event.replace(' ','_')}_{year}"
    os.makedirs(out_dir, exist_ok=True)

    # Copie du CSS fourni
    css_target = os.path.join(out_dir, "report.css")
    try:
        if os.path.abspath(args.css) != os.path.abspath(css_target):
            import shutil; shutil.copy(args.css, css_target)
    except Exception as e:
        print(f"[WARN] Impossible de copier le CSS ({args.css}) -> {css_target} : {e}")

    parts = []
    parts.append("<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'/>")
    parts.append(f"<title>Rapport {event} {year}</title>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'/>")
    parts.append("<link rel='stylesheet' href='report.css'/>")
    parts.append("<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200' />")
    parts.append("<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css' integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=' crossorigin=''/>")
    parts.append("<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js' integrity='sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=' crossorigin=''></script>")
    parts.append("<script src='https://d3js.org/d3.v7.min.js'></script>")
    parts.append("<script src='https://unpkg.com/d3-cloud/build/d3.layout.cloud.js'></script>")
    parts.append("<style>.bubble{display:flex;align-items:center;justify-content:center;border-radius:50%;color:#fff;font-weight:700;border:2px solid rgba(255,255,255,.85);box-shadow:0 2px 8px rgba(0,0,0,.25);}</style>")

    parts.append("</head><body>")

    parts.append("<header>")
    parts.append(f"<h1>PC Organisation — {event} {year}</h1>")
    parts.append("<nav><ul>")
    parts.append("<li><a class='active' href='#'>Accueil</a></li>")
    parts.append("<li><a href='#pc-activity'>Activité PC</a></li>")
    parts.append("<li><a href='#site-activity'>Activité Site</a></li>")
    parts.append("<li><a href='#operators'>Opérateurs</a></li>")
    parts.append("<li><a href='#quality'>Qualité</a></li>")
    parts.append("</ul></nav>")
    parts.append("</header>")

    # KPIs (bandeau)
    parts.append("<div class='card'>")
    parts.append("<h3><span class='material-symbols-outlined'>insights</span> KPIs clés</h3>")
    parts.append("<div class='kpi-grid'>")
    for label,val in [
        ("Total fiches", f"{len(df)}"),
        # ("% incidents", f"{incident_rate}%"),
        ("% téléphone", f"{tel_share}%"),
        ("% radio", f"{radio_share}%"),
        ("Délai médian (clos)", human_td_minutes(median_delay)),
        ("P90 délai (clos)", human_td_minutes(p90_delay)),
        ("SLA 10 min", f"{SLA10}%"),
        ("SLA 30 min", f"{SLA30}%"),
        ("SLA 60 min", f"{SLA60}%"),
    ]:
        parts.append(f"<div class='kpi'><div class='kpi-label'>{label}</div><div class='kpi-value'>{val}</div></div>")
    parts.append("</div></div>")
    
    # Thermomètre global de qualité
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_quality_gauge)}")
    parts.append("<p class='hint'>Indice calculé à partir de champs clés pondérés (fermeture, carroyé, zone, sous-classification, opérateur de clôture, appelant). Objectif : tendre vers 100%.</p>")
    parts.append("</div>")
    
    # === Nuage de mots ===
    parts.append("<div class='card'>")
    parts.append("<h3><span class='material-symbols-outlined'>cloud</span>Nuage de mots</h3>")
    parts.append("<div id='wordcloud' style='width:100%;height:360px;'></div>")
    parts.append("<p class='hint'>Mots les plus fréquents après suppression des stopwords et termes sans valeur analytique.</p>")
    parts.append("<script>")
    parts.append(f"const WORDCLOUD_DATA = {json.dumps(wordcloud_words)};")
    parts.append(r"""
    (function(){
      const el = document.getElementById('wordcloud');
      if(!el || !Array.isArray(WORDCLOUD_DATA) || !WORDCLOUD_DATA.length){
        if(el){ el.innerHTML = "<em>Aucune donnée textuelle disponible.</em>"; }
        return;
      }
      function render(){
        const w = el.clientWidth || 760, h = el.clientHeight || 360;
        el.innerHTML = "";
        const maxN = d3.max(WORDCLOUD_DATA, d => d.n) || 1;
        const minN = d3.min(WORDCLOUD_DATA, d => d.n) || 1;
        const size = d3.scaleSqrt().domain([minN, maxN]).range([12, 52]); // tailles police
        const fill = d3.scaleOrdinal(d3.schemeTableau10);                 // couleurs

        const words = WORDCLOUD_DATA.map(d => ({text:d.t, size:size(d.n)}));

        d3.layout.cloud()
          .size([w, h])
          .words(words)
          .padding(2)
          .rotate(() => 0)  // pas de rotation, lisibilité
          .font("DEMO, sans-serif")
          .fontSize(d => d.size)
          .on("end", draw)
          .start();

        function draw(words){
          const svg = d3.select(el).append("svg")
            .attr("width", w).attr("height", h);

          const g = svg.append("g")
            .attr("transform", "translate("+ (w/2) +"," + (h/2) + ")");

          g.selectAll("text")
            .data(words)
            .enter().append("text")
              .style("font-family", "DEMO, sans-serif")
              .style("font-size", d => d.size + "px")
              .style("fill", (d,i) => fill(i%10))
              .attr("text-anchor", "middle")
              .attr("transform", d => "translate(" + [d.x, d.y] + ")")
              .text(d => d.text)
              .append("title").text(d => d.text);
        }
      }
      render();
      // re-render au resize
      let to=null; window.addEventListener("resize", ()=>{ clearTimeout(to); to=setTimeout(render, 150); });
    })();
    """)
    parts.append("</script>")
    parts.append("</div>")
    
    # Encadré guide de lecture
    parts.append("<div class='card'>")
    parts.append("<h3><span class='material-symbols-outlined'>menu_book</span> Guide de lecture des indicateurs</h3>")
    parts.append("<ul>")
    parts.append("<li><strong>Médiane</strong> : temps pour lequel 50% des incidents sont fermés plus vite et 50% plus lentement → reflète la situation typique.</li>")
    parts.append("<li><strong>P90</strong> : temps en dessous duquel 90% des incidents sont fermés → utile pour voir les cas extrêmes/longs sans être biaisé par une valeur aberrante.</li>")
    parts.append("<li><strong>SLA</strong> (Service Level Agreement) : part des incidents fermés sous un délai donné. Exemple : SLA 30 min = % d’incidents fermés en moins de 30 minutes.</li>")
    parts.append("</ul>")
    parts.append("<p class='hint'>Ces indicateurs permettent d’évaluer la réactivité du PC et d’identifier les marges de progrès.</p>")
    parts.append("</div>")

    # ======= 1 graphique par carte =======
    # Activité PC
    parts.append("<h2 id='pc-activity'>Activité du PC</h2>")
    parts.append(f"<div class='card'>{fig_html(fig_timeline)}</div>")
    parts.append(f"<div class='card'>{fig_html(fig_heat)}</div>")
    
    # Courbe de backlog, embolie pendant l'événement
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_backlog)}")
    parts.append("<p class='hint'>Backlog élevé/persistant = sous-capacité ou routage inefficace. Sert à caler le dimensionnement horaire.</p>")
    parts.append("</div>")
    
    # Recommandation staff
    parts.append("<div class='card'>")
    parts.append(f"<h3><span class='material-symbols-outlined'>group</span> Staffing conseillé</h3>")
    parts.append(f"<p class='hint'>{staffing_hint}</p>")
    parts.append("</div>")

    # Activité Site
    parts.append("<h2 id='site-activity'>Activité du site</h2>")
    parts.append(f"<div class='card'>{fig_html(fig_source)}</div>")
    parts.append(f"<div class='card'>{fig_html(fig_sous)}</div>")
    parts.append(f"<div class='card'>{fig_html(fig_sous_pie)}<p class='hint'>Sous-classification inconnue/non renseignée : <strong>{unknown_sc_count}</strong></p></div>")
    parts.append(f"<div class='card'>{fig_html(fig_area)}</div>")
    
    parts.append(
        "<div class='card'>"
        "<h3>Carte des appels</h3>"
        "<div id='car-map' style='width:100%;height:620px;border-radius:16px;overflow:hidden'></div>"
        f"<p class='hint'>Références sans coordonnées dans <code>grid_ref</code> : <strong>{missing_coords}</strong>.</p>"
        "<script>"
            f"const CAR_POINTS = {car_points_json};"
            """
            (function(){
            const map = L.map('car-map', {zoomControl:true, attributionControl:true});
            // Tuiles OpenStreetMap (légères, pas de token)
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; OpenStreetMap'
            }).addTo(map);

            // Si pas de points, centre sur le circuit du Mans
            const fallback = [47.95, 0.21];
            if (!CAR_POINTS.length) {
                map.setView(fallback, 13);
                return;
            }

            // Binning simple pour les couleurs (4 classes)
            const ns = CAR_POINTS.map(p => p.n);
            const minN = Math.min(...ns), maxN = Math.max(...ns);
            const q1 = minN + 0.25*(maxN-minN);
            const q2 = minN + 0.50*(maxN-minN);
            const q3 = minN + 0.75*(maxN-minN);

            // --- 1–2 = désaturé + plus petit ---
            function colorFor(n){
                if (n <= 2) return 'rgba(108,117,125,0.45)'; // gris discret et translucide
                return (n>=q3) ? '#d73027' : (n>=q2) ? '#fc8d59' : (n>=q1) ? '#fee08b' : '#91bfdb';
            }
            function borderFor(n){
                return (n <= 2) ? 'rgba(255,255,255,0.35)' : 'rgba(255,255,255,0.85)';
            }
            function sizeFor(n){
                if (n <= 2) {
                    const s = 22 + 2*Math.sqrt(n); // un peu plus petit pour 1–2
                    return [s, s];
                }
                const s = 28 + 3*Math.sqrt(n);
                return [s, s];
            }

            // Échappement HTML simple
            function esc(s){
                return String(s)
                .replace(/&/g,'&amp;')
                .replace(/</g,'&lt;')
                .replace(/>/g,'&gt;')
                .replace(/"/g,'&quot;')
                .replace(/'/g,'&#39;');
            }

            const markers = CAR_POINTS.map(p => {
                const [w,h] = sizeFor(p.n);
                const html = `<div class="bubble" style="background:${colorFor(p.n)};border:2px solid ${borderFor(p.n)};width:${w}px;height:${h}px;">${p.n}</div>`;
                const icon = L.divIcon({html, className:'', iconSize:[w,h], iconAnchor:[w/2,h/2]});

                // Contenu du popup : liste scrollable des descriptions
                const listHtml = (p.descs && p.descs.length)
                    ? `<div style="max-height:300px;overflow:auto;margin-top:6px">
                        <ol style="padding-left:18px;margin:0">
                        ${p.descs.map(d => `<li style="margin:6px 0">${esc(d)}</li>`).join('')}
                        </ol>
                    </div>`
                    : '<div style="margin-top:6px"><em>Aucune description</em></div>';

                const popupHtml = `<div><b>${esc(p.ref)}</b> — ${p.n} appels${listHtml}</div>`;

                return L.marker([p.lat, p.lon], {icon})
                        .bindTooltip(`${p.ref} — ${p.n}`)
                        .bindPopup(popupHtml, {maxWidth: 420});
            });

            const group = L.featureGroup(markers).addTo(map);
            map.fitBounds(group.getBounds(), {padding:[24,24]});
            })();
            """
            "</script>"
        "</div>"
    )
       
    # Point d'attractions
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_hot)}")
    parts.append("<p class='hint'>Cibles prioritaires = bulles grandes, chaudes (score élevé). Action pré-événement : checks de terrain, briefings, kits prêt-à-poser.</p>")
    parts.append("</div>")
    
    # Relances
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_relance)}")
    parts.append("<p class='hint'>Un taux élevé de relances suggère un manque d’autorité décisionnelle, d’information initiale, ou de coordination inter-services.</p>")
    parts.append("</div>")
    
    # Services contactés
    parts.append("<h2 id='services'>Services contactés</h2>")

    # Répartition des services
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_svc_split)}")
    parts.append("<p class='hint'>Observer le mix de sollicitations. Un service très sollicité mérite un point de contact dédié ou un canal prioritaire.</p>")
    parts.append("</div>")

    # P90 des délais par service
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_svc_p90)}")
    parts.append("<p class='hint'>Prioriser les services au P90 élevé avec un N clos important.</p>")
    parts.append("</div>")

    # SLA (10/30/60) par service
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_svc_sla)}")
    parts.append("<p class='hint'>Le SLA révèle la tenue des engagements : cibler les services sous-performants pour co-définir des seuils réalistes.</p>")
    parts.append("</div>")
    
    # Intervenants
    parts.append("<h2 id='intervenants'>Intervenants</h2>")

    # KPIs
    parts.append("<div class='card'>")
    parts.append("<h3><span class='material-symbols-outlined'>diversity_3</span> Engagements des intervenants</h3>")
    parts.append("<div class='kpi-grid'>")
    parts.append(f"<div class='kpi'><div class='kpi-label'>Total engagements</div><div class='kpi-value'>{total_engagements}</div></div>")
    parts.append(f"<div class='kpi'><div class='kpi-label'>Moyenne par fiche</div><div class='kpi-value'>{avg_interv_per_fiche}</div></div>")
    parts.append("</div>")
    parts.append("</div>")
    
    # Camembert global intervenants
    parts.append(f"<div class='card'>{fig_html(fig_interv_pie)}</div>")

    # Graphiques
    parts.append(f"<div class='card'>{fig_html(fig_interv_top)}</div>")
    parts.append(f"<div class='card'>{fig_html(fig_interv_levels)}</div>")
    
    # Appelants
    parts.append("<h2 id='appelants'>Appelants</h2>")

    # KPI global
    parts.append("<div class='card'>")
    parts.append("<h3><span class='material-symbols-outlined'>call</span> Analyse des appelants</h3>")
    parts.append("<div class='kpi-grid'>")
    parts.append(f"<div class='kpi'><div class='kpi-label'>Appelants uniques</div><div class='kpi-value'>{n_appelants_uniques}</div></div>")
    parts.append(f"<div class='kpi'><div class='kpi-label'>Fiches sans appelant</div><div class='kpi-value'>{n_appelants_missing}</div></div>")
    parts.append(f"<div class='kpi'><div class='kpi-label'>% sans appelant</div><div class='kpi-value'>{p_appelants_missing}%</div></div>")
    parts.append("</div>")
    parts.append("<p class='hint'>Un appelant correspond à la personne ayant déclenché la fiche (champ <code>content_category.appelant</code>).</p>")
    parts.append("</div>")

    # Graphiques
    parts.append(f"<div class='card'>{fig_html(fig_appelants_top)}</div>")
    parts.append(f"<div class='card'>{fig_html(fig_appelants_treemap)}{appelants_table_html}</div>")
    
    # Canaux
    parts.append(f"<div class='card'>{fig_html(fig_chan)}</div>")

    # Opérateurs
    parts.append("<h2 id='operators'>Opérateurs</h2>")
    
    # FCR bouclage au premier contact
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_fcr)}")
    parts.append("<p class='hint'>FCR = même opérateur ouvre et clôture en ≤ 30 min (paramétrable). Bon proxy d’autonomie et de fluidité.</p>")
    parts.append("</div>")
    
    # Stats opérateurs
    parts.append(f"<div class='card'>{fig_html(fig_op_c)}</div>")
    parts.append(f"<div class='card'>{fig_html(fig_op_delay)}</div>")
    if not anomalies.empty:
        # petit rendu HTML simple
        an_rows = []
        for _, r in anomalies.iterrows():
            an_rows.append(
                f"<tr><td>{r['operator_close']}</td>"
                f"<td>{human_hhmm(r['mediane'])}</td>"
                f"<td>{human_hhmm(r['p90'])}</td>"
                f"<td>{int(r['n'])}</td></tr>"
            )
        parts.append(
            "<div class='card'><h3>Opérateurs avec délais > cap (P95)</h3>"
            "<table class='report-table' cellspacing='0' cellpadding='4'>"
            "<tr><th>Opérateur</th><th>Médiane</th><th>P90</th><th>N clos</th></tr>"
            + "\n".join(an_rows) +
            "</table></div>"
        )
    parts.append(f"<div class='card'>{fig_html(fig_sla)}</div>")

    # Qualité
    parts.append("<h2 id='quality'>Qualité de saisie des fiches</h2>")
    
    # Carte: KPI fiches parfaites
    parts.append(
        "<div class='card'>"
        f"<h3><span class='material-symbols-outlined'>verified</span> Fiches parfaites</h3>"
        f"<div class='kpi-grid'>"
        f"<div class='kpi'><div class='kpi-label'>Nombre</div><div class='kpi-value'>{n_perfect}</div></div>"
        f"<div class='kpi'><div class='kpi-label'>Part</div><div class='kpi-value'>{pct_perfect}%</div></div>"
        f"<div class='kpi'><div class='kpi-label'>Total fiches</div><div class='kpi-value'>{N}</div></div>"
        f"</div>"
        "<p class='hint'>Une fiche est dite <em>parfaite</em> si <strong>carroyé</strong>, <strong>zone</strong>, <strong>description</strong>, <strong>commentaires</strong>, <strong>sous-classification</strong> et <strong>appelant</strong> sont renseignés.</p>"
        "</div>"
    )

    # Donut parfait / non parfait
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_perfect_donut)}")
    parts.append("</div>")

    # Barres: manques par champ indispensable
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_miss_required)}")
    parts.append("<p class='hint'>Prioriser les champs avec un taux de manque élevé pour augmenter rapidement la part de fiches parfaites.</p>")
    parts.append("</div>")

    # Barres: distribution par nombre de champs manquants
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_miss_dist)}")
    parts.append("<p class='hint'>Cette distribution montre si les fiches “presque complètes” (1–2 manques) dominent, ou si l’on a surtout des fiches très incomplètes (4+ manques).</p>")
    parts.append("</div>")
    
    # Top 10 des fiches
    parts.append(top10_cards(df, "PCO.Secours",   "Top 10 durée — Secours"))
    parts.append(top10_cards(df, "PCO.Securite",  "Top 10 durée — Sécurité"))
    parts.append(top10_cards(df, "PCO.Technique", "Top 10 durée — Technique"))
    parts.append(top10_cards(df, "PCO.Information", "Top 10 durée — Information"))
    
    # Moustache qualité de saisie
    parts.append(
        "<div class='card'>"
        f"{fig_html(fig_len)}"
        f"<p class='hint'>{qual_hint}</p>"
        "</div>"
    )
    
    # =======================
    # SECTION — Amélioration
    # =======================
    parts.append("<h2 id='amelioration'>Amélioration de la qualité des données</h2>")

    # KPIs qualité (mini-cards)
    parts.append("<div class='card'>")
    parts.append("<h3><span class='material-symbols-outlined'>build</span> Indicateurs de qualité (manquants / faibles)</h3>")
    parts.append("<div class='kpi-grid'>")
    for label, val in quality_kpis:
        parts.append(f"<div class='kpi'><div class='kpi-label'>{label}</div><div class='kpi-value'>{val}</div></div>")
    parts.append("</div>")
    parts.append("<p class='hint'>Les pourcentages sont calculés sur l’ensemble des fiches. Une valeur élevée signifie un manque à corriger dans la saisie.</p>")
    parts.append("</div>")

    # Taux par type (barres horizontales)
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_missing_fields)}")
    parts.append("<p class='hint'>Prioriser les champs à fort taux de manque. Une action de formation ciblée peut faire progresser rapidement l’indice global.</p>")
    parts.append("</div>")

    # Manquants par opérateur (stack bar)
    parts.append("<div class='card'>")
    parts.append(f"{fig_html(fig_missing_by_operator)}")
    parts.append("<p class='hint'>Focus “opérateur” pour adresser des retours concrets (rappels de procédure, contrôles à la clôture, etc.).</p>")
    parts.append("</div>")

    # Tableau détaillé des champs manquants
    if not quality_table_df.empty:
        rows = "\n".join(
            f"<tr><td>{r['champ']}</td><td>{int(r['nb_manquants'])}</td><td>{r['pct_manquants']}%</td></tr>"
            for _, r in quality_table_df.iterrows()
        )
        parts.append(
            "<div class='card'>"
            "<h3>Détail des champs manquants</h3>"
            "<table class='mini-table' cellspacing='0' cellpadding='0'>"
            "<thead><tr><th>Champ</th><th>Nb manquants</th><th>%</th></tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
            "</div>"
        )

    parts.append(f"<footer>Généré par TITAN</footer>")
    parts.append("</body></html>")

    out_dir = f"pcorg_report_{event.replace(' ','_')}_{year}"
    out_html = os.path.join(out_dir, "index.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    print(f"✅ Rapport généré : {out_html}")
    print(f"🧾 CSS utilisé : {css_target}")
    
    # ============ DUPLICATION DANS LE DOSSIER DE L'ÉVÉNEMENT (site global) ============
    # Objectif : créer {analysis_{safe_event_name}_{year}}/pcorg.html
    # qui est une copie du rapport avec l'entête/nav harmonisés (onglet PC Org actif)
    # et un <base> identique aux autres pages du site global.

    # 1) safe_event_name (mêmes règles que ton script global)
    def make_safe_event_name(raw: str) -> str:
        # même logique: autorise alnum + '_' et remplace espaces par '_'
        s = (raw or "").strip()
        s = "".join(c for c in s if c.isalnum() or c in (" ", "_")).rstrip().replace(" ", "_")
        return s

    # (option) mapping comme dans le global si tu veux forcer certains libellés
    event_name_overrides = {
        "LMC": "LE_MANS_CLASSIC",
        "GPE": "GP_EXPLORER",
        "SBK": "SUPERBIKE"
    }
    display_event = event_name_overrides.get(event, event)
    safe_event_name = make_safe_event_name(display_event)

    # 2) dossier cible du site global (même convention que le script global)
    site_dir = f"analysis_{safe_event_name}_{year}"
    os.makedirs(site_dir, exist_ok=True)

    # 3) on part de l'HTML complet qu'on vient de générer (dans `parts`)
    full_html = "\n".join(parts)

    # 4) on fabrique un en-tête harmonisé (nav complète, onglet PC Org actif, <base> OK)
    #    NB : on garde les mêmes CDN (plotly, leaflet, d3) que l’index autonome.
    new_head_and_header = f"""<!DOCTYPE html>
        <html lang='fr'>
        <head>
        <meta charset='utf-8'/>
        <meta name='viewport' content='width=device-width, initial-scale=1'/>
        <title>PC Organisation – {event} {year}</title>
        <link rel='stylesheet' href='report.css'/>
        <link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200' />
        <link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css' integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=' crossorigin=''/>
        <script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js' integrity='sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=' crossorigin=''></script>
        <script src='https://d3js.org/d3.v7.min.js'></script>
        <script src='https://unpkg.com/d3-cloud/build/d3.layout.cloud.js'></script>
        <style>.bubble{{display:flex;align-items:center;justify-content:center;border-radius:50%;color:#fff;font-weight:700;border:2px solid rgba(255,255,255,.85);box-shadow:0 2px 8px rgba(0,0,0,.25);}}</style>
        </head>
        <body>
        <header>
            <h1>PC Organisation — {event} {year}</h1>
            <a class='back-link' href='index.html'><span class='material-symbols-outlined'>arrow_back</span>Retour à l'accueil</a>
            <nav>
            <ul>
                <li><a href='index.html'>Accueil</a></li>
                <li><a href='frequentation.html'>Fréquentation</a></li>
                <li><a href='portes.html'>Portes</a></li>
                <li><a href='meteo.html'>Météo</a></li>
                <li><a href='prediction.html'>Prédictions</a></li>
                <li><a class='active' href='pcorg.html'>PC Org</a></li>
            </ul>
            </nav>
        </header>
        <script>
            if (location.protocol === 'file:') {{
            var baseElem = document.getElementById('base-url');
            if (baseElem) {{ baseElem.removeAttribute('href'); }}
            }}
        </script>
        """

    # 5) on remplace l'entête/entête-nav d'origine par le nouveau
    #    -> tout ce qui va du début du document jusqu'à </header>
    embedded_html = re.sub(
        pattern=r"(?s)^.*?</header>",
        repl=new_head_and_header,
        string=full_html,
        count=1
    )

    # 6) on écrit pcorg.html à l’emplacement du site global
    pcorg_path = os.path.join(site_dir, "pcorg.html")
    with open(pcorg_path, "w", encoding="utf-8") as f:
        f.write(embedded_html)

    print(f"📎 Copie site global : {pcorg_path}")

if __name__ == "__main__":
    main()