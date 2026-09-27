#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_import_hsh.py — alimente la chaine /scan-report depuis la borne HSH.

La chaine scan-report est cablee sur un export Excel Sirius (`scan_import.py`).
Les parkings du stade (matchs de foot) n'ont pas d'export : les scans ne vivent
que dans la borne Handshake. Ce module interroge la borne, fabrique le meme
contrat `parsed` que `scan_import.parse_scan_xlsx`, puis reutilise TELS QUELS
les constructeurs et l'ecriture de `scan_import` — aucune logique de document
n'est redupliquee ici.

    borne HSH --> collecter() --> build_parsed() --> scan_import.resolve_features
                                                 --> scan_import.build_*_doc
                                                 --> scan_import.archive_and_replace

Usage :
    python scan_import_hsh.py --event "LE MANS FC-RC LENS" --year 2026 \
        --jour 2026-09-13 --race "2026-09-13T17:15:00"
    python scan_import_hsh.py ... --dry-run     # n'ecrit rien, affiche le bilan
"""

import argparse
import datetime
import socket
import sys
from collections import defaultdict

import scan_import
from live_controle import (
    HSH_IP, HSH_PORT, CONNECT_TIMEOUT, READ_TIMEOUT_TRANSACTIONS,
    OPTION_TRANSACTIONS, DEFAULT_TX_ISSUER,
    build_transactions_xml, parse_transactions,
    envoyer_et_recevoir, encapsuler_transactions,
)

# ---------------------------------------------------------------------------
# Perimetre : les 3 parkings du stade, crees cote Skidata en septembre 2026.
# ---------------------------------------------------------------------------
#
# On indexe sur l'Area ID et non sur le nom : il existe deja un "P M1" du
# circuit (Area 634, vers ACCES MAISON HUNAUDIERES) qui n'a aucun rapport avec
# PARKING M1 (Area 1227). Un rapprochement par nom les confondrait.
PARKINGS_STADE = {
    '1227': 'PARKING M1',
    '1229': 'PARKING M2 - HOUX ANNEXE',
    '1231': 'PARKING M3 - ANTARES SUD',
}

# Libelle de zone commun : pour un match, les 3 acces parking constituent tout
# le perimetre controle. Ils sont donc modelises en unites `porte` (ce sont les
# seules que `build_frequentation_doc` et `build_portes_doc` retiennent), avec
# cette zone comme regroupement.
ZONE_STADE = 'PARKINGS STADE'

# Statuts que Skidata comptabilise comme un passage dans ses compteurs Area.
# Verifie par reconciliation sur les matchs du 13/09 et du 19/09 2026 : sur
# PARKING M1, 1121 (status 0) + 6 (status 107) + 3 (status 133) = 1130, soit
# exactement les `Entries` du compteur 1275. Les refus (106 pas de whitelist,
# 130 condition non remplie, 117 anti-repassage, 105 titre expire) n'y sont pas.
STATUTS_PASSAGE = {'0', '107', '133'}

SLOT_MINUTES = 15


# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------

def collecter(from_paris, to_paris, issuer=None, verbose=True):
    """Toutes les transactions de la borne entre deux bornes HEURE PARIS.

    ⚠ La borne interprete From/To en heure LOCALE Paris, pas en UTC (verifie :
    en lecture brute 4 scans seulement sortent de leur fenetre de validite,
    contre 881 si on ajoute +2h). On lui passe donc des dates locales telles
    quelles, sans conversion.

    ⚠ La pagination conserve From/To a CHAQUE page, en plus du curseur
    LastTransactionId. `collecte_forensic.collecter_et_stocker` les abandonne
    des la page 2 et collecte alors tout ce qui suit le curseur, bien au-dela
    de la fenetre demandee.
    """
    if issuer is None:
        issuer = DEFAULT_TX_ISSUER

    f_str = from_paris.strftime('%Y-%m-%dT%H:%M:%S')
    t_str = to_paris.strftime('%Y-%m-%dT%H:%M:%S')
    if verbose:
        print('Borne %s:%s  Issuer=%s  Option=%s' % (HSH_IP, HSH_PORT, issuer,
                                                     OPTION_TRANSACTIONS))
        print('Fenetre (heure Paris) : %s -> %s' % (f_str, t_str))

    vus = {}
    cursor = None
    page = 0

    with socket.create_connection((HSH_IP, HSH_PORT), timeout=CONNECT_TIMEOUT) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(READ_TIMEOUT_TRANSACTIONS)

        while True:
            page += 1
            req = build_transactions_xml(from_dt=f_str, to_dt=t_str,
                                         last_tx_id=cursor, issuer=issuer)
            resp = envoyer_et_recevoir(sock, encapsuler_transactions(req))
            if not resp:
                break

            not_complete, txs, max_txid = parse_transactions(resp)
            nouveaux = 0
            for tx in txs:
                tid = tx.get('transaction_id')
                if tid is not None and tid not in vus:
                    vus[tid] = tx
                    nouveaux += 1

            if not txs or nouveaux == 0 or not not_complete or max_txid is None:
                break
            cursor = str(max_txid)
            if page > 2000:
                raise RuntimeError('pagination_sans_fin')

        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    rows = sorted(vus.values(), key=lambda t: t.get('transaction_id') or 0)
    if verbose:
        print('%d transactions recuperees en %d page(s)' % (len(rows), page))
    return rows


# ---------------------------------------------------------------------------
# Adaptation au contrat `parsed` de scan_import
# ---------------------------------------------------------------------------

def _tronquer(dt):
    """Debut du creneau de 15 min contenant dt."""
    return dt.replace(minute=(dt.minute // SLOT_MINUTES) * SLOT_MINUTES,
                      second=0, microsecond=0)


def build_parsed(txs, perimetre=None, source_label=None):
    """Transactions HSH -> meme dict que `scan_import.parse_scan_xlsx`.

    Une unite `porte` par parking, creneaux de 15 min, `entree`/`sortie` en
    deltas. `autre` est toujours 0 : la borne donne toujours un sens (l'Area
    porte Entry/Exit), il n'existe donc pas ici de scan sans direction.

    Retourne (parsed, stats) ou `stats` porte le detail des refus, que le
    document ne represente pas et qui serait sinon perdu silencieusement.
    """
    perimetre = perimetre or PARKINGS_STADE

    buckets = defaultdict(lambda: {
        'devices': [],
        'slots': defaultdict(lambda: {'e': 0, 's': 0, 'a': 0}),
    })
    vus_devices = set()
    stats = {
        'total': 0,
        'passages': 0,
        'refus': 0,
        'refus_par_statut': defaultdict(int),
        'refus_par_zone': defaultdict(int),
        'hors_perimetre': 0,
        'sans_date': 0,
    }

    for tx in txs:
        area = tx.get('area') or {}
        aid = str(area.get('ID') or '')
        if aid not in perimetre:
            stats['hors_perimetre'] += 1
            continue

        nom = perimetre[aid]
        stats['total'] += 1

        brut = tx.get('date_paris') or tx.get('date_utc')
        if not brut:
            stats['sans_date'] += 1
            continue
        try:
            dt = datetime.datetime.strptime(str(brut)[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                dt = datetime.datetime.strptime(str(brut)[:19], '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                stats['sans_date'] += 1
                continue

        bucket = buckets[nom]
        cp = (tx.get('checkpoint') or {}).get('Name')
        if cp and (nom, cp) not in vus_devices:
            vus_devices.add((nom, cp))
            bucket['devices'].append(cp)

        statut = str(tx.get('status'))
        if statut not in STATUTS_PASSAGE:
            stats['refus'] += 1
            stats['refus_par_statut'][statut] += 1
            stats['refus_par_zone'][nom] += 1
            continue

        # Le sens vient des attributs Entry/Exit de l'Area, seule source fiable
        # (`direction` de live_controle en derive deja).
        if str(area.get('Exit')) == '1':
            sens = 's'
        else:
            sens = 'e'
        bucket['slots'][_tronquer(dt)][sens] += 1
        stats['passages'] += 1

    units = []
    for nom in sorted(buckets):
        data = buckets[nom]
        if not data['slots']:
            continue
        units.append(scan_import._make_unit(
            name=nom, kind='porte', zone=ZONE_STADE, porte=nom,
            devices=data['devices'], slots=data['slots']))

    if not units:
        raise scan_import.ScanImportError('hsh_sans_donnees', 400)

    all_ts = sorted({ts for u in units for ts in u['slots']})
    stats['refus_par_statut'] = dict(stats['refus_par_statut'])
    stats['refus_par_zone'] = dict(stats['refus_par_zone'])

    parsed = {
        'units': units,
        'period_start': all_ts[0],
        'period_end': all_ts[-1],
        'slot_count': len(all_ts),
        'has_autre': False,
        'source_file': source_label or 'HSH %s:%s' % (HSH_IP, HSH_PORT),
        'zone_count': 0,
        'porte_count': len(units),
        # Pas d'aide UAM ni de renfort PDA sur ce perimetre : ces notions sont
        # propres aux portes a tripodes de l'enceinte du circuit.
        'device_hours': {},
        'uam_devices': [],
    }
    return parsed, stats


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def importer(db, event, year, from_paris, to_paris, race_iso=None,
             issuer=None, imported_by=None, dry_run=False, verbose=True):
    """Collecte, construit et ecrit les 3 documents historique_controle.

    Retourne un dict de bilan. En `dry_run`, rien n'est ecrit.
    """
    txs = collecter(from_paris, to_paris, issuer=issuer, verbose=verbose)
    label = 'HSH %s -> %s (Issuer %s, Option %s)' % (
        from_paris.strftime('%Y-%m-%d %H:%M'), to_paris.strftime('%Y-%m-%d %H:%M'),
        issuer if issuer is not None else DEFAULT_TX_ISSUER, OPTION_TRANSACTIONS)
    parsed, stats = build_parsed(txs, source_label=label)

    resolved = scan_import.resolve_features(db, parsed['units'],
                                            event=event, year=year)
    # Ces parkings sont bien des lieux, mais ils n'existent dans aucune
    # collection geo du circuit : `resolve_features` rend `aucun`, ce qui est
    # exact (rattachement a faire le jour ou ils y seront ajoutes). On force en
    # revanche la categorie : `guess_category` rend 'porte' pour une unite de
    # kind 'porte', alors que ce sont des parkings vehicules.
    for unit in parsed['units']:
        res = resolved.setdefault(unit['key'], {})
        res['category'] = 'parking'
        res['category_source'] = 'hsh'

    docs = {
        'complet': scan_import.build_complet_doc(
            parsed, resolved, event, year, race_iso=race_iso,
            imported_by=imported_by),
        'frequentation': scan_import.build_frequentation_doc(
            parsed, event, year, race_iso=race_iso, resolved=resolved),
        'portes': scan_import.build_portes_doc(
            parsed, resolved, event, year, race_iso=race_iso),
    }

    bilan = {
        'event': event, 'year': int(year), 'race': race_iso,
        'transactions_recues': len(txs),
        'stats': stats,
        'unites': [{'name': u['name'], 'entree': u['total_entree'],
                    'sortie': u['total_sortie'], 'devices': u['devices'],
                    'creneaux': len(u['slots'])} for u in parsed['units']],
        'period_start': scan_import._iso(parsed['period_start']),
        'period_end': scan_import._iso(parsed['period_end']),
        'slot_count': parsed['slot_count'],
        'ecrit': False,
    }

    if dry_run:
        return bilan

    scan_import.ensure_indexes(db)
    for doc in docs.values():
        scan_import.archive_and_replace(db, doc, archived_by=imported_by,
                                        reason='import_hsh')
    bilan['ecrit'] = True
    return bilan


def _paris(raw):
    """'2026-09-13' ou '2026-09-13T17:15:00' -> datetime naif heure Paris."""
    raw = str(raw).strip().replace(' ', 'T')
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError('date illisible : %r' % raw)


def main():
    p = argparse.ArgumentParser(
        description='Importe les scans HSH des parkings du stade dans historique_controle')
    p.add_argument('--event', required=True, help="Nom cockpit de l'evenement")
    p.add_argument('--year', type=int, required=True)
    p.add_argument('--jour', help='Journee a collecter (AAAA-MM-JJ), 00h00 -> 23h59')
    p.add_argument('--from', dest='debut', help='Debut heure Paris (sinon --jour)')
    p.add_argument('--to', dest='fin', help='Fin heure Paris (sinon --jour)')
    p.add_argument('--race', help="Date/heure du coup d'envoi (heure Paris)")
    p.add_argument('--issuer', type=int, default=None)
    p.add_argument('--by', default='scan_import_hsh')
    p.add_argument('--dry-run', action='store_true', help="N'ecrit rien en base")
    args = p.parse_args()

    if args.jour:
        j = _paris(args.jour)
        debut = j.replace(hour=0, minute=0)
        fin = j.replace(hour=23, minute=59, second=59)
    elif args.debut and args.fin:
        debut, fin = _paris(args.debut), _paris(args.fin)
    else:
        p.error('fournir --jour, ou --from et --to')

    race_iso = scan_import.to_naive_paris_iso(args.race) if args.race else None

    # `live_controle` resout la base avec la meme regle que `app.py`
    # (TITAN_ENV=prod -> titan, sinon titan_dev) sans avoir a demarrer Flask.
    from live_controle import db, DB_NAME
    print('Base : %s' % DB_NAME)

    bilan = importer(db, args.event, args.year, debut, fin,
                     race_iso=race_iso, issuer=args.issuer,
                     imported_by=args.by, dry_run=args.dry_run)

    print()
    print('=' * 78)
    print('%s %s   race=%s' % (bilan['event'], bilan['year'], bilan['race']))
    print('=' * 78)
    s = bilan['stats']
    print('  transactions recues        : %d' % bilan['transactions_recues'])
    print('  dont sur le perimetre      : %d' % s['total'])
    print('  passages comptes           : %d' % s['passages'])
    print('  refus (hors comptage)      : %d  %s' % (s['refus'], s['refus_par_statut']))
    print('  periode                    : %s -> %s (%d creneaux)'
          % (bilan['period_start'], bilan['period_end'], bilan['slot_count']))
    print()
    for u in bilan['unites']:
        print('  %-28s entree=%-6d sortie=%-4d %d boitier(s) %s'
              % (u['name'], u['entree'], u['sortie'], len(u['devices']), u['devices']))
    print()
    print('  ECRIT EN BASE' if bilan['ecrit'] else '  DRY-RUN : rien ecrit')
    return 0


if __name__ == '__main__':
    sys.exit(main())
