/* pmv_editor.js -- editeur de messages 96 x 64 de la page /pmv.
 *
 * Portage de docs/pmv/editeur/PMV_Bitmap_Editor.html (v1.3.00) dans la charte
 * Cockpit. Les ALGORITHMES sont repris a l'identique (Bresenham, formes,
 * triangle, remplissage, polices 4x5 et 5x7, import BMP 1/4/8/24/32 bpp,
 * volet Copier, insertion, copier/coller) ; seuls changent le balisage (dans
 * templates/pmv.html, prefixe ped-), les icones (Material Symbols), les
 * dialogues (toasts Cockpit) et les evenements (pointeur, pour la tablette).
 * Le volet Reseau de l'original n'est pas repris : le registre Cockpit le
 * remplace, et plus rien ne part vers un resolveur DNS depuis le navigateur.
 *
 * API : window.PmvEditor = { afficher, getPNGBase64, chargerPNG, estVide,
 *                            estModifie, marquerPropre, onChange }
 */
(function () {
  "use strict";

  var racine = document.getElementById("ped-root");
  if (!racine) return;

  var W = 96, H = 64;

  function $(id) { return document.getElementById(id); }
  function toast(type, msg) { if (typeof window.showToast === "function") window.showToast(type, msg); }

  // ================================================================ constantes
  var PAL = [
    { h: "#ffffff", l: "Blanc" }, { h: "#c0c0c0", l: "Gris clair" }, { h: "#808080", l: "Gris" }, { h: "#000000", l: "Noir" },
    { h: "#ff0000", l: "Rouge" }, { h: "#ff8000", l: "Orange" }, { h: "#ffff00", l: "Jaune" },
    { h: "#00ff00", l: "Vert" }, { h: "#008000", l: "Vert fonce" }, { h: "#00ffff", l: "Cyan" },
    { h: "#0000ff", l: "Bleu" }, { h: "#000080", l: "Marine" }, { h: "#ff00ff", l: "Magenta" },
    { h: "#800080", l: "Violet" }, { h: "#804000", l: "Marron" }
  ];

  // Police 5x7 (reprise telle quelle)
  var F57 = {
    " ": [0, 0, 0, 0, 0, 0, 0], "!": [4, 4, 4, 4, 4, 0, 4], "\"": [10, 10, 0, 0, 0, 0, 0],
    "-": [0, 0, 0, 31, 0, 0, 0], ".": [0, 0, 0, 0, 0, 0, 4], "/": [1, 1, 2, 4, 8, 16, 16],
    "0": [14, 17, 19, 21, 25, 17, 14], "1": [4, 12, 4, 4, 4, 4, 14], "2": [14, 17, 1, 2, 4, 8, 31],
    "3": [31, 1, 2, 6, 1, 17, 14], "4": [2, 6, 10, 18, 31, 2, 2], "5": [31, 16, 30, 1, 1, 17, 14],
    "6": [6, 8, 16, 30, 17, 17, 14], "7": [31, 1, 2, 4, 8, 8, 8], "8": [14, 17, 17, 14, 17, 17, 14],
    "9": [14, 17, 17, 15, 1, 2, 12], ":": [0, 0, 4, 0, 0, 4, 0], "?": [14, 17, 1, 2, 4, 0, 4],
    "@": [14, 17, 1, 13, 21, 21, 14],
    "A": [14, 17, 17, 31, 17, 17, 17], "B": [30, 17, 17, 30, 17, 17, 30], "C": [14, 17, 16, 16, 16, 17, 14],
    "D": [28, 18, 17, 17, 17, 18, 28], "E": [31, 16, 16, 30, 16, 16, 31], "F": [31, 16, 16, 30, 16, 16, 16],
    "G": [14, 17, 16, 19, 17, 17, 15], "H": [17, 17, 17, 31, 17, 17, 17], "I": [14, 4, 4, 4, 4, 4, 14],
    "J": [7, 2, 2, 2, 18, 18, 12], "K": [17, 18, 20, 24, 20, 18, 17], "L": [16, 16, 16, 16, 16, 16, 31],
    "M": [17, 27, 21, 17, 17, 17, 17], "N": [17, 25, 21, 19, 17, 17, 17], "O": [14, 17, 17, 17, 17, 17, 14],
    "P": [30, 17, 17, 30, 16, 16, 16], "Q": [14, 17, 17, 17, 21, 18, 13], "R": [30, 17, 17, 30, 20, 18, 17],
    "S": [15, 16, 16, 14, 1, 1, 30], "T": [31, 4, 4, 4, 4, 4, 4], "U": [17, 17, 17, 17, 17, 17, 14],
    "V": [17, 17, 17, 17, 17, 10, 4], "W": [17, 17, 17, 21, 21, 27, 17], "X": [17, 17, 10, 4, 10, 17, 17],
    "Y": [17, 17, 10, 4, 4, 4, 4], "Z": [31, 1, 2, 4, 8, 16, 31],
    "a": [0, 0, 14, 1, 15, 17, 15], "b": [16, 16, 30, 17, 17, 17, 30], "c": [0, 0, 14, 16, 16, 17, 14],
    "d": [1, 1, 15, 17, 17, 17, 15], "e": [0, 0, 14, 17, 31, 16, 14], "f": [6, 8, 28, 8, 8, 8, 8],
    "g": [0, 15, 17, 17, 15, 1, 14], "h": [16, 16, 30, 17, 17, 17, 17], "i": [4, 0, 12, 4, 4, 4, 14],
    "j": [2, 0, 6, 2, 2, 18, 12], "k": [16, 16, 18, 20, 24, 20, 18], "l": [12, 4, 4, 4, 4, 4, 14],
    "m": [0, 0, 26, 21, 21, 17, 17], "n": [0, 0, 30, 17, 17, 17, 17], "o": [0, 0, 14, 17, 17, 17, 14],
    "p": [0, 0, 30, 17, 30, 16, 16], "q": [0, 0, 15, 17, 15, 1, 1], "r": [0, 0, 22, 24, 16, 16, 16],
    "s": [0, 0, 14, 16, 14, 1, 30], "t": [8, 8, 28, 8, 8, 9, 6], "u": [0, 0, 17, 17, 17, 17, 14],
    "v": [0, 0, 17, 17, 17, 10, 4], "w": [0, 0, 17, 17, 21, 21, 10], "x": [0, 0, 17, 10, 4, 10, 17],
    "y": [0, 0, 17, 17, 15, 1, 14], "z": [0, 0, 31, 2, 4, 8, 31],
    "é": [2, 4, 14, 17, 31, 16, 14], "è": [8, 4, 14, 17, 31, 16, 14], "ê": [4, 10, 14, 17, 31, 16, 14],
    "à": [2, 4, 14, 1, 15, 17, 15], "ù": [2, 4, 17, 17, 17, 17, 14], "û": [4, 10, 17, 17, 17, 17, 14],
    "ô": [4, 10, 14, 17, 17, 17, 14], "î": [4, 10, 12, 4, 4, 4, 14], "ç": [14, 17, 16, 16, 17, 14, 6]
  };
  // Police compacte 4x5 (reprise telle quelle)
  var FP = {
    " ": [0, 0, 0, 0, 0], "-": [0, 0, 7, 0, 0], ".": [0, 0, 0, 0, 2], ":": [0, 2, 0, 2, 0],
    "0": [6, 9, 9, 9, 6], "1": [2, 6, 2, 2, 7], "2": [6, 1, 2, 4, 7], "3": [7, 1, 3, 1, 7],
    "4": [5, 5, 7, 1, 1], "5": [7, 4, 6, 1, 6], "6": [3, 4, 6, 5, 2], "7": [7, 1, 2, 4, 4],
    "8": [6, 9, 6, 9, 6], "9": [6, 9, 7, 1, 6],
    "A": [6, 9, 15, 9, 9], "B": [14, 9, 14, 9, 14], "C": [7, 8, 8, 8, 7], "D": [14, 9, 9, 9, 14],
    "E": [15, 8, 14, 8, 15], "F": [15, 8, 14, 8, 8], "G": [7, 8, 11, 9, 7], "H": [9, 9, 15, 9, 9],
    "I": [14, 4, 4, 4, 14], "J": [3, 1, 1, 9, 6], "K": [9, 10, 12, 10, 9], "L": [8, 8, 8, 8, 15],
    "M": [9, 15, 15, 9, 9], "N": [9, 13, 11, 9, 9], "O": [6, 9, 9, 9, 6], "P": [14, 9, 14, 8, 8],
    "Q": [6, 9, 9, 11, 7], "R": [14, 9, 14, 10, 9], "S": [7, 8, 6, 1, 14], "T": [31, 4, 4, 4, 4],
    "U": [9, 9, 9, 9, 6], "V": [9, 9, 9, 6, 6], "W": [9, 9, 15, 15, 6], "X": [9, 6, 6, 6, 9],
    "Y": [9, 6, 6, 4, 4], "Z": [15, 1, 6, 8, 15]
  };
  var FONT_ROWS = { f57: 7, pmv: 5 };
  function fontData(fn) { return fn === "f57" ? F57 : FP; }
  function charW(fn, ch) { return fn === "pmv" ? (ch === " " ? 3 : 4) : (ch === " " ? 3 : 5); }
  function textWidth(txt, fn, sc) {
    var w = 0;
    for (var i = 0; i < txt.length; i++) { w += charW(fn, txt[i]) * sc; if (i < txt.length - 1) w += 1; }
    return w;
  }
  function textHeight(fn, sc) { return FONT_ROWS[fn] * sc; }

  // ================================================================ etat
  var zoom = 8, col = "#ffffff", tool = "pencil", grid = true, guides = false;
  var thick = 1, esz = 1, shapeMode = "outline";
  var drawing = false, lx = -1, ly = -1;
  var hist = [], maxH = 40, imgD;
  var shapeStart = null, shapeEnd = null;
  var selState = "idle", selStart = null, selEnd = null, selClip = null, selX = 0, selY = 0;
  var selDragOff = { x: 0, y: 0 }, selCopied = false;
  var tscale = 1, tfont = "pmv", tpos = { x: 2, y: 2 }, tOverCanvas = false;
  var cursorPos = null;
  var clip = null, clipW = 0, clipH = 0, pasting = false, pastePos = { x: 0, y: 0 };
  var mode = "dessin";
  var ajusteUneFois = false, modifie = false;
  var ecouteurs = [];

  var mc = $("ped-mc"), ctx = mc.getContext("2d");
  var go = $("ped-go"), gctx = go.getContext("2d");
  var oc = $("ped-oc"), octx = oc.getContext("2d");
  var tampon = document.createElement("canvas");            // 96 x 64, source unique de l'affichage
  tampon.width = W; tampon.height = H;
  var tctx = tampon.getContext("2d");

  function resizeCanvases() { [mc, go, oc].forEach(function (c) { c.width = W * zoom; c.height = H * zoom; }); }
  function initBuf() { imgD = new ImageData(W, H); imgD.data.fill(0); for (var i = 3; i < W * H * 4; i += 4) imgD.data[i] = 255; }
  function h2rgb(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
  function rgb2h(r, g, b) { return "#" + [r, g, b].map(function (v) { return v.toString(16).padStart(2, "0"); }).join(""); }
  function spx(x, y, h) {
    if (x < 0 || x >= W || y < 0 || y >= H) return;
    var c = h2rgb(h), i = (y * W + x) * 4;
    imgD.data[i] = c[0]; imgD.data[i + 1] = c[1]; imgD.data[i + 2] = c[2]; imgD.data[i + 3] = 255;
  }
  function gpx(x, y) {
    if (x < 0 || x >= W || y < 0 || y >= H) return "#000000";
    var i = (y * W + x) * 4;
    return rgb2h(imgD.data[i], imgD.data[i + 1], imgD.data[i + 2]);
  }
  function bpx(x, y, h) {
    var sz = (h === "#000000" && tool === "eraser") ? esz : thick;
    var hf = Math.floor(sz / 2);
    for (var dy = -hf; dy < sz - hf; dy++) for (var dx = -hf; dx < sz - hf; dx++) spx(x + dx, y + dy, h);
  }

  // ================================================================ rendu
  var changeTimer = null;
  function signalerChangement() {
    modifie = true;
    clearTimeout(changeTimer);
    changeTimer = setTimeout(function () { ecouteurs.forEach(function (cb) { try { cb(); } catch (e) {} }); }, 120);
  }

  function render(sansChangement) {
    tctx.putImageData(imgD, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, mc.width, mc.height);
    ctx.drawImage(tampon, 0, 0, W * zoom, H * zoom);
    renderGrid(); renderOverlay(); updateStatus(); renderSim();
    if (!sansChangement) signalerChangement();
  }

  function renderSim() {
    var sc = $("ped-sim-canvas");
    if (!sc) return;
    var st = sc.getContext("2d");
    st.fillStyle = "#000"; st.fillRect(0, 0, 128, 66);
    var s = Math.min(128 / W, 66 / H);
    var offX = Math.floor((128 - W * s) / 2), offY = Math.floor((66 - H * s) / 2);
    for (var y = 0; y < H; y++) for (var x = 0; x < W; x++) {
      var i = (y * W + x) * 4;
      st.fillStyle = "rgb(" + Math.round(imgD.data[i] * 0.92) + "," + Math.round(imgD.data[i + 1] * 0.92) + "," + Math.round(imgD.data[i + 2] * 0.92) + ")";
      st.fillRect(offX + x * s, offY + y * s, Math.ceil(s), Math.ceil(s));
    }
    if (s >= 1.2) {
      st.fillStyle = "rgba(0,0,0,0.18)";
      for (var yy = 0; yy < 66; yy += Math.ceil(s)) st.fillRect(0, yy, 128, 1);
      for (var xx = 0; xx < 128; xx += Math.ceil(s)) st.fillRect(xx, 0, 1, 66);
    }
  }

  function renderGrid() {
    gctx.clearRect(0, 0, go.width, go.height);
    if (grid && zoom >= 4) {
      gctx.fillStyle = "rgba(71,85,105,0.55)";
      for (var x = 0; x <= W; x++) gctx.fillRect(x * zoom - 0.5, 0, 1, H * zoom);
      for (var y = 0; y <= H; y++) gctx.fillRect(0, y * zoom - 0.5, W * zoom, 1);
    }
    if (guides) {
      var cxp = (W / 2) * zoom, cyp = (H / 2) * zoom;
      gctx.strokeStyle = "rgba(0,0,0,0.55)"; gctx.lineWidth = 3;
      gctx.beginPath(); gctx.moveTo(cxp, 0); gctx.lineTo(cxp, H * zoom); gctx.moveTo(0, cyp); gctx.lineTo(W * zoom, cyp); gctx.stroke();
      gctx.strokeStyle = "rgba(0,220,255,0.9)"; gctx.lineWidth = 1;
      gctx.beginPath(); gctx.moveTo(cxp, 0); gctx.lineTo(cxp, H * zoom); gctx.moveTo(0, cyp); gctx.lineTo(W * zoom, cyp); gctx.stroke();
    }
  }

  // ================================================================ overlay
  function croix(cx, cy, arm) {
    octx.strokeStyle = "rgba(0,0,0,0.5)"; octx.lineWidth = 3;
    octx.beginPath(); octx.moveTo(cx - arm, cy); octx.lineTo(cx + arm, cy); octx.moveTo(cx, cy - arm); octx.lineTo(cx, cy + arm); octx.stroke();
    octx.strokeStyle = "rgba(255,255,255,0.9)"; octx.lineWidth = 1;
    octx.beginPath(); octx.moveTo(cx - arm, cy); octx.lineTo(cx + arm, cy); octx.moveTo(cx, cy - arm); octx.lineTo(cx, cy + arm); octx.stroke();
  }

  function renderOverlay() {
    octx.clearRect(0, 0, oc.width, oc.height);
    if (tool === "text" && tOverCanvas) renderTextOverlay();
    if (pasting && clip) renderPasteOverlay();
    if (tool === "sel") renderSelOverlay();
    if (tool === "line" || tool === "rect" || tool === "circle" || tool === "triangle") renderShapePreview();
    if ((tool === "circle" || tool === "triangle") && tOverCanvas && !shapeStart && cursorPos)
      croix(cursorPos.x * zoom + zoom / 2, cursorPos.y * zoom + zoom / 2, 6);
    if (tool === "eraser" && tOverCanvas && cursorPos) {
      var hf = Math.floor(esz / 2), px = (cursorPos.x - hf) * zoom, py = (cursorPos.y - hf) * zoom, side = esz * zoom;
      octx.strokeStyle = "rgba(0,0,0,0.6)"; octx.lineWidth = 3; octx.strokeRect(px, py, side, side);
      octx.strokeStyle = "rgba(255,255,255,0.9)"; octx.lineWidth = 1; octx.strokeRect(px + 0.5, py + 0.5, side - 1, side - 1);
    }
  }

  function drawText(txt, x0, y0, color, fn, sc, target) {
    var f = fontData(fn), nrows = FONT_ROWS[fn], cx = x0;
    for (var i = 0; i < txt.length; i++) {
      var ch = txt[i], rows = f[ch] || f[ch.toUpperCase()] || f[" "], w = charW(fn, ch);
      for (var row = 0; row < nrows; row++) {
        var bits = rows[row] || 0;
        for (var c = 0; c < w; c++) {
          if ((bits >> (w - 1 - c)) & 1) {
            for (var sy = 0; sy < sc; sy++) for (var sx = 0; sx < sc; sx++) {
              var px = cx + c * sc + sx, py = y0 + row * sc + sy;
              if (target === "buf") spx(px, py, color);
              else { target.fillStyle = color; target.fillRect(px * zoom, py * zoom, zoom, zoom); }
            }
          }
        }
      }
      cx += charW(fn, ch) * sc + 1;
    }
  }

  function renderTextOverlay() {
    var txt = $("ped-texte").value;
    if (!txt) return;
    var tw = textWidth(txt, tfont, tscale), th = textHeight(tfont, tscale);
    octx.fillStyle = "rgba(37,99,235,0.14)"; octx.fillRect(tpos.x * zoom, tpos.y * zoom, tw * zoom, th * zoom);
    octx.strokeStyle = "rgba(96,165,250,0.8)"; octx.lineWidth = 1; octx.setLineDash([3, 3]);
    octx.strokeRect(tpos.x * zoom + 0.5, tpos.y * zoom + 0.5, tw * zoom - 1, th * zoom - 1); octx.setLineDash([]);
    drawText(txt, tpos.x, tpos.y, col, tfont, tscale, octx);
  }

  function renderShapePreview() {
    if (!shapeStart || !shapeEnd) return;
    var x0 = shapeStart[0], y0 = shapeStart[1], x1 = shapeEnd[0], y1 = shapeEnd[1];
    if (tool === "line") drawLineOv(x0, y0, x1, y1);
    else if (tool === "rect") drawRectOv(x0, y0, x1, y1);
    else if (tool === "circle") { drawCircleOv(x0, y0, x1, y1); croix(x0 * zoom + zoom / 2, y0 * zoom + zoom / 2, 6); }
    else if (tool === "triangle") {
      octx.fillStyle = col;
      trianglePixels(x0, y0, x1, y1).forEach(function (p) { octx.fillRect(p[0] * zoom, p[1] * zoom, zoom, zoom); });
      croix(x0 * zoom + zoom / 2, y0 * zoom + zoom / 2, 5);
    }
  }

  function drawLineOv(x0, y0, x1, y1) {
    var hf = Math.floor(thick / 2);
    octx.fillStyle = col;
    bresenham(x0, y0, x1, y1).forEach(function (p) {
      for (var dy = -hf; dy < thick - hf; dy++) for (var dx = -hf; dx < thick - hf; dx++)
        octx.fillRect((p[0] + dx) * zoom, (p[1] + dy) * zoom, zoom, zoom);
    });
  }
  function drawRectOv(x0, y0, x1, y1) {
    var mx = Math.min(x0, x1), xx = Math.max(x0, x1), my = Math.min(y0, y1), xy = Math.max(y0, y1);
    if (shapeMode === "fill") { octx.fillStyle = col; octx.fillRect(mx * zoom, my * zoom, (xx - mx + 1) * zoom, (xy - my + 1) * zoom); return; }
    for (var t = 0; t < thick; t++) {
      var l = mx + t, r = xx - t, tp = my + t, b = xy - t;
      if (l > r || tp > b) break;
      octx.strokeStyle = col; octx.lineWidth = zoom;
      octx.strokeRect((l + 0.5) * zoom, (tp + 0.5) * zoom, (r - l) * zoom, (b - tp) * zoom);
    }
  }
  function drawCircleOv(x0, y0, x1, y1) {
    var r = Math.round(Math.sqrt(Math.pow(x1 - x0, 2) + Math.pow(y1 - y0, 2)));
    octx.fillStyle = col;
    if (shapeMode === "fill") {
      for (var y = y0 - r; y <= y0 + r; y++) for (var x = x0 - r; x <= x0 + r; x++)
        if ((x - x0) * (x - x0) + (y - y0) * (y - y0) <= r * r) octx.fillRect(x * zoom, y * zoom, zoom, zoom);
    } else {
      for (var yy = y0 - r - thick; yy <= y0 + r + thick; yy++) for (var xx = x0 - r - thick; xx <= x0 + r + thick; xx++) {
        var d = Math.sqrt((xx - x0) * (xx - x0) + (yy - y0) * (yy - y0));
        if (d >= r - thick / 2 && d <= r + thick / 2) octx.fillRect(xx * zoom, yy * zoom, zoom, zoom);
      }
    }
  }

  function renderSelOverlay() {
    if (selState === "idle") return;
    if (selState === "drawing") {
      if (!selStart || !selEnd) return;
      var x0 = Math.min(selStart.x, selEnd.x), y0 = Math.min(selStart.y, selEnd.y);
      var w = (Math.abs(selEnd.x - selStart.x) + 1) * zoom, h = (Math.abs(selEnd.y - selStart.y) + 1) * zoom;
      octx.strokeStyle = "rgba(255,255,255,0.9)"; octx.lineWidth = 1; octx.setLineDash([4, 4]);
      octx.strokeRect(x0 * zoom + 0.5, y0 * zoom + 0.5, w - 1, h - 1); octx.setLineDash([]);
      return;
    }
    if ((selState === "active" || selState === "dragging") && selClip) {
      var sw = Math.abs(selEnd.x - selStart.x) + 1, sh = Math.abs(selEnd.y - selStart.y) + 1;
      octx.globalAlpha = 0.9;
      for (var y = 0; y < sh; y++) for (var x = 0; x < sw; x++) {
        var c = selClip[y * sw + x];
        octx.fillStyle = "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")";
        octx.fillRect((selX + x) * zoom, (selY + y) * zoom, zoom, zoom);
      }
      octx.globalAlpha = 1;
      octx.strokeStyle = "rgba(96,165,250,0.95)"; octx.lineWidth = 1; octx.setLineDash([4, 4]);
      octx.strokeRect(selX * zoom + 0.5, selY * zoom + 0.5, sw * zoom - 1, sh * zoom - 1); octx.setLineDash([]);
    }
  }

  function renderPasteOverlay() {
    octx.globalAlpha = 0.8;
    for (var y = 0; y < clipH; y++) for (var x = 0; x < clipW; x++) {
      var c = clip[y * clipW + x];
      octx.fillStyle = "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")";
      octx.fillRect((pastePos.x + x) * zoom, (pastePos.y + y) * zoom, zoom, zoom);
    }
    octx.globalAlpha = 1;
    octx.strokeStyle = "rgba(96,165,250,0.95)"; octx.lineWidth = 1; octx.setLineDash([3, 3]);
    octx.strokeRect(pastePos.x * zoom + 0.5, pastePos.y * zoom + 0.5, clipW * zoom - 1, clipH * zoom - 1); octx.setLineDash([]);
  }

  // ================================================================ dessin dans imgD
  function bresenham(x0, y0, x1, y1) {
    var pts = [], dx = Math.abs(x1 - x0), dy = Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1, err = dx - dy;
    while (true) {
      pts.push([x0, y0]);
      if (x0 === x1 && y0 === y1) break;
      var e2 = 2 * err;
      if (e2 > -dy) { err -= dy; x0 += sx; }
      if (e2 < dx) { err += dx; y0 += sy; }
    }
    return pts;
  }
  function commitLine(x0, y0, x1, y1) {
    var c = tool === "eraser" ? "#000000" : col;
    bresenham(x0, y0, x1, y1).forEach(function (p) { bpx(p[0], p[1], c); });
  }
  function commitRect(x0, y0, x1, y1) {
    var mx = Math.min(x0, x1), xx = Math.max(x0, x1), my = Math.min(y0, y1), xy = Math.max(y0, y1), x, y;
    if (shapeMode === "fill") { for (y = my; y <= xy; y++) for (x = mx; x <= xx; x++) spx(x, y, col); return; }
    for (var t = 0; t < thick; t++) {
      var l = mx + t, r = xx - t, tp = my + t, b = xy - t;
      if (l > r || tp > b) break;
      for (x = l; x <= r; x++) { spx(x, tp, col); spx(x, b, col); }
      for (y = tp; y <= b; y++) { spx(l, y, col); spx(r, y, col); }
    }
  }
  function commitCircle(x0, y0, x1, y1) {
    var r = Math.round(Math.sqrt(Math.pow(x1 - x0, 2) + Math.pow(y1 - y0, 2))), x, y;
    if (shapeMode === "fill") {
      for (y = y0 - r; y <= y0 + r; y++) for (x = x0 - r; x <= x0 + r; x++)
        if ((x - x0) * (x - x0) + (y - y0) * (y - y0) <= r * r) spx(x, y, col);
      return;
    }
    for (y = y0 - r - thick; y <= y0 + r + thick; y++) for (x = x0 - r - thick; x <= x0 + r + thick; x++) {
      var d = Math.sqrt((x - x0) * (x - x0) + (y - y0) * (y - y0));
      if (d >= r - thick / 2 && d <= r + thick / 2) spx(x, y, col);
    }
  }
  function flood(sx, sy, tg, fc) {
    if (tg === fc) return;
    var stack = [[sx, sy]], vis = new Set();
    while (stack.length) {
      var p = stack.pop(), x = p[0], y = p[1];
      if (x < 0 || x >= W || y < 0 || y >= H) continue;
      var k = y * W + x;
      if (vis.has(k) || gpx(x, y) !== tg) continue;
      vis.add(k); spx(x, y, fc);
      stack.push([x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]);
    }
  }
  function commitTriangle(x0, y0, x1, y1) {
    trianglePixels(x0, y0, x1, y1).forEach(function (p) { spx(p[0], p[1], col); });
  }
  // Pointe (tipX, tipY), base centree sur (baseX, baseY) : le vecteur donne hauteur ET direction.
  function trianglePixels(tipX, tipY, baseX, baseY) {
    var dx = baseX - tipX, dy = baseY - tipY, len = Math.sqrt(dx * dx + dy * dy);
    if (len < 1) return [];
    var ux = dx / len, uy = dy / len, px = -uy, py = ux;
    var halfBase = len * 0.6, steps = Math.ceil(len), result = [], seen = new Set();
    function add(x, y) {
      var xi = Math.round(x), yi = Math.round(y);
      if (xi < 0 || xi >= W || yi < 0 || yi >= H) return;
      var k = yi * W + xi;
      if (seen.has(k)) return;
      seen.add(k); result.push([xi, yi]);
    }
    for (var i = 0; i <= steps; i++) {
      var t = i / steps, ax = tipX + ux * len * t, ay = tipY + uy * len * t, hw = halfBase * t, w, t2;
      if (shapeMode === "fill") {
        var ws = Math.ceil(hw);
        for (w = -ws; w <= ws; w++) add(ax + px * w, ay + py * w);
      } else if (i === 0) {
        for (t2 = -thick + 1; t2 <= thick - 1; t2++) add(ax + px * t2, ay + py * t2);
      } else if (i === steps) {
        var wb = Math.ceil(hw);
        for (w = -wb; w <= wb; w++) add(ax + px * w, ay + py * w);
      } else {
        for (t2 = 0; t2 < thick; t2++) {
          add(ax + px * (hw - t2), ay + py * (hw - t2));
          add(ax + px * (-hw + t2), ay + py * (-hw + t2));
        }
      }
    }
    return result;
  }

  // ================================================================ selection
  function selClear() {
    selState = "idle"; selStart = null; selEnd = null; selClip = null; selCopied = false;
    renderOverlay();
  }
  function selCapture() {
    var x0 = Math.min(selStart.x, selEnd.x), y0 = Math.min(selStart.y, selEnd.y);
    var x1 = Math.max(selStart.x, selEnd.x), y1 = Math.max(selStart.y, selEnd.y), x, y;
    selX = x0; selY = y0; selClip = [];
    for (y = y0; y <= y1; y++) for (x = x0; x <= x1; x++) {
      var i = (y * W + x) * 4;
      selClip.push([imgD.data[i], imgD.data[i + 1], imgD.data[i + 2]]);
    }
    if (!selCopied) {
      for (y = y0; y <= y1; y++) for (x = x0; x <= x1; x++) spx(x, y, "#000000");
      render();
    }
  }
  function selCommit() {
    if (!selClip || selState === "idle") return;
    saveH();
    var sw = Math.abs(selEnd.x - selStart.x) + 1, sh = Math.abs(selEnd.y - selStart.y) + 1;
    for (var y = 0; y < sh; y++) for (var x = 0; x < sw; x++) {
      var c = selClip[y * sw + x];
      spx(selX + x, selY + y, rgb2h(c[0], c[1], c[2]));
    }
    selClear(); render();
  }
  function selInBounds(x, y) {
    if (!selClip || selState !== "active") return false;
    var sw = Math.abs(selEnd.x - selStart.x) + 1, sh = Math.abs(selEnd.y - selStart.y) + 1;
    return x >= selX && x < selX + sw && y >= selY && y < selY + sh;
  }

  // ================================================================ historique
  function saveH() { hist.push(new Uint8ClampedArray(imgD.data)); if (hist.length > maxH) hist.shift(); }
  function undo() {
    if (pasting) { pasting = false; clip = null; renderOverlay(); return; }
    if (!hist.length) return;
    imgD.data.set(hist.pop()); render();
  }

  // ================================================================ evenements toile (pointeur)
  function canvasCoords(e) {
    var r = mc.getBoundingClientRect();
    return [Math.floor((e.clientX - r.left) / zoom), Math.floor((e.clientY - r.top) / zoom)];
  }

  function deposerCollage() {
    saveH();
    for (var py = 0; py < clipH; py++) for (var px = 0; px < clipW; px++) {
      var c = clip[py * clipW + px];
      spx(pastePos.x + px, pastePos.y + py, rgb2h(c[0], c[1], c[2]));
    }
    pasting = false; clip = null; render();
  }

  mc.addEventListener("pointerdown", function (e) {
    if (e.button === 2) return;                         // clic droit : menu contextuel
    e.preventDefault();
    try { mc.setPointerCapture(e.pointerId); } catch (err) {}
    var p = canvasCoords(e), x = p[0], y = p[1];
    tOverCanvas = true; cursorPos = { x: x, y: y };
    if (pasting) {
      if (e.pointerType !== "mouse") { pastePos = { x: x - Math.floor(clipW / 2), y: y - Math.floor(clipH / 2) }; }
      deposerCollage(); return;
    }
    if (tool === "text") {
      var txt = $("ped-texte").value;
      tpos = { x: x, y: y };
      if (txt) { saveH(); drawText(txt, x, y, col, tfont, tscale, "buf"); render(); }
      else toast("info", "Saisissez d'abord le texte a poser.");
      return;
    }
    if (tool === "sel") {
      if (selState === "active") {
        if (selInBounds(x, y)) { selState = "dragging"; selDragOff = { x: x - selX, y: y - selY }; selCopied = e.altKey; return; }
        selCommit();
      }
      saveH(); selState = "drawing"; selStart = { x: x, y: y }; selEnd = { x: x, y: y }; selClip = null; return;
    }
    if (tool === "fill") { saveH(); flood(x, y, gpx(x, y), col); render(); return; }
    if (tool === "eye") { col = gpx(x, y); updateColUI(); setTool("pencil"); return; }
    if (tool === "line" || tool === "rect" || tool === "circle" || tool === "triangle") {
      saveH(); shapeStart = [x, y]; shapeEnd = [x, y]; return;
    }
    saveH(); drawing = true; lx = x; ly = y; bpx(x, y, tool === "eraser" ? "#000000" : col); render();
  });

  mc.addEventListener("pointermove", function (e) {
    var p = canvasCoords(e), x = p[0], y = p[1];
    $("ped-coords").textContent = (x >= 0 && x < W && y >= 0 && y < H) ? "x " + x + "  y " + y : "--";
    tOverCanvas = true; cursorPos = { x: x, y: y };
    if (pasting) { pastePos = { x: x, y: y }; renderOverlay(); return; }
    if (tool === "text") { tpos = { x: x, y: y }; renderOverlay(); return; }
    if (tool === "sel") {
      if (selState === "drawing") { selEnd = { x: Math.max(0, Math.min(W - 1, x)), y: Math.max(0, Math.min(H - 1, y)) }; renderOverlay(); }
      else if (selState === "dragging") { selX = x - selDragOff.x; selY = y - selDragOff.y; renderOverlay(); }
      else if (selState === "active") mc.style.cursor = selInBounds(x, y) ? "move" : "crosshair";
      return;
    }
    if (tool === "line" || tool === "rect" || tool === "circle" || tool === "triangle") {
      if (shapeStart) shapeEnd = [x, y];
      renderOverlay(); return;
    }
    if (tool === "eraser") renderOverlay();
    if (!drawing) return;
    if (lx >= 0) commitLine(lx, ly, x, y); else bpx(x, y, tool === "eraser" ? "#000000" : col);
    lx = x; ly = y; render();
  });

  mc.addEventListener("pointerup", function (e) {
    var p = canvasCoords(e), x = p[0], y = p[1];
    if (tool === "line" || tool === "rect" || tool === "circle" || tool === "triangle") {
      if (shapeStart) {
        if (tool === "line") commitLine(shapeStart[0], shapeStart[1], x, y);
        else if (tool === "rect") commitRect(shapeStart[0], shapeStart[1], x, y);
        else if (tool === "circle") commitCircle(shapeStart[0], shapeStart[1], x, y);
        else commitTriangle(shapeStart[0], shapeStart[1], x, y);
        shapeStart = null; shapeEnd = null; render();
      }
      return;
    }
    if (tool === "sel") {
      if (selState === "drawing") {
        selEnd = { x: Math.max(0, Math.min(W - 1, x)), y: Math.max(0, Math.min(H - 1, y)) };
        if (selStart.x === selEnd.x && selStart.y === selEnd.y) { hist.pop(); selClear(); }
        else { selState = "active"; selCopied = e.altKey; selCapture(); renderOverlay(); }
      } else if (selState === "dragging") { selState = "active"; renderOverlay(); }
      return;
    }
    drawing = false; lx = -1; ly = -1;
  });

  mc.addEventListener("pointerleave", function () {
    if (drawing || shapeStart || selState === "drawing" || selState === "dragging") return;
    tOverCanvas = false; cursorPos = null;
    $("ped-coords").textContent = "--";
    renderOverlay();
  });

  $("ped-scene").addEventListener("wheel", function (e) {
    if (mode !== "dessin") return;
    e.preventDefault();
    if (e.deltaY < 0) zIn(); else zOut();
  }, { passive: false });

  // ================================================================ palette, outils, proprietes
  function buildPal() {
    var el = $("ped-palette");
    PAL.forEach(function (c) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "pmv-ed-teinte"; b.style.background = c.h;
      b.dataset.h = c.h; b.title = c.l; b.setAttribute("aria-label", c.l);
      b.addEventListener("click", function () { col = c.h; updateColUI(); updateTxtPrev(); renderOverlay(); });
      el.appendChild(b);
    });
  }
  function updateColUI() {
    document.querySelectorAll("#ped-palette .pmv-ed-teinte").forEach(function (s) { s.classList.toggle("active", s.dataset.h === col); });
    $("ped-couleur-pastille").style.background = col;
    $("ped-couleur-hex").textContent = col.toUpperCase();
  }

  var CURSEURS = { pencil: "crosshair", eraser: "cell", fill: "copy", line: "crosshair", rect: "crosshair",
                   circle: "crosshair", triangle: "crosshair", eye: "crosshair", sel: "crosshair", text: "text" };

  function setTool(n) {
    if (tool === "sel" && n !== "sel" && selState === "active") selCommit();
    else if (tool === "sel" && n !== "sel") selClear();
    tool = n;
    document.querySelectorAll("#ped-outils [data-outil]").forEach(function (b) { b.classList.toggle("active", b.dataset.outil === n); });
    mc.style.cursor = CURSEURS[n] || "crosshair";
    $("ped-prop-epaisseur").hidden = ["pencil", "line", "rect", "circle", "triangle"].indexOf(n) < 0;
    $("ped-prop-gomme").hidden = n !== "eraser";
    $("ped-prop-forme").hidden = ["rect", "circle", "triangle"].indexOf(n) < 0;
    $("ped-prop-triangle").hidden = n !== "triangle";
    $("ped-prop-texte").hidden = n !== "text";
    if (n === "text") { updateTxtPrev(); setTimeout(function () { $("ped-texte").focus(); }, 0); }
    renderOverlay();
  }

  document.querySelectorAll("#ped-outils [data-outil]").forEach(function (b) {
    b.addEventListener("click", function () { setTool(b.dataset.outil); });
  });
  $("ped-annuler").addEventListener("click", undo);
  $("ped-epaisseur").addEventListener("input", function () {
    thick = +this.value; $("ped-epaisseur-val").textContent = thick + " px"; renderOverlay();
  });
  function segment(idGroupe, attr, cb) {
    var groupe = $(idGroupe);
    groupe.addEventListener("click", function (e) {
      var b = e.target.closest("[data-" + attr + "]");
      if (!b) return;
      groupe.querySelectorAll("[data-" + attr + "]").forEach(function (x) { x.classList.toggle("active", x === b); });
      cb(b.dataset[attr]);
    });
  }
  segment("ped-gomme", "taille", function (v) { esz = +v; renderOverlay(); });
  segment("ped-forme", "forme", function (v) { shapeMode = v; renderOverlay(); });

  // ================================================================ texte
  function updateTxtPrev() {
    var txt = $("ped-texte").value || "";
    var pc = $("ped-texte-apercu");
    var tw = Math.max(1, textWidth(txt, tfont, tscale)), th = textHeight(tfont, tscale);
    pc.width = Math.min(tw, 190); pc.height = Math.max(th, 1);
    var p = pc.getContext("2d");
    p.fillStyle = "#000"; p.fillRect(0, 0, pc.width, pc.height);
    var f = fontData(tfont), nrows = FONT_ROWS[tfont], cx = 0;
    for (var i = 0; i < txt.length; i++) {
      var ch = txt[i], rows = f[ch] || f[ch.toUpperCase()] || f[" "], cw = charW(tfont, ch);
      for (var row = 0; row < nrows; row++) {
        var bits = rows[row] || 0;
        for (var c2 = 0; c2 < cw; c2++) {
          if ((bits >> (cw - 1 - c2)) & 1) { p.fillStyle = col; p.fillRect(cx + c2 * tscale, row * tscale, tscale, tscale); }
        }
      }
      cx += cw * tscale + 1;
    }
    $("ped-texte-apercu").style.width = (pc.width * 2) + "px";
    $("ped-texte-apercu").style.height = (pc.height * 2) + "px";
  }
  $("ped-texte").addEventListener("input", function () { updateTxtPrev(); renderOverlay(); });
  $("ped-police").addEventListener("change", function () { tfont = this.value; updateTxtPrev(); renderOverlay(); });
  $("ped-echelle").addEventListener("input", function () {
    tscale = +this.value; $("ped-echelle-val").textContent = "x" + tscale; updateTxtPrev(); renderOverlay();
  });

  // ================================================================ zoom, grille
  function zIn() { if (zoom < 16) { zoom++; applyZ(); } }
  function zOut() { if (zoom > 2) { zoom--; applyZ(); } }
  function applyZ() { $("ped-zoom-val").textContent = "x" + zoom; resizeCanvases(); render(true); }
  function ajuster() {
    var sc = $("ped-scene");
    if (!sc.clientWidth) return false;
    zoom = Math.max(2, Math.min(16, Math.min(Math.floor((sc.clientWidth - 32) / W), Math.floor((sc.clientHeight - 32) / H))));
    applyZ();
    return true;
  }
  $("ped-zoom-moins").addEventListener("click", zOut);
  $("ped-zoom-plus").addEventListener("click", zIn);
  $("ped-zoom-ajuster").addEventListener("click", ajuster);
  $("ped-grille").addEventListener("change", function () { grid = this.checked; renderGrid(); });
  $("ped-reperes").addEventListener("change", function () { guides = this.checked; renderGrid(); });

  // ================================================================ statut
  function updateStatus() {
    var c = {};
    for (var i = 0; i < W * H * 4; i += 4) {
      var h = rgb2h(imgD.data[i], imgD.data[i + 1], imgD.data[i + 2]);
      c[h] = (c[h] || 0) + 1;
    }
    var el = $("ped-comptes");
    el.textContent = "";
    Object.keys(c).sort(function (a, b) { return c[b] - c[a]; }).slice(0, 4).forEach(function (h) {
      var s = document.createElement("span");
      var pastille = document.createElement("i");
      pastille.style.background = h;
      s.appendChild(pastille);
      s.appendChild(document.createTextNode(h.toUpperCase() + " " + c[h]));
      el.appendChild(s);
    });
  }

  // ================================================================ import / export
  function lireBMP(buf) {
    // -> {px:[[r,g,b]...], w, h} ou leve une Error au message affichable
    if (!buf || buf.byteLength < 54) throw new Error("Fichier BMP trop court ou vide.");
    var v = new DataView(buf);
    if (v.getUint8(0) !== 0x42 || v.getUint8(1) !== 0x4D) throw new Error("Ce n'est pas un fichier BMP.");
    var pxoff = v.getUint32(10, true), w = v.getInt32(18, true), h = v.getInt32(22, true);
    var bpp = v.getUint16(28, true), compress = v.getUint32(30, true), ah = Math.abs(h), bu = h > 0;
    if (w < 1 || ah < 1 || w > 4096 || ah > 4096) throw new Error("Dimensions BMP invalides (" + w + " x " + ah + ").");
    if (compress === 1 || compress === 2) throw new Error("BMP compresse (RLE) non pris en charge.");
    var pal = [], row, x, ir, st;
    if (bpp <= 8) {
      var dibSize = v.getUint32(14, true), n = Math.min(1 << bpp, (pxoff - 14 - dibSize) >> 2);
      for (var i = 0; i < n; i++) { var o = 14 + dibSize + i * 4; pal.push([v.getUint8(o + 2), v.getUint8(o + 1), v.getUint8(o)]); }
    }
    var px = new Array(w * ah);
    if (bpp === 32) {
      for (row = 0; row < ah; row++) { ir = bu ? ah - 1 - row : row; for (x = 0; x < w; x++) { var f = pxoff + (row * w + x) * 4; px[ir * w + x] = [v.getUint8(f + 2), v.getUint8(f + 1), v.getUint8(f)]; } }
    } else if (bpp === 24) {
      st = Math.floor((w * 3 + 3) / 4) * 4;
      for (row = 0; row < ah; row++) { ir = bu ? ah - 1 - row : row; for (x = 0; x < w; x++) { var f2 = pxoff + row * st + x * 3; px[ir * w + x] = [v.getUint8(f2 + 2), v.getUint8(f2 + 1), v.getUint8(f2)]; } }
    } else if (bpp === 16 && compress === 3) {
      // 5-6-5 : le format de Sigma Editor et de nos vecteurs
      st = Math.floor((w * 2 + 3) / 4) * 4;
      for (row = 0; row < ah; row++) {
        ir = bu ? ah - 1 - row : row;
        for (x = 0; x < w; x++) {
          var val = v.getUint16(pxoff + row * st + x * 2, true);
          var r5 = (val >> 11) & 31, g6 = (val >> 5) & 63, b5 = val & 31;
          px[ir * w + x] = [(r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2)];
        }
      }
    } else if (bpp === 8) {
      st = Math.floor((w + 3) / 4) * 4;
      for (row = 0; row < ah; row++) { ir = bu ? ah - 1 - row : row; for (x = 0; x < w; x++) px[ir * w + x] = pal[v.getUint8(pxoff + row * st + x)] || [0, 0, 0]; }
    } else if (bpp === 4) {
      st = Math.floor((w * 4 + 31) / 32) * 4;
      for (row = 0; row < ah; row++) { ir = bu ? ah - 1 - row : row; for (x = 0; x < w; x++) { var bv = v.getUint8(pxoff + row * st + Math.floor(x / 2)); px[ir * w + x] = pal[x % 2 === 0 ? (bv >> 4) & 0xF : bv & 0xF] || [0, 0, 0]; } }
    } else if (bpp === 1) {
      st = Math.floor((w + 31) / 32) * 4;
      for (row = 0; row < ah; row++) { ir = bu ? ah - 1 - row : row; for (x = 0; x < w; x++) { var bv1 = v.getUint8(pxoff + row * st + Math.floor(x / 8)); px[ir * w + x] = pal[(bv1 >> (7 - x % 8)) & 1] || [0, 0, 0]; } }
    } else {
      throw new Error("Profondeur BMP non prise en charge : " + bpp + " bits (acceptes : 1, 4, 8, 16 en 5-6-5, 24, 32).");
    }
    return { px: px, w: w, h: ah };
  }

  function lireRaster(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file), img = new Image();
      img.onload = function () {
        var t = document.createElement("canvas");
        t.width = img.naturalWidth; t.height = img.naturalHeight;
        var c = t.getContext("2d");
        c.drawImage(img, 0, 0);
        var id = c.getImageData(0, 0, t.width, t.height), px = [];
        for (var i = 0; i < t.width * t.height; i++) px.push([id.data[i * 4], id.data[i * 4 + 1], id.data[i * 4 + 2]]);
        URL.revokeObjectURL(url);
        resolve({ px: px, w: t.width, h: t.height });
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("Impossible de lire cette image.")); };
      img.src = url;
    });
  }

  function lireFichier(file) {
    if (/\.bmp$/i.test(file.name)) {
      return file.arrayBuffer().then(lireBMP);
    }
    return lireRaster(file);
  }

  function redimensionner(px, srcW, srcH, dw, dh) {
    var s = document.createElement("canvas"); s.width = srcW; s.height = srcH;
    var sc = s.getContext("2d"), sid = sc.createImageData(srcW, srcH);
    for (var i = 0; i < srcW * srcH; i++) { sid.data[i * 4] = px[i][0]; sid.data[i * 4 + 1] = px[i][1]; sid.data[i * 4 + 2] = px[i][2]; sid.data[i * 4 + 3] = 255; }
    sc.putImageData(sid, 0, 0);
    var d = document.createElement("canvas"); d.width = dw; d.height = dh;
    var dc = d.getContext("2d");
    dc.imageSmoothingEnabled = true; dc.imageSmoothingQuality = "high";
    dc.drawImage(s, 0, 0, dw, dh);
    var did = dc.getImageData(0, 0, dw, dh), out = [];
    for (var j = 0; j < dw * dh; j++) out.push([did.data[j * 4], did.data[j * 4 + 1], did.data[j * 4 + 2]]);
    return out;
  }

  // > 96x64 : ajuste proportionnellement ; = 96x64 : au pixel ; < 96x64 : centre tel quel.
  function placePixelsOnCanvas(px, srcW, srcH) {
    saveH();
    var fw = srcW, fh = srcH, fpx = px;
    if (srcW > W || srcH > H) {
      var scale = Math.min(W / srcW, H / srcH);
      fw = Math.round(srcW * scale); fh = Math.round(srcH * scale);
      fpx = redimensionner(px, srcW, srcH, fw, fh);
    }
    var offX = Math.floor((W - fw) / 2), offY = Math.floor((H - fh) / 2);
    for (var y = 0; y < fh; y++) for (var x = 0; x < fw; x++) {
      var dx = offX + x, dy = offY + y;
      if (dx < 0 || dx >= W || dy < 0 || dy >= H) continue;
      var c = fpx[y * fw + x], i = (dy * W + dx) * 4;
      imgD.data[i] = c[0]; imgD.data[i + 1] = c[1]; imgD.data[i + 2] = c[2]; imgD.data[i + 3] = 255;
    }
    render();
  }

  $("ped-importer").addEventListener("click", function () { $("ped-fichier-import").click(); });
  $("ped-fichier-import").addEventListener("change", function () {
    var f = this.files && this.files[0];
    this.value = "";
    if (!f) return;
    lireFichier(f).then(function (r) {
      placePixelsOnCanvas(r.px, r.w, r.h);
      toast("success", f.name + " importe (" + r.w + " x " + r.h + "). Ctrl+Z pour annuler.");
    }).catch(function (e) { toast("error", e.message || "Fichier illisible."); });
  });

  function telecharger(blob, nom) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = nom;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }
  function nomFichier(ext) {
    var n = ($("ped-nom").value || "message-pmv").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    return (n || "message-pmv") + "." + ext;
  }
  $("ped-exporter-bmp").addEventListener("click", function () {
    // BMP 32 bits, comme l'original (ouvrable partout). Le 5-6-5 du panneau est fabrique par le serveur.
    var buf = new ArrayBuffer(54 + W * H * 4), v = new DataView(buf);
    v.setUint8(0, 0x42); v.setUint8(1, 0x4D); v.setUint32(2, 54 + W * H * 4, true); v.setUint32(6, 0, true); v.setUint32(10, 54, true);
    v.setUint32(14, 40, true); v.setInt32(18, W, true); v.setInt32(22, H, true); v.setUint16(26, 1, true); v.setUint16(28, 32, true);
    v.setUint32(30, 0, true); v.setUint32(34, W * H * 4, true);
    var off = 54;
    for (var y = H - 1; y >= 0; y--) for (var x = 0; x < W; x++) {
      var i = (y * W + x) * 4;
      v.setUint8(off++, imgD.data[i + 2]); v.setUint8(off++, imgD.data[i + 1]); v.setUint8(off++, imgD.data[i]); v.setUint8(off++, 0);
    }
    telecharger(new Blob([buf], { type: "image/bmp" }), nomFichier("bmp"));
  });
  $("ped-exporter-png").addEventListener("click", function () {
    tctx.putImageData(imgD, 0, 0);
    tampon.toBlob(function (b) { telecharger(b, nomFichier("png")); }, "image/png");
  });
  $("ped-vider").addEventListener("click", function () {
    window.showConfirmToast("Vider tout le dessin ? (annulable avec Ctrl+Z)", { type: "warning", okLabel: "Vider", cancelLabel: "Annuler" })
      .then(function (oui) { if (oui) { saveH(); initBuf(); render(); } });
  });

  // ================================================================ modales (ouverture / fermeture)
  function ouvrir(id) { $(id).hidden = false; }
  function fermer(id) { $(id).hidden = true; }
  ["ped-modal-inserer", "ped-modal-aide"].forEach(function (id) {
    $(id).addEventListener("click", function (e) {
      if (e.target === this || e.target.closest("[data-fermer]")) fermer(id);
    });
  });
  function modaleOuverte() {
    return !$("ped-modal-inserer").hidden || !$("ped-modal-aide").hidden ||
      !!document.querySelector(".crud-modal:not([hidden])");
  }

  // ================================================================ insertion d'image
  var ins = { px: null, w: 0, h: 0, taille: 32 };
  function insResultat() {
    var sc = Math.min(ins.taille / ins.w, ins.taille / ins.h);
    var fw = Math.max(1, Math.round(ins.w * sc)), fh = Math.max(1, Math.round(ins.h * sc));
    $("ped-ins-resultat").textContent = "Sera redimensionnee en " + fw + " x " + fh + " px";
    return [fw, fh];
  }
  function insTaille(t) {
    ins.taille = Math.max(4, Math.min(96, t));
    $("ped-ins-taille").value = ins.taille;
    document.querySelectorAll("#ped-ins-tailles [data-taille]").forEach(function (b) { b.classList.toggle("active", +b.dataset.taille === ins.taille); });
    insResultat();
  }
  function ouvrirInsertion(r) {
    ins.px = r.px; ins.w = r.w; ins.h = r.h;
    var pv = $("ped-ins-apercu"), sc = Math.min(300 / r.w, 120 / r.h, 4);
    var pw = Math.max(1, Math.round(r.w * sc)), ph = Math.max(1, Math.round(r.h * sc));
    pv.width = pw; pv.height = ph;
    var pt = pv.getContext("2d");
    pt.fillStyle = "#000"; pt.fillRect(0, 0, pw, ph);
    for (var y = 0; y < r.h; y++) for (var x = 0; x < r.w; x++) {
      var c = r.px[y * r.w + x];
      pt.fillStyle = "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")";
      pt.fillRect(Math.round(x * sc), Math.round(y * sc), Math.max(1, Math.ceil(sc)), Math.max(1, Math.ceil(sc)));
    }
    $("ped-ins-info").textContent = "Source : " + r.w + " x " + r.h + " px";
    insTaille(ins.taille);
    ouvrir("ped-modal-inserer");
  }
  segment("ped-ins-tailles", "taille", function (v) { insTaille(+v); });
  $("ped-ins-taille").addEventListener("input", function () {
    ins.taille = Math.max(4, Math.min(96, parseInt(this.value, 10) || 32));
    document.querySelectorAll("#ped-ins-tailles [data-taille]").forEach(function (b) { b.classList.remove("active"); });
    insResultat();
  });
  $("ped-ins-ok").addEventListener("click", function () {
    fermer("ped-modal-inserer");
    var d = insResultat(), fw = d[0], fh = d[1];
    var px = (fw === ins.w && fh === ins.h) ? ins.px : redimensionner(ins.px, ins.w, ins.h, fw, fh);
    commencerCollage(px, fw, fh);
  });
  $("ped-inserer").addEventListener("click", function () { $("ped-fichier-inserer").click(); });
  $("ped-fichier-inserer").addEventListener("change", function () {
    var f = this.files && this.files[0];
    this.value = "";
    if (!f) return;
    lireFichier(f).then(ouvrirInsertion).catch(function (e) { toast("error", e.message || "Fichier illisible."); });
  });

  function commencerCollage(px, w, h) {
    clip = px; clipW = w; clipH = h; pasting = true;
    pastePos = { x: Math.floor((W - w) / 2), y: Math.floor((H - h) / 2) };
    if (tool === "sel") selClear();
    basculerMode("dessin");
    renderOverlay();
    toast("info", "Deplacez l'image puis cliquez pour la deposer (Echap pour abandonner).");
  }

  // ================================================================ menu contextuel copier / couper / coller
  var menu = $("ped-menu"), ctxClip = null, ctxW = 0, ctxH = 0;
  function aSelection() { return selState === "active" && selClip && selClip.length > 0; }
  function doCopy() {
    if (!aSelection()) return;
    ctxClip = selClip.slice(); ctxW = Math.abs(selEnd.x - selStart.x) + 1; ctxH = Math.abs(selEnd.y - selStart.y) + 1;
  }
  function doCut() {
    if (!aSelection()) return;
    doCopy();
    if (selCopied) {                 // selection prise avec Alt : la zone d'origine n'a pas ete videe
      saveH();
      var x0 = Math.min(selStart.x, selEnd.x), y0 = Math.min(selStart.y, selEnd.y);
      for (var dy = 0; dy < ctxH; dy++) for (var dx = 0; dx < ctxW; dx++) spx(x0 + dx, y0 + dy, "#000000");
    }
    selClear(); render();            // sinon la zone est deja videe par selCapture
  }
  function doPaste() {
    if (!ctxClip) return;
    if (aSelection()) selCommit();
    commencerCollage(ctxClip.slice(), ctxW, ctxH);
  }
  mc.addEventListener("contextmenu", function (e) {
    e.preventDefault();
    $("ped-menu-couper").disabled = !aSelection();
    $("ped-menu-copier").disabled = !aSelection();
    $("ped-menu-coller").disabled = !ctxClip;
    menu.hidden = false;
    var x = Math.min(e.clientX, window.innerWidth - menu.offsetWidth - 8);
    var y = Math.min(e.clientY, window.innerHeight - menu.offsetHeight - 8);
    menu.style.left = x + "px"; menu.style.top = y + "px";
  });
  document.addEventListener("click", function (e) { if (!menu.contains(e.target)) menu.hidden = true; });
  document.addEventListener("scroll", function () { menu.hidden = true; }, true);
  $("ped-menu-copier").addEventListener("click", function () {
    menu.hidden = true;
    if (!aSelection()) return;
    doCopy(); toast("info", "Zone copiee : clic droit puis Coller, ou Ctrl+V.");
  });
  $("ped-menu-couper").addEventListener("click", function () { doCut(); menu.hidden = true; });
  $("ped-menu-coller").addEventListener("click", function () { menu.hidden = true; doPaste(); });

  // ================================================================ clavier (onglet Messages seulement)
  function editeurVisible() {
    var v = document.querySelector('.pmv-view[data-vue="messages"]');
    return v && v.classList.contains("active") && mode === "dessin";
  }
  var RACCOURCIS = { p: "pencil", e: "eraser", f: "fill", l: "line", r: "rect", c: "circle", g: "triangle", i: "eye", s: "sel", t: "text" };
  document.addEventListener("keydown", function (e) {
    if (!editeurVisible() || modaleOuverte()) return;
    var cible = e.target.tagName;
    if (cible === "INPUT" || cible === "SELECT" || cible === "TEXTAREA") {
      if (e.key === "Escape" && e.target.id === "ped-texte") { e.target.blur(); }
      return;
    }
    var k = e.key.toLowerCase();
    if (e.ctrlKey || e.metaKey) {
      if (k === "z") { e.preventDefault(); undo(); }
      else if (k === "c" && aSelection()) { e.preventDefault(); doCopy(); }
      else if (k === "x" && aSelection()) { e.preventDefault(); doCut(); }
      else if (k === "v" && ctxClip) { e.preventDefault(); doPaste(); }
      return;
    }
    if (e.altKey) return;
    if (RACCOURCIS[k]) { setTool(RACCOURCIS[k]); return; }
    if (e.key === "Enter" && tool === "sel" && selState === "active") selCommit();
    if ((e.key === "Delete" || e.key === "Backspace") && tool === "sel" && selState === "active") { e.preventDefault(); selClear(); render(); }
    if (e.key === "Escape") {
      if (pasting) { pasting = false; clip = null; renderOverlay(); return; }
      if (tool === "sel" && selState === "active") { hist.length && imgD.data.set(hist.pop()); selClear(); render(); return; }
      if (tool === "text") setTool("pencil");
    }
  });

  // ================================================================ volet Copier (source -> dessin)
  var cp = (function () {
    var RATIO = W / H, HANDLE = 8, MIN_FRAME = 24;
    var elSrc = $("ped-source"), elSc = $("ped-src"), elOv = $("ped-src-ov"), elVide = $("ped-source-vide");
    var img = null, iw = 0, ih = 0, mz = 1, etat = "idle";
    var fx = 0, fy = 0, fw = W, fh = H, action = null, off = null;
    var zx0 = 0, zy0 = 0, zx1 = 0, zy1 = 0, zsel = false, zseling = false;

    function msg(t) { $("ped-cp-etat").textContent = t; }
    function boutons() {
      $("ped-cp-choix").hidden = etat !== "choose";
      $("ped-cp-valider").hidden = !(etat === "orig" || etat === "custom");
      $("ped-cp-zonebar").hidden = etat !== "zone";
      if (etat === "choose") {
        var petit = iw <= W && ih <= H;
        $("ped-cp-centre").hidden = !petit;
        $("ped-cp-original").hidden = petit;
        $("ped-cp-perso").hidden = petit;
        $("ped-cp-fit").hidden = petit;
      }
    }
    function afficher() {
      elVide.hidden = !!img || mode !== "copier";
      elSrc.hidden = !img || mode !== "copier";
      if (img && mode === "copier") dessinerSource();
    }
    function dessinerSource() {
      var scene = $("ped-scene");
      var aw = Math.max(60, scene.clientWidth - 28), ah = Math.max(60, scene.clientHeight - 28);
      mz = Math.max(0.05, Math.min(12, Math.min(aw / iw, ah / ih)));
      var dw = Math.max(1, Math.round(iw * mz)), dh = Math.max(1, Math.round(ih * mz));
      elSc.width = dw; elSc.height = dh; elOv.width = dw; elOv.height = dh;
      var t = elSc.getContext("2d");
      t.imageSmoothingEnabled = mz < 1; t.imageSmoothingQuality = "high";
      t.clearRect(0, 0, dw, dh); t.drawImage(img, 0, 0, dw, dh);
      cadre();
    }
    function cadre() {
      var ot = elOv.getContext("2d");
      ot.clearRect(0, 0, elOv.width, elOv.height);
      if (etat === "orig" || etat === "custom") {
        var rx = fx * mz, ry = fy * mz, rw = fw * mz, rh = fh * mz;
        ot.fillStyle = "rgba(0,0,0,.5)"; ot.fillRect(0, 0, elOv.width, elOv.height);
        ot.clearRect(rx, ry, rw, rh);
        ot.strokeStyle = "#f59e0b"; ot.lineWidth = 1.5; ot.setLineDash([4, 3]);
        ot.strokeRect(rx + 0.5, ry + 0.5, rw - 1, rh - 1); ot.setLineDash([]);
        if (etat === "custom") {
          ot.fillStyle = "#f59e0b";
          [[rx, ry], [rx + rw, ry], [rx, ry + rh], [rx + rw, ry + rh]].forEach(function (p) { ot.fillRect(p[0] - HANDLE / 2, p[1] - HANDLE / 2, HANDLE, HANDLE); });
        }
      } else if (etat === "zone" && zsel) {
        var x0 = Math.min(zx0, zx1) * mz, y0 = Math.min(zy0, zy1) * mz;
        var w = (Math.abs(zx1 - zx0) + 1) * mz, h = (Math.abs(zy1 - zy0) + 1) * mz;
        ot.fillStyle = "rgba(37,99,235,.22)"; ot.fillRect(x0, y0, w, h);
        ot.strokeStyle = "rgba(96,165,250,.95)"; ot.lineWidth = 1; ot.setLineDash([3, 3]);
        ot.strokeRect(x0 + 0.5, y0 + 0.5, w - 1, h - 1); ot.setLineDash([]);
      }
    }
    function commit(dessinFn, texte) {
      var t = document.createElement("canvas"); t.width = W; t.height = H;
      var c = t.getContext("2d");
      c.fillStyle = "#000"; c.fillRect(0, 0, W, H);
      dessinFn(c);
      var id = c.getImageData(0, 0, W, H);
      saveH();
      for (var i = 0; i < W * H * 4; i += 4) { imgD.data[i] = id.data[i]; imgD.data[i + 1] = id.data[i + 1]; imgD.data[i + 2] = id.data[i + 2]; imgD.data[i + 3] = 255; }
      reset(); basculerMode("dessin"); render();
      toast("success", texte + " Ctrl+Z pour annuler.");
    }
    function reset() {
      img = null; etat = "idle"; action = null; zsel = false; zseling = false;
      msg("Aucune source chargee"); boutons(); afficher();
    }
    function hit(mx, my) {
      var rx = fx * mz, ry = fy * mz, rw = fw * mz, rh = fh * mz;
      function pres(a, b) { return Math.abs(a - b) < HANDLE; }
      if (etat === "custom") {
        if (pres(mx, rx) && pres(my, ry)) return "tl";
        if (pres(mx, rx + rw) && pres(my, ry)) return "tr";
        if (pres(mx, rx) && pres(my, ry + rh)) return "bl";
        if (pres(mx, rx + rw) && pres(my, ry + rh)) return "br";
      }
      if (mx > rx && mx < rx + rw && my > ry && my < ry + rh) return "move";
      return null;
    }
    function curseur(a) { return a === "move" ? "grab" : (a === "tl" || a === "br") ? "nwse-resize" : (a === "tr" || a === "bl") ? "nesw-resize" : "crosshair"; }

    $("ped-cp-charger").addEventListener("click", function () { $("ped-cp-fichier").click(); });
    $("ped-cp-fichier").addEventListener("change", function () {
      var file = this.files && this.files[0];
      this.value = "";
      if (!file) return;
      var url = URL.createObjectURL(file), im = new Image();
      im.onload = function () {
        URL.revokeObjectURL(url);
        img = im; iw = im.naturalWidth; ih = im.naturalHeight;
        action = null; fx = 0; fy = 0; fw = W; fh = H; zsel = false; zseling = false;
        etat = "choose";
        msg("Source " + iw + " x " + ih + " : choisir une action");
        boutons(); afficher();
      };
      im.onerror = function () { URL.revokeObjectURL(url); toast("error", "Impossible de lire cette image."); };
      im.src = url;
    });
    $("ped-cp-centre").addEventListener("click", function () {
      var dx = Math.floor((W - iw) / 2), dy = Math.floor((H - ih) / 2);
      commit(function (t) { t.imageSmoothingEnabled = false; t.drawImage(img, dx, dy); }, "Image placee au centre.");
    });
    $("ped-cp-original").addEventListener("click", function () {
      etat = "orig"; fw = W; fh = H; fx = Math.round((iw - W) / 2); fy = Math.round((ih - H) / 2);
      msg("Glisser le cadre 96 x 64, puis Valider le cadrage"); boutons(); cadre();
    });
    $("ped-cp-perso").addEventListener("click", function () {
      etat = "custom"; fw = Math.min(iw, ih * RATIO); fh = fw / RATIO; fx = Math.round((iw - fw) / 2); fy = Math.round((ih - fh) / 2);
      msg("Coins = redimensionner (3:2), interieur = deplacer, puis Valider le cadrage"); boutons(); cadre();
    });
    $("ped-cp-fit").addEventListener("click", function () {
      var sc = Math.min(W / iw, H / ih), dw = Math.round(iw * sc), dh = Math.round(ih * sc), dx = Math.floor((W - dw) / 2), dy = Math.floor((H - dh) / 2);
      commit(function (t) { t.imageSmoothingEnabled = true; t.imageSmoothingQuality = "high"; t.drawImage(img, 0, 0, iw, ih, dx, dy, dw, dh); }, "Image ajustee en entier.");
    });
    $("ped-cp-zone").addEventListener("click", function () {
      etat = "zone"; zsel = false; zseling = false;
      msg("Tracer un rectangle sur la source, puis Copier la selection"); boutons(); cadre();
    });
    $("ped-cp-abandon").addEventListener("click", reset);
    $("ped-cp-ok").addEventListener("click", function () {
      if (etat === "orig") commit(function (t) { t.imageSmoothingEnabled = false; t.drawImage(img, fx, fy, W, H, 0, 0, W, H); }, "Zone 96 x 64 reprise a la resolution d'origine.");
      else if (etat === "custom") {
        var a = fx, b = fy, c = fw, d = fh;
        commit(function (t) { t.imageSmoothingEnabled = true; t.imageSmoothingQuality = "high"; t.drawImage(img, a, b, c, d, 0, 0, W, H); },
          "Zone " + Math.round(c) + " x " + Math.round(d) + " ramenee en 96 x 64.");
      }
    });
    function retour() { etat = "choose"; action = null; zsel = false; msg("Source " + iw + " x " + ih + " : choisir une action"); boutons(); cadre(); }
    $("ped-cp-retour").addEventListener("click", retour);
    $("ped-cp-retour-zone").addEventListener("click", retour);
    $("ped-cp-copier-zone").addEventListener("click", function () {
      if (!zsel) { msg("Tracez d'abord un rectangle sur la source."); return; }
      var x0 = Math.max(0, Math.min(zx0, zx1)), y0 = Math.max(0, Math.min(zy0, zy1));
      var x1 = Math.min(iw - 1, Math.max(zx0, zx1)), y1 = Math.min(ih - 1, Math.max(zy0, zy1));
      var w = x1 - x0 + 1, h = y1 - y0 + 1;
      if (w < 1 || h < 1) return;
      var t = document.createElement("canvas"); t.width = w; t.height = h;
      var c = t.getContext("2d"); c.imageSmoothingEnabled = false;
      c.drawImage(img, x0, y0, w, h, 0, 0, w, h);
      var id = c.getImageData(0, 0, w, h), arr = new Array(w * h);
      for (var i = 0; i < w * h; i++) arr[i] = [id.data[i * 4], id.data[i * 4 + 1], id.data[i * 4 + 2]];
      reset();
      commencerCollage(arr, w, h);
    });

    elSc.addEventListener("pointerdown", function (e) {
      var r = elSc.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      try { elSc.setPointerCapture(e.pointerId); } catch (err) {}
      if (etat === "zone") {
        zx0 = Math.max(0, Math.min(iw - 1, Math.floor(mx / mz))); zy0 = Math.max(0, Math.min(ih - 1, Math.floor(my / mz)));
        zx1 = zx0; zy1 = zy0; zseling = true; zsel = true; cadre(); return;
      }
      if (etat !== "orig" && etat !== "custom") return;
      action = hit(mx, my);
      if (action) off = { mx: mx, my: my, fx: fx, fy: fy, fw: fw, fh: fh };
    });
    elSc.addEventListener("pointermove", function (e) {
      var r = elSc.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      if (etat === "zone") {
        if (!zseling) return;
        zx1 = Math.max(0, Math.min(iw - 1, Math.floor(mx / mz))); zy1 = Math.max(0, Math.min(ih - 1, Math.floor(my / mz)));
        cadre(); return;
      }
      if (etat !== "orig" && etat !== "custom") return;
      if (!action) { elSc.style.cursor = curseur(hit(mx, my)); return; }
      var dxs = (mx - off.mx) / mz, dys = (my - off.my) / mz;
      if (action === "move") {
        fx = Math.max(0, Math.min(iw - fw, off.fx + dxs));
        fy = Math.max(0, Math.min(ih - fh, off.fy + dys));
      } else if (etat === "custom") {
        var nw = (action === "br" || action === "tr") ? off.fw + dxs : off.fw - dxs;
        nw = Math.max(MIN_FRAME, nw);
        var nh = nw / RATIO, nx = off.fx, ny = off.fy;
        if (action === "bl" || action === "tl") nx = off.fx + off.fw - nw;
        if (action === "tr" || action === "tl") ny = off.fy + off.fh - nh;
        if (nx >= 0 && ny >= 0 && nx + nw <= iw + 0.01 && ny + nh <= ih + 0.01) { fx = nx; fy = ny; fw = nw; fh = nh; }
      }
      cadre();
    });
    function finGlisse() { action = null; zseling = false; }
    elSc.addEventListener("pointerup", finGlisse);
    elSc.addEventListener("pointercancel", finGlisse);
    window.addEventListener("resize", function () { if (img && mode === "copier") dessinerSource(); });

    return { afficher: afficher };
  })();

  // ================================================================ modes Dessin / Copier
  function basculerMode(m) {
    mode = m;
    document.querySelectorAll("#ped-modes [data-mode]").forEach(function (b) { b.classList.toggle("active", b.dataset.mode === m); });
    $("ped-panneau-dessin").hidden = m !== "dessin";
    $("ped-panneau-copier").hidden = m !== "copier";
    $("ped-toile").hidden = m !== "dessin";
    cp.afficher();
  }
  $("ped-modes").addEventListener("click", function (e) {
    var b = e.target.closest("[data-mode]");
    if (b) basculerMode(b.dataset.mode);
  });

  // ================================================================ aide
  $("ped-aide-btn").addEventListener("click", function () { ouvrir("ped-modal-aide"); });
  $("ped-aide-onglets").addEventListener("click", function (e) {
    var b = e.target.closest("[data-aide]");
    if (!b) return;
    document.querySelectorAll("#ped-aide-onglets [data-aide]").forEach(function (x) { x.classList.toggle("active", x === b); });
    document.querySelectorAll("[data-aide-section]").forEach(function (s) { s.hidden = s.getAttribute("data-aide-section") !== b.dataset.aide; });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (!$("ped-modal-aide").hidden) fermer("ped-modal-aide");
    if (!$("ped-modal-inserer").hidden) fermer("ped-modal-inserer");
  });

  // ================================================================ simulateur (remorque stylisee)
  $("ped-simulateur").innerHTML =
    '<svg viewBox="0 0 194 220" width="194" height="220" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
    '<defs><linearGradient id="ped-ciel" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#7ab8e8"/><stop offset="100%" stop-color="#b8d8f0"/></linearGradient>' +
    '<linearGradient id="ped-herbe" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#5a9e3a"/><stop offset="100%" stop-color="#3d7a25"/></linearGradient></defs>' +
    '<rect x="0" y="0" width="194" height="220" fill="url(#ped-ciel)"/><rect x="0" y="175" width="194" height="45" fill="url(#ped-herbe)"/>' +
    '<rect x="0" y="190" width="194" height="4" fill="#555"/><rect x="0" y="194" width="194" height="26" fill="#6b6b6b"/>' +
    '<rect x="15" y="203" width="16" height="3" fill="#e8e000" opacity="0.7"/><rect x="50" y="203" width="16" height="3" fill="#e8e000" opacity="0.7"/>' +
    '<rect x="85" y="203" width="16" height="3" fill="#e8e000" opacity="0.7"/><rect x="120" y="203" width="16" height="3" fill="#e8e000" opacity="0.7"/>' +
    '<rect x="155" y="203" width="16" height="3" fill="#e8e000" opacity="0.7"/>' +
    '<ellipse cx="65" cy="190" rx="10" ry="8" fill="#222"/><ellipse cx="65" cy="190" rx="6" ry="5" fill="#444"/><ellipse cx="65" cy="190" rx="2" ry="2" fill="#888"/>' +
    '<ellipse cx="129" cy="190" rx="10" ry="8" fill="#222"/><ellipse cx="129" cy="190" rx="6" ry="5" fill="#444"/><ellipse cx="129" cy="190" rx="2" ry="2" fill="#888"/>' +
    '<rect x="76" y="179" width="4" height="13" fill="#666"/><rect x="73" y="191" width="10" height="3" rx="1" fill="#555"/>' +
    '<rect x="114" y="179" width="4" height="13" fill="#666"/><rect x="111" y="191" width="10" height="3" rx="1" fill="#555"/>' +
    '<rect x="22" y="142" width="150" height="46" rx="3" fill="#f0f0f0" stroke="#ccc" stroke-width="1"/>' +
    '<rect x="22" y="167" width="150" height="21" fill="#e86820"/><rect x="22" y="165" width="150" height="4" fill="#e86820"/>' +
    '<polygon points="97,144 57,164 65,164 97,148 129,164 137,164" fill="#cc1111"/><polygon points="97,152 53,173 62,173 97,156 132,173 141,173" fill="#cc1111"/>' +
    '<rect x="22" y="168" width="14" height="10" rx="2" fill="#cc0000" stroke="#900" stroke-width="0.5"/><rect x="158" y="168" width="14" height="10" rx="2" fill="#cc0000" stroke="#900" stroke-width="0.5"/>' +
    '<rect x="80" y="179" width="34" height="9" rx="1" fill="#fff" stroke="#aaa" stroke-width="0.5"/>' +
    '<rect x="91" y="90" width="12" height="55" fill="#555"/><rect x="93" y="88" width="8" height="57" fill="#666"/>' +
    '<rect x="30" y="88" width="134" height="7" rx="2" fill="#444"/><rect x="28" y="14" width="138" height="76" rx="3" fill="#2a2a2a" stroke="#444" stroke-width="1.5"/>' +
    '<rect x="31" y="17" width="132" height="70" rx="2" fill="#111"/>' +
    '<foreignObject x="33" y="19" width="128" height="66"><canvas id="ped-sim-canvas" xmlns="http://www.w3.org/1999/xhtml" width="128" height="66" style="display:block;image-rendering:pixelated;width:128px;height:66px"></canvas></foreignObject>' +
    '<rect x="33" y="19" width="128" height="16" fill="white" opacity="0.04"/>' +
    '<circle cx="33" cy="20" r="2" fill="#555"/><circle cx="161" cy="20" r="2" fill="#555"/><circle cx="33" cy="83" r="2" fill="#555"/><circle cx="161" cy="83" r="2" fill="#555"/>' +
    "</svg>";

  // ================================================================ API publique
  function pngDataURL() { tctx.putImageData(imgD, 0, 0); return tampon.toDataURL("image/png"); }

  window.PmvEditor = {
    /** A appeler quand l'onglet Messages devient visible (la scene a enfin une taille). */
    afficher: function () {
      if (!ajusteUneFois && ajuster()) ajusteUneFois = true;
      cp.afficher();
    },
    getPNGBase64: function () {
      if (tool === "sel" && selState === "active") selCommit();
      return pngDataURL().split(",")[1];
    },
    /** Charge une image 96 x 64 (base64 PNG) : dessin remplace, historique conserve. */
    chargerPNG: function (b64) {
      return new Promise(function (resolve, reject) {
        var im = new Image();
        im.onload = function () {
          if (im.naturalWidth !== W || im.naturalHeight !== H) { reject(new Error("image hors format")); return; }
          var t = document.createElement("canvas"); t.width = W; t.height = H;
          var c = t.getContext("2d"); c.drawImage(im, 0, 0);
          var id = c.getImageData(0, 0, W, H);
          selClear(); pasting = false; clip = null;
          saveH();
          imgD.data.set(id.data);
          for (var i = 3; i < W * H * 4; i += 4) imgD.data[i] = 255;
          render(true); modifie = false;
          resolve();
        };
        im.onerror = function () { reject(new Error("image illisible")); };
        im.src = "data:image/png;base64," + b64;
      });
    },
    estVide: function () {
      for (var i = 0; i < W * H * 4; i += 4) if (imgD.data[i] || imgD.data[i + 1] || imgD.data[i + 2]) return false;
      return true;
    },
    estModifie: function () { return modifie; },
    marquerPropre: function () { modifie = false; },
    nouveau: function () { selClear(); pasting = false; clip = null; saveH(); initBuf(); render(true); modifie = false; },
    onChange: function (cb) { ecouteurs.push(cb); }
  };

  // ================================================================ init
  buildPal(); initBuf(); resizeCanvases(); setTool("pencil"); updateColUI(); updateTxtPrev(); render(true);
  basculerMode("dessin");
  window.addEventListener("resize", function () { if (editeurVisible()) ajuster(); });
})();
