import getpass

import psycopg2


def chiedi(domanda):
    url = getpass.getpass(domanda).strip()
    if not url.startswith(("postgres://", "postgresql://")):
        print("Non è un indirizzo di database (deve iniziare con postgresql://).")
        raise SystemExit
    return url


def guarda(nome, url):
    try:
        c = psycopg2.connect(url, connect_timeout=20)
        cur = c.cursor()
        cur.execute("SELECT current_database()")
        print("\n=== %s: database «%s» ===" % (nome, cur.fetchone()[0]))
        cur.execute("SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') "
                    "ORDER BY table_schema, table_name")
        tabelle = cur.fetchall()
        if not tabelle:
            print("Nessuna tabella.")
        for schema, t in tabelle:
            cur.execute('SELECT COUNT(*) FROM "%s"."%s"' % (schema, t))
            print("%-12s %-26s %6d righe" % (schema, t, cur.fetchone()[0]))
        c.close()
    except Exception as e:
        print("\n=== %s: connessione non riuscita: %s" % (nome, str(e).split("\n")[0]))


a = chiedi("1/2 External Database URL di symposium-db (non si vede), poi Invio: ")
b = chiedi("2/2 External Database URL di alexandria-db (non si vede), poi Invio: ")
guarda("PRIMO indirizzo", a)
guarda("SECONDO indirizzo", b)
