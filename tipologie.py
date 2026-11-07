_ELENCO = [
    ("Serate e compagnia", [
        ("convivio-estia", "Convivio di Estia", "🔥", "Cena tra amici, semplice e senza pretese.", False),
        ("xenia", "Xenia", "🏺", "Per chi è da poco in città: l'ospitalità prima di tutto.", False),
        ("baccanale", "Baccanale", "🍇", "Serata scatenata, musica e brindisi.", False),
        ("saturnali", "Saturnali", "🎁", "Festa goliardica con allegria e piccoli doni.", False),
        ("consesso-afrodite", "Consesso di Afrodite", "🌹", "Serata per single e nuovi incontri.", False),
        ("sissizio", "Sissizio", "🛡️", "Cena tra compagni e colleghi: pasto sobrio, ognuno contribuisce.", False),
        ("convito-olimpia", "Convito ad Olimpia", "🏅", "Cena tra compagni di squadra.", False),
    ]),
    ("Muse e cultura", [
        ("parnaso", "Banchetto sul Parnaso", "📚", "Libri, arte e dibattiti.", False),
        ("calliope", "Invitato di Calliope", "✒️", "Poesia, scrittura e libri.", False),
        ("talia", "Invitato di Talia", "🎭", "Cabaret, risate e serate comiche.", False),
        ("euterpe", "Coro di Euterpe", "🎶", "Musica e karaoke.", False),
        ("clio", "Invitato di Clio", "🏛️", "Storia e archeologia.", False),
        ("urania", "Invitato di Urania", "🔭", "Scienza, stelle e divulgazione.", False),
        ("melpomene", "Invitato di Melpomene", "🎬", "Teatro e cinema.", False),
    ]),
    ("Sfide e giochi", [
        ("agone", "Agone", "⚔️", "Sfide e competizioni amichevoli.", False),
        ("palamede", "Tavola di Palamede", "🎲", "Giochi da tavolo.", False),
        ("delfi", "Oracolo di Delfi", "🔮", "Quiz ed enigmi.", False),
        ("atena", "Sfida di Atena", "🦉", "Giochi di strategia.", False),
        ("edipo", "Indagine di Edipo", "🧩", "Cena con delitto e giochi di mistero.", False),
        ("eracle", "Fatiche di Eracle", "💪", "Sport, trekking e partite da guardare insieme.", False),
        ("ermes-tregua", "Tregua di Ermes", "📵", "Serata senza telefoni: si parla guardandosi negli occhi.", False),
        ("pandora", "Dono di Pandora", "🎀", "Scambio di un piccolo dono.", False),
    ]),
    ("Mensa e territorio", [
        ("dioniso", "Libagione di Dioniso", "🍷", "Vino e degustazioni.", False),
        ("demetra", "Mensa di Demetra", "🌾", "Cucina tipica e regionale.", False),
        ("ambrosia", "Ambrosia e Nettare", "🍯", "Brunch e aperitivi.", False),
        ("eos", "Aurora di Eos", "🌅", "Colazioni.", False),
        ("agora", "Agorà", "🗣️", "Networking e incontri di lavoro.", False),
        ("ermes-lingua", "Lingua di Ermes", "🌍", "Scambio linguistico.", False),
        ("artemide", "Convivio di Artemide", "🏹", "Natura, trekking e animali.", False),
        ("ade", "Banchetto di Ade", "🕯️", "Cena al buio.", False),
    ]),
    ("Banchetti degli dei ⭐", [
        ("olimpo", "Banchetto sull'Olimpo", "⛰️", "Il banchetto degli dei, riservato ai Premium.", True),
        ("immortali", "Simposio degli Immortali", "👑", "Cene di gala per pochi.", True),
        ("zeus", "Convito di Zeus", "⚡", "Serate di alto livello.", True),
    ]),
]

GRUPPI = []
TIPOLOGIE = []
for _gruppo, _voci in _ELENCO:
    _lista = []
    for _slug, _nome, _emoji, _descr, _esclusiva in _voci:
        _t = {"slug": _slug, "nome": _nome, "emoji": _emoji, "descrizione": _descr,
              "esclusiva": _esclusiva, "gruppo": _gruppo}
        _lista.append(_t)
        TIPOLOGIE.append(_t)
    GRUPPI.append((_gruppo, _lista))
TIPOLOGIE_PER_SLUG = {_t["slug"]: _t for _t in TIPOLOGIE}
