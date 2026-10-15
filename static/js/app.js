/* ChainLedger - small UI helpers (no libraries).
   1. Light / dark theme toggle (remembered in this browser)
   2. Copy-to-clipboard buttons next to hashes
   3. Hover a hash -> every identical hash on the page lights up,
      so you can SEE how block N's hash equals block N+1's prev_hash
   4. Pill tabs on the block page
   5. Footer signature pixel-dissolve (same credit animation as Aryan's other projects)
   6. "Marks by block" area chart with hover tooltip and semester filter chips
   7. Press "/" to jump to the search box
   0. Loading screen (runs first) */
(function () {
  "use strict";

  // 0. Loading screen ----------------------------------------------------------
  // Shown once per visit. The bar follows the real page load (HTML, fonts,
  // images) and the steps mirror what the server already did: recompute every
  // hash, check the anchor. A random hex string "hashes" until it settles on
  // the real chain tip.
  (function () {
    var root = document.documentElement;
    var box = document.getElementById("loader");
    if (!box || !root.classList.contains("booting")) { if (box) box.remove(); return; }

    var fill = box.querySelector(".loader-fill"), pct = box.querySelector(".loader-pct");
    var stepEl = box.querySelector(".loader-step"), hashEl = box.querySelector(".loader-hash");
    var steps = []; try { steps = JSON.parse(box.getAttribute("data-steps")); } catch (e) {}
    var ok = box.getAttribute("data-ok") === "1", tip = box.getAttribute("data-tip") || "";
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var MIN = reduce ? 300 : 1500;           // keep it on screen long enough to read
    var start = performance.now(), shown = 0, pageLoaded = document.readyState === "complete", fontsReady = false;

    addEventListener("load", function () { pageLoaded = true; });
    (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(function () { fontsReady = true; });

    var HEX = "0123456789abcdef";
    function scramble() { var out = ""; for (var i = 0; i < 64; i++) out += HEX[(Math.random() * 16) | 0]; return out; }

    function finish() {
      fill.style.width = "100%"; pct.textContent = "100%";
      if (steps.length) { stepEl.textContent = steps[steps.length - 1]; stepEl.classList.add(ok ? "ok" : "bad"); }
      if (tip) hashEl.textContent = tip;
      try { sessionStorage.setItem("chainledger-booted", "1"); } catch (e) {}
      setTimeout(function () {
        box.classList.add("is-done");
        setTimeout(function () { root.classList.remove("booting"); box.remove(); }, 460);
      }, reduce ? 150 : 520);
    }

    function frame(now) {
      var elapsed = now - start;
      var ready = pageLoaded && fontsReady && elapsed >= MIN;
      // ease toward 92% while loading, then to 100% once everything is in
      var target = ready ? 100 : Math.min(92, (elapsed / MIN) * 92);
      shown += (target - shown) * (reduce ? 1 : 0.14);
      if (ready && shown > 99.4) { finish(); return; }
      fill.style.width = shown.toFixed(1) + "%";
      pct.textContent = Math.floor(shown) + "%";
      if (steps.length > 1) stepEl.textContent = steps[Math.min(steps.length - 2, Math.floor(shown / (92 / (steps.length - 1))))];
      if (!reduce) hashEl.textContent = scramble();
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }());

  // 1. Theme toggle ------------------------------------------------------------
  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var root = document.documentElement;
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("chainledger-theme", next); } catch (e) { /* private mode */ }
      document.dispatchEvent(new CustomEvent("themechange"));
    });
  });

  // 2. Copy buttons -------------------------------------------------------------
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    // Fallback for plain-HTTP pages (e.g. http://<EC2-IP>)
    return new Promise(function (resolve) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (e) { /* ignore */ }
      document.body.removeChild(ta);
      resolve();
    });
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    e.preventDefault();
    copyText(btn.getAttribute("data-copy")).then(function () {
      btn.classList.add("done");
      setTimeout(function () { btn.classList.remove("done"); }, 1200);
    });
  });

  // 3. Hash hover highlight ------------------------------------------------------
  function highlight(hash, on) {
    document.querySelectorAll('.hash[data-hash="' + hash + '"]').forEach(function (el) {
      el.classList.toggle("hl", on);
    });
  }
  document.addEventListener("mouseover", function (e) {
    var el = e.target.closest(".hash[data-hash]");
    if (el) highlight(el.getAttribute("data-hash"), true);
  });
  document.addEventListener("mouseout", function (e) {
    var el = e.target.closest(".hash[data-hash]");
    if (el) highlight(el.getAttribute("data-hash"), false);
  });

  // 4. Tabs ---------------------------------------------------------------------
  document.querySelectorAll("[data-tabs]").forEach(function (group) {
    var tabs = group.querySelectorAll("[data-tab]");
    function show(name) {
      var found = false;
      tabs.forEach(function (t) {
        var on = t.getAttribute("data-tab") === name;
        found = found || on;
        t.classList.toggle("active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      if (!found) return false;
      document.querySelectorAll("[data-panel]").forEach(function (p) {
        p.hidden = p.getAttribute("data-panel") !== name;
      });
      return true;
    }
    tabs.forEach(function (t) {
      t.addEventListener("click", function () {
        var name = t.getAttribute("data-tab");
        show(name);
        history.replaceState(null, "", "#" + name);
      });
    });
    if (!show(location.hash.slice(1))) show(tabs[0].getAttribute("data-tab"));
  });

  // 5. Signature ----------------------------------------------------------------
  // The signature dissolves in out of a grid of blocks rather than fading, so
  // the typeface never changes mid-animation. Replays every time the footer
  // comes back into view.
  (function () {
    var sig = document.querySelector(".footer-signature");
    if (!sig) return;

    var pixels = sig.querySelector(".footer-signature-pixels");
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!pixels || reduce) return;

    var COLS = 22, ROWS = 5;
    pixels.style.gridTemplateColumns = "repeat(" + COLS + ", 1fr)";
    pixels.style.gridTemplateRows = "repeat(" + ROWS + ", 1fr)";

    var tiles = [];
    for (var n = 0; n < COLS * ROWS; n++) {
      var tile = document.createElement("i");
      pixels.appendChild(tile);
      tiles.push(tile);
    }

    function cover() {
      sig.classList.add("is-revealing");
      tiles.forEach(function (t) {
        t.style.transitionDelay = "0s";
        t.style.opacity = "1";
        t.style.transform = "scale(1)";
      });
    }

    function reveal() {
      // Each block clears after its own delay, so the word emerges in a
      // ragged scatter rather than a clean wipe.
      tiles.forEach(function (t) {
        t.style.transitionDelay = (Math.random() * 0.55).toFixed(3) + "s";
        t.style.opacity = "0";
        t.style.transform = "scale(0.4)";
      });
      setTimeout(function () { sig.classList.remove("is-revealing"); }, 120);
    }

    cover();

    if (!("IntersectionObserver" in window)) { reveal(); return; }

    new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) reveal(); else cover();
      });
    }, { threshold: 0.6 }).observe(sig);
  }());

  // 6. Area chart -----------------------------------------------------------------
  // Draws an SVG line + soft area of marks per block. One series, so no legend;
  // hovering anywhere snaps a crosshair to the nearest block and shows a tooltip.
  var SVG = "http://www.w3.org/2000/svg";
  function el(name, attrs, parent) {
    var n = document.createElementNS(SVG, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function smoothPath(pts) {
    if (pts.length < 2) return pts.length ? "M" + pts[0][0] + "," + pts[0][1] : "";
    var d = "M" + pts[0][0] + "," + pts[0][1];
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      var c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
      var c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += " C" + c1x.toFixed(1) + "," + c1y.toFixed(1) + " " + c2x.toFixed(1) + "," + c2y.toFixed(1) + " " + p2[0].toFixed(1) + "," + p2[1].toFixed(1);
    }
    return d;
  }
  document.querySelectorAll(".area-chart[data-series]").forEach(function (box, idx) {
    var all = [];
    try { all = JSON.parse(box.getAttribute("data-series")) || []; } catch (e) { return; }
    if (!all.length) return;
    var filter = "all";
    var card = box.closest(".card");

    function draw() {
      var data = all.filter(function (p) { return filter === "all" || String(p.sem) === filter; });
      box.querySelectorAll("svg, .chart-tip, .empty").forEach(function (n) { n.remove(); });
      if (!data.length) { var e = document.createElement("p"); e.className = "empty"; e.textContent = "No blocks in this semester."; box.appendChild(e); return; }
      var W = Math.max(280, box.clientWidth - 20), H = 260, L = 42, R = 12, T = 14, B = 26;
      var svg = el("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" }, box);
      var gid = "areaFill" + idx;
      var defs = el("defs", {}, svg), grad = el("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      el("stop", { offset: "0%", "stop-color": "#ff8a1f", "stop-opacity": ".32" }, grad);
      el("stop", { offset: "100%", "stop-color": "#ff8a1f", "stop-opacity": "0" }, grad);
      var x = function (i) { return data.length === 1 ? (L + W - R) / 2 : L + i * (W - L - R) / (data.length - 1); };
      var y = function (v) { return T + (100 - v) * (H - T - B) / 100; };
      [0, 25, 50, 75, 100].forEach(function (v) {
        el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), "class": "grid-line" }, svg);
        el("text", { x: L - 10, y: y(v) + 3.5, "text-anchor": "end", "class": "axis-label" }, svg).textContent = v;
      });
      var step = Math.ceil(data.length / 8);
      data.forEach(function (p, i) {
        if (i % step === 0 || i === data.length - 1)
          el("text", { x: x(i), y: H - 6, "text-anchor": "middle", "class": "axis-label" }, svg).textContent = "#" + p.id;
      });
      var pts = data.map(function (p, i) { return [x(i), y(p.y)]; });
      var line = smoothPath(pts);
      el("path", { d: line + " L" + pts[pts.length - 1][0] + "," + y(0) + " L" + pts[0][0] + "," + y(0) + " Z", fill: "url(#" + gid + ")" }, svg);
      el("path", { d: line, "class": "line" }, svg);
      var cross = el("line", { y1: T, y2: y(0), "class": "cross", visibility: "hidden" }, svg);
      var dot = el("circle", { r: 5, "class": "focus-dot", visibility: "hidden" }, svg);
      var tip = document.createElement("div"); tip.className = "chart-tip"; tip.hidden = true; box.appendChild(tip);
      var hit = el("rect", { x: L, y: 0, width: W - L - R, height: H, "class": "hit" }, svg);
      function show(evt) {
        var r = svg.getBoundingClientRect(), px = (evt.clientX - r.left) * W / r.width;
        var i = Math.max(0, Math.min(data.length - 1, Math.round((px - L) / ((W - L - R) / Math.max(1, data.length - 1)))));
        var p = data[i];
        cross.setAttribute("x1", pts[i][0]); cross.setAttribute("x2", pts[i][0]);
        dot.setAttribute("cx", pts[i][0]); dot.setAttribute("cy", pts[i][1]);
        cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
        tip.innerHTML = "#" + p.id + " · <b>" + p.y + "</b> marks<br>" + String(p.label).replace(/[<>&]/g, "") + " · sem " + p.sem;
        tip.style.left = (pts[i][0] * r.width / W + (svg.getBoundingClientRect().left - box.getBoundingClientRect().left)) + "px";
        tip.style.top = (pts[i][1] * r.height / H + (svg.getBoundingClientRect().top - box.getBoundingClientRect().top)) + "px";
        tip.hidden = false;
      }
      function hide() { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true; }
      hit.addEventListener("mousemove", show);
      hit.addEventListener("mouseleave", hide);
    }

    if (card) card.querySelectorAll(".chip[data-sem]").forEach(function (c) {
      c.addEventListener("click", function () {
        filter = c.getAttribute("data-sem");
        card.querySelectorAll(".chip[data-sem]").forEach(function (o) { o.classList.toggle("active", o === c); });
        draw();
      });
    });
    var t; addEventListener("resize", function () { clearTimeout(t); t = setTimeout(draw, 150); });
    draw();
  });

  // 7. "/" focuses search -------------------------------------------------------------
  document.addEventListener("keydown", function (e) {
    var a = document.activeElement, typing = a && /input|textarea|select/i.test(a.tagName);
    var q = document.getElementById("top-q");
    if (e.key === "/" && !typing && q) { e.preventDefault(); q.focus(); }
  });
})();
