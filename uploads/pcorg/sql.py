#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
import socket
import pyodbc

HOST = "10.34.0.4"
DB   = "AppHistoV4"
TIMEOUT = 5

INSTANCE_NAME = "SQLAPP"
SQL_BROWSER_PORT = 1434  # UDP

# Port dynamique qui marche aujourd'hui (fallback si Browser ne répond pas)
KNOWN_DYNAMIC_PORT = "65422"

# === USER & PASSWORD depuis variables d'environnement ===
USER = os.getenv("MSSQL_USER")


def pick_driver():
    drivers = [d for d in pyodbc.drivers()]
    for name in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server"):
        if name in drivers:
            return name
    raise RuntimeError(
        "Aucun driver ODBC SQL Server installé (18/17/SQL Server). "
        "Drivers trouvés : " + ", ".join(drivers)
    )


def resolve_port_via_sql_browser(host, instance, timeout=2.0):
    """
    Interroge le SQL Browser (UDP 1434) pour connaître le port TCP de l'instance nommée.
    Retourne le port (str) ou None.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)

        # Message SSRP : 0x03 + nom_instance + 0x00
        payload = b"\x03" + instance.encode("ascii") + b"\x00"
        sock.sendto(payload, (host, SQL_BROWSER_PORT))

        data, _ = sock.recvfrom(4096)
        text = data.decode("ascii", errors="ignore")

        # Exemple de réponse : 'ServerName;SRV-PRYSM;InstanceName;SQLAPP;IsClustered;No;Version;14.0.2085.1;tcp;65422;np;\\SRV-PRYSM\pipe\MSSQL$SQLAPP\sql\query;'
        parts = text.split(";")
        for i, part in enumerate(parts):
            if part.lower() == "tcp" and i + 1 < len(parts):
                port = parts[i + 1]
                if port.isdigit():
                    return port
    except Exception as e:
        print(f"SQL Browser indisponible ou bloqué (info debug) : {e}")
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return None


def build_conn_str(driver, server, user, password):
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={DB}",
        f"UID={user}",
        f"PWD={password}",
    ]
    parts.append(f"Connection Timeout={TIMEOUT}")
    if driver.startswith("ODBC Driver 18") or driver.startswith("ODBC Driver 17"):
        parts.append("Encrypt=no")
    return ";".join(parts) + ";"


def try_connect(conn_str):
    return pyodbc.connect(conn_str)


def print_table_columns(cur, schema: str, table: str):
    """Affiche les colonnes d'une table (nom, type, nullabilité)."""
    print(f"\n=== Colonnes de {schema}.{table} ===")
    cur.execute("""
        SELECT 
            COLUMN_NAME,
            DATA_TYPE,
            IS_NULLABLE,
            CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, (schema, table))
    rows = cur.fetchall()
    if not rows:
        print("  (aucune colonne trouvée, vérifier le nom de la table)")
        return

    for name, dtype, is_null, char_len in rows:
        if char_len is not None and char_len > 0 and dtype in ("nvarchar", "varchar", "char", "nchar"):
            type_str = f"{dtype}({char_len})"
        else:
            type_str = dtype
        null_str = "NULL" if is_null == "YES" else "NOT NULL"
        print(f"  - {name:<30} {type_str:<20} {null_str}")


def main():
    # Vérif USER
    global USER
    if not USER:
        USER = input("MSSQL_USER non défini, saisir le login SQL : ").strip()

    # Mot de passe depuis l'environnement, fallback interactif
    password = os.getenv("MSSQL_PASSWORD")
    if not password:
        password = input("Mot de passe SQL Server (visible) (MSSQL_PASSWORD non défini) : ")

    print(f"Test connexion SQL -> DB={DB} avec l'utilisateur '{USER}'")

    driver = pick_driver()
    print(f"Driver ODBC détecté : {driver}")

    server_candidates = []

    # 1️⃣ Tentative via SQL Browser (prioritaire)
    print(f"\nInterrogation du SQL Browser (UDP {SQL_BROWSER_PORT}) pour l'instance '{INSTANCE_NAME}'...")
    resolved_port = resolve_port_via_sql_browser(HOST, INSTANCE_NAME)
    if resolved_port:
        print(f"Port résolu via SQL Browser : {resolved_port}")
        server_candidates.append(f"{HOST},{resolved_port}")
    else:
        print("SQL Browser injoignable ou ne retourne pas de port, on passe sur les ports connus.")

    # 2️⃣ Port dynamique connu qui marche aujourd'hui (fallback robuste)
    if KNOWN_DYNAMIC_PORT:
        server_candidates.append(f"{HOST},{KNOWN_DYNAMIC_PORT}")

    # 3️⃣ Quelques fallbacks au cas où
    server_candidates.extend([
        f"{HOST},1433",
        f"{HOST}\\{INSTANCE_NAME}",
        f"{HOST}\\SQLEXPRESS",
        f"{HOST}\\SQLSERVER",
        f"{HOST}",
    ])

    last_err = None
    for server in server_candidates:
        conn_str = build_conn_str(driver, server, USER, password)
        safe_conn_str = conn_str.replace(password, "****")

        print(f"\n>>> Tentative: SERVER={server}")
        print(f"Chaîne de connexion (sans mot de passe) : {safe_conn_str}")

        try:
            with try_connect(conn_str) as conn:
                cur = conn.cursor()
                # Infos serveur
                cur.execute("SELECT @@SERVERNAME, @@SERVICENAME, @@VERSION")
                name, svc, ver = cur.fetchone()
                print("Connexion OK ✅")
                print(f"Serveur: {name} | Service: {svc}\n{ver}")

                # 🔎 Colonnes de dbo.Events
                print_table_columns(cur, "dbo", "Events")

                # 🔎 Colonnes de dbo.UserMessages
                print_table_columns(cur, "dbo", "UserMessages")

                print("\nTerminé ✅")
                return
        except Exception as e:
            last_err = e
            print(f"Échec: {e}")

    print("\nToutes les tentatives ont échoué ❌")
    if last_err:
        print(f"\nDernière erreur brute renvoyée par ODBC : {last_err}")


if __name__ == "__main__":
    try:
        main()
    except pyodbc.Error as e:
        print("\n[ERREUR SQL/ODBC]")
        print(e)
        sys.exit(3)
    except Exception as e:
        print("\n[ERREUR INATTENDUE]")
        print(e)
        sys.exit(4)
