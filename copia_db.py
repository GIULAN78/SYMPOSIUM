import getpass
import os
import sys
from urllib.parse import urlsplit, urlunsplit

import psycopg2


def chiedi(domanda):
    url = getpass.getpass(domanda).strip()
    if not url.startswith(("postgres://", "postgresql://")):
        print("Quello che hai incollato non è un indirizzo di database (deve iniziare con postgresql://). Riprova dall'inizio.")
        raise SystemExit
    return url


old_url = chiedi("1/2 External Database URL di symposium-db (non si vede), poi Invio: ")
alex_url = chiedi("2/2 External Database URL di alexandria-db (non si vede), poi Invio: ")
nuovo_url = urlunsplit(urlsplit(alex_url)._replace(path="/symposium_db"))

try:
    c = psycopg2.connect(alex_url, connect_timeout=20)
    c.autocommit = True
    cur = c.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = 'symposium_db'")
    if cur.fetchone():
        print("Database symposium_db già presente.")
    else:
        cur.execute("CREATE DATABASE symposium_db")
        print("Database symposium_db creato.")
    c.close()
except Exception as e:
    print("Creazione del database non riuscita:", str(e).split("\n")[0])
    raise SystemExit

os.environ["DATABASE_URL"] = nuovo_url
sys.path.insert(0, os.getcwd())
try:
    import app as symposium
    print("Tabelle create nel nuovo database.")
except Exception as e:
    print("Creazione delle tabelle non riuscita:", str(e).split("\n")[0])
    raise SystemExit

try:
    vecchio = psycopg2.connect(old_url, connect_timeout=20)
    nuovo = psycopg2.connect(nuovo_url, connect_timeout=20)
except Exception as e:
    print("Connessione non riuscita:", str(e).split("\n")[0])
    raise SystemExit

cv = vecchio.cursor()
cn = nuovo.cursor()

cn.execute("SELECT COUNT(*) FROM utenti")
if cn.fetchone()[0] > 0:
    print("Il nuovo database contiene già dati: non copio per non sovrascriverli.")
    raise SystemExit

cv.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")
presenti = [r[0] for r in cv.fetchall()]
ordine = [
    "utenti", "locali", "tavoli", "iscrizioni", "messaggi", "valutazioni_locale",
    "valutazioni_criteri", "presenze", "compatibilita", "assenze", "notifiche",
    "preferiti", "locali_seguiti", "richieste", "amici", "bloccati", "segnalazioni",
    "messaggi_privati", "preferiti_utenti", "impostazioni", "movimenti_alx",
    "reset_password", "feedback_app",
]
tabelle = [t for t in ordine if t in presenti] + [t for t in presenti if t not in ordine]

esito = []
for t in tabelle:
    tab = '"%s"' % t
    try:
        cn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s", (t,))
        col_nuove = {r[0] for r in cn.fetchall()}
        if not col_nuove:
            esito.append((t, None, None, "manca nel nuovo database"))
            continue
        cv.execute("SELECT * FROM " + tab)
        nomi = [d[0] for d in cv.description]
        usa = [n for n in nomi if n in col_nuove]
        idx = [nomi.index(n) for n in usa]
        righe = cv.fetchall()
        if righe:
            q = "INSERT INTO %s (%s) VALUES (%s)" % (
                tab, ", ".join('"%s"' % n for n in usa), ", ".join(["%s"] * len(usa)))
            for r in righe:
                cn.execute(q, [r[i] for i in idx])
        if "id" in col_nuove:
            cn.execute("SELECT pg_get_serial_sequence(%s, 'id')", (t,))
            seq = cn.fetchone()[0]
            if seq:
                cn.execute("SELECT setval(%s, COALESCE((SELECT MAX(id) FROM " + tab + "), 1), (SELECT MAX(id) FROM " + tab + ") IS NOT NULL)", (seq,))
        cn.execute("SELECT COUNT(*) FROM " + tab)
        n_nuovo = cn.fetchone()[0]
        nuovo.commit()
        esito.append((t, len(righe), n_nuovo, "ok" if len(righe) == n_nuovo else "NON TORNA"))
    except Exception as e:
        nuovo.rollback()
        esito.append((t, None, None, "errore: " + str(e).split("\n")[0][:80]))

print()
print("%-24s %8s %8s  %s" % ("Tabella", "Vecchio", "Nuovo", "Esito"))
for t, a, b, stato in esito:
    print("%-24s %8s %8s  %s" % (t, "" if a is None else a, "" if b is None else b, stato))
print()
tutto = all(x[3] == "ok" for x in esito)
print("COPIA COMPLETATA: tutte le tabelle tornano." if tutto else "Qualche tabella non torna: incollami la tabella qui sopra.")
print("Il database vecchio non è stato toccato.")
vecchio.close()
nuovo.close()
