(function () {
    var box = document.getElementById("interessi-box");
    if (!box) { return; }
    var nascosto = document.getElementById("interessi");
    var altri = document.getElementById("interessi-altri");
    var caselle = box.querySelectorAll('input[type="checkbox"]');
    var form = nascosto.form;
    function norma(t) { return t.trim().toLowerCase(); }
    var noti = {};
    caselle.forEach(function (c) { noti[norma(c.value)] = c; });
    var avanzi = [];
    (nascosto.value || "").split(",").forEach(function (v) {
        var k = norma(v);
        if (!k) { return; }
        if (noti[k]) { noti[k].checked = true; } else { avanzi.push(v.trim()); }
    });
    altri.value = avanzi.join(", ");
    function componi() {
        var v = [];
        caselle.forEach(function (c) { if (c.checked) { v.push(c.value); } });
        if (altri.value.trim()) { v.push(altri.value.trim()); }
        nascosto.value = v.join(", ");
    }
    if (form) { form.addEventListener("submit", componi); }
})();
