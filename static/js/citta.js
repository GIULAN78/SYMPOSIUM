(function () {
    var campi = document.querySelectorAll('input[name="citta"]');
    if (!campi.length) { return; }
    var lista = document.createElement("datalist");
    lista.id = "elenco-comuni";
    document.body.appendChild(lista);
    var comuni = null;
    function norma(t) { return t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }
    function carica() {
        if (comuni) { return Promise.resolve(); }
        return fetch("/static/comuni.json").then(function (r) { return r.json(); }).then(function (d) {
            comuni = d.map(function (c) { return [c[0], c[1], norma(c[0])]; });
        }).catch(function () { comuni = []; });
    }
    function aggiorna(campo) {
        var q = norma(campo.value.trim());
        if (q.length < 2) { lista.innerHTML = ""; return; }
        carica().then(function () {
            var a = [], b = [];
            for (var i = 0; i < comuni.length; i++) {
                var p = comuni[i][2].indexOf(q);
                if (p === 0) { a.push(comuni[i]); } else if (p > 0) { b.push(comuni[i]); }
                if (a.length >= 12) { break; }
            }
            lista.innerHTML = "";
            a.concat(b).slice(0, 12).forEach(function (c) {
                var o = document.createElement("option");
                o.value = c[0];
                o.label = c[0] + (c[1] ? " (" + c[1] + ")" : "");
                lista.appendChild(o);
            });
        });
    }
    campi.forEach(function (campo) {
        campo.setAttribute("list", "elenco-comuni");
        campo.setAttribute("autocomplete", "off");
        campo.addEventListener("input", function () { aggiorna(campo); });
    });
})();
