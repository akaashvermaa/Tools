"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ── Shared helpers ──────────────────────────────────────────────────────────

function toast(msg, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 3500);
}

function setStatus(el, text, kind = "") {
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
}

function fmtSize(b) {
  if (b == null) return "—";
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (b < 1024) return `${b.toFixed(1)} ${unit}`;
    b /= 1024;
  }
  return `${b.toFixed(1)} TB`;
}

function fmtTime(s) {
  if (s == null) return "—";
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function filenameFrom(res, fallback) {
  const cd = res.headers.get("Content-Disposition") || "";
  const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (star) return decodeURIComponent(star[1]);
  const plain = cd.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : fallback;
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function errorFrom(res) {
  try {
    return (await res.json()).error || res.statusText;
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

/** POST a form and download whatever file the server sends back. Resolves to { name, headers }. */
async function postForFile(url, form, fallbackName) {
  const res = await fetch(url, { method: "POST", body: form });
  if (!res.ok) throw new Error(await errorFrom(res));
  const name = filenameFrom(res, fallbackName);
  saveBlob(await res.blob(), name);
  return { name, headers: res.headers };
}

async function postAndDownload(url, form, fallbackName) {
  return (await postForFile(url, form, fallbackName)).name;
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorFrom(res));
  return res.json();
}

/** Disable the button, show progress text, and report success/failure. */
async function runTask(button, statusEl, busyText, task) {
  button.disabled = true;
  setStatus(statusEl, busyText, "busy");
  try {
    const msg = await task();
    setStatus(statusEl, msg, "ok");
    toast(msg);
  } catch (e) {
    setStatus(statusEl, e.message, "err");
    toast(e.message, true);
  } finally {
    button.disabled = false;
  }
}

// ── Navigation, theme & mobile menu ─────────────────────────────────────────

function setMenu(open) {
  $("#sidebar").classList.toggle("open", open);
  $("#scrim").classList.toggle("open", open);
}

function showTab(id) {
  if (!$(`#tab-${id}`)) id = "home";
  $$(".nav-item[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${id}`));
  const label = $(`.nav-item[data-tab="${id}"]`).textContent.trim();
  $("#crumb").textContent = label;
  document.title = id === "home" ? "Aura Tools" : `${label} · Aura Tools`;
  history.replaceState(null, "", `#${id}`);
  setMenu(false);
  window.scrollTo(0, 0);
  if (id === "editor") requestAnimationFrame(() => Editor.redraw());
}
$$("[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("#menu-btn").addEventListener("click", () => setMenu(true));
$("#scrim").addEventListener("click", () => setMenu(false));

function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set) return set;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function renderThemeToggle() {
  const dark = currentTheme() === "dark";
  $("#theme-toggle use").setAttribute("href", dark ? "#i-sun" : "#i-moon");
  $("#theme-toggle span").textContent = dark ? "Light mode" : "Dark mode";
}
$("#theme-toggle").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("aura-theme", next); } catch {}
  renderThemeToggle();
});
renderThemeToggle();

// ── Dropzones & file lists ──────────────────────────────────────────────────

function acceptsFile(accept, file) {
  if (!accept) return true;
  const ext = "." + file.name.split(".").pop().toLowerCase();
  return accept.split(",").includes(ext);
}

/** Wire a dropzone's drag/drop and its hidden <input type=file> to onFiles(files). */
function bindDropzone(zone, onFiles) {
  const input = $("input[type=file]", zone);
  const handle = (fileList) => {
    const all = [...fileList];
    const ok = all.filter((f) => acceptsFile(zone.dataset.accept, f));
    if (ok.length < all.length) toast(`Skipped ${all.length - ok.length} unsupported file(s)`, true);
    if (ok.length) onFiles("single" in zone.dataset ? ok.slice(0, 1) : ok);
  };
  if (input) {
    input.accept = zone.dataset.accept || "";
    input.addEventListener("change", () => { handle(input.files); input.value = ""; });
  }
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    handle(e.dataTransfer.files);
  });
}

class FileList {
  constructor(listEl, { single = false, onChange = null } = {}) {
    this.el = listEl;
    this.files = [];
    this.single = single;
    this.ordered = "ordered" in listEl.dataset;
    this.onChange = onChange;
  }

  add(files) {
    if (this.single) {
      this.files = [files[0]];
    } else {
      const key = (f) => `${f.name}|${f.size}|${f.lastModified}`;
      const existing = new Set(this.files.map(key));
      const added = files.filter((f) => !existing.has(key(f)));
      added.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
      this.files.push(...added);
    }
    this.render();
  }

  clear() { this.files = []; this.render(); }

  move(i, delta) {
    const j = i + delta;
    if (j < 0 || j >= this.files.length) return;
    [this.files[i], this.files[j]] = [this.files[j], this.files[i]];
    this.render();
  }

  render() {
    this.el.replaceChildren(...this.files.map((f, i) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="num">${i + 1}.</span><span class="name"></span>` +
        `<span class="size">${fmtSize(f.size)}</span>` +
        (this.ordered && !this.single
          ? `<button class="btn icon" data-act="up" title="Move up">↑</button>` +
            `<button class="btn icon" data-act="down" title="Move down">↓</button>`
          : "") +
        `<button class="btn icon" data-act="remove" title="Remove">✕</button>`;
      $(".name", li).textContent = f.name;
      li.addEventListener("click", (e) => {
        const act = e.target.dataset.act;
        if (act === "up") this.move(i, -1);
        else if (act === "down") this.move(i, 1);
        else if (act === "remove") { this.files.splice(i, 1); this.render(); }
      });
      return li;
    }));
    if (this.onChange) this.onChange(this.files);
  }

  formData(field = "files") {
    const fd = new FormData();
    this.files.forEach((f) => fd.append(field, f, f.name));
    return fd;
  }
}

const lists = {};
$$(".dropzone[data-list]").forEach((zone) => {
  const list = new FileList($(`#${zone.dataset.list}`), { single: "single" in zone.dataset });
  lists[zone.dataset.list] = list;
  bindDropzone(zone, (files) => list.add(files));
});

// ── Convert tab ─────────────────────────────────────────────────────────────

$("#convert-run").addEventListener("click", (e) => {
  const list = lists["convert-list"];
  if (!list.files.length) return toast("Please add at least one file to convert.", true);
  runTask(e.currentTarget, $("#convert-status"), "Creating PDF…", async () => {
    const fd = list.formData();
    fd.append("fit_a4", $("#convert-fit").checked ? "1" : "0");
    fd.append("compress", $("#convert-compress").checked ? "1" : "0");
    await postAndDownload("/api/convert", fd, "output.pdf");
    return `PDF created from ${list.files.length} file(s)!`;
  });
});

// ── File converter tab ──────────────────────────────────────────────────────

const fcZone = $('.dropzone[data-list="fc-list"]');

function fcRouteChanged() {
  const opt = $("#fc-route").selectedOptions[0];
  const accept = opt.dataset.accept;
  const out = opt.dataset.out;
  fcZone.dataset.accept = accept;
  $("input[type=file]", fcZone).accept = accept;
  $("#fc-hint").textContent = accept.replaceAll(".", "").toUpperCase().replaceAll(",", ", ");
  $("#fc-quality-field").hidden = out !== "jpg" && out !== "webp";
  $("#fc-dpi-field").hidden = accept !== ".pdf";
  $("#fc-merge-field").hidden = out !== "pdf";
  lists["fc-list"].clear();
}
$("#fc-route").addEventListener("change", fcRouteChanged);
$("#fc-quality").addEventListener("input", (e) => ($("#fc-quality-val").textContent = e.target.value));
fcRouteChanged();

$("#fc-run").addEventListener("click", (e) => {
  const list = lists["fc-list"];
  if (!list.files.length) return toast("Please add at least one file.", true);
  runTask(e.currentTarget, $("#fc-status"), "Converting…", async () => {
    const fd = list.formData();
    fd.append("route", $("#fc-route").value);
    fd.append("quality", $("#fc-quality").value);
    fd.append("dpi", $("#fc-dpi").value);
    fd.append("merge", $("#fc-merge").checked ? "1" : "0");
    const name = await postAndDownload("/api/fileconv", fd, "converted");
    return `Converted ${list.files.length} file(s) → ${name}`;
  });
});

// ── Merge tab ───────────────────────────────────────────────────────────────

$("#merge-run").addEventListener("click", (e) => {
  const list = lists["merge-list"];
  if (list.files.length < 2) return toast("Please add at least 2 PDF files to merge.", true);
  runTask(e.currentTarget, $("#merge-status"), "Merging…", async () => {
    await postAndDownload("/api/merge", list.formData(), "merged.pdf");
    return `Merged ${list.files.length} PDFs successfully!`;
  });
});

// ── Split tab ───────────────────────────────────────────────────────────────

lists["split-list"].onChange = async (files) => {
  if (!files.length) return setStatus($("#split-status"), "");
  try {
    const fd = new FormData();
    fd.append("file", files[0]);
    const res = await fetch("/api/pdf/info", { method: "POST", body: fd });
    if (!res.ok) throw new Error(await errorFrom(res));
    setStatus($("#split-status"), `${(await res.json()).pages} pages`);
  } catch (e) {
    setStatus($("#split-status"), e.message, "err");
  }
};

$("#split-run").addEventListener("click", (e) => {
  const list = lists["split-list"];
  if (!list.files.length) return toast("Please add a PDF to split.", true);
  const mode = $("input[name=split-mode]:checked").value;
  const range = $("#split-range").value.trim();
  const n = parseInt($("#split-n").value, 10);
  if (mode === "range" && !range) return toast("Please enter a page range (e.g., 1-3, 5).", true);
  if (mode === "every_n" && !(n >= 1)) return toast("Please enter a valid number (e.g., 2).", true);

  runTask(e.currentTarget, $("#split-status"), "Splitting…", async () => {
    const fd = list.formData("file");
    fd.append("mode", mode);
    fd.append("range", range);
    fd.append("n", String(n));
    const name = await postAndDownload("/api/split", fd, "split.pdf");
    return `Saved ${name}`;
  });
});
$("#split-range").addEventListener("focus", () => ($("input[value=range]").checked = true));
$("#split-n").addEventListener("focus", () => ($("input[value=every_n]").checked = true));

// ── Organize pages tab ──────────────────────────────────────────────────────

const Reorder = {
  file: null,
  thumbs: [],
  pages: [],   // current result, in order: { page: source index, rotate: degrees }
  dragFrom: null,

  fresh() { return this.thumbs.map((_, i) => ({ page: i, rotate: 0 })); },

  async load(file) {
    const status = $("#reorder-status");
    this.file = file;
    $("#reorder-grid").replaceChildren();
    $("#reorder-run").disabled = true;
    setStatus(status, "Rendering pages…", "busy");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/pdf/thumbnails", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await errorFrom(res));
      this.thumbs = (await res.json()).pages;
      this.pages = this.fresh();
      $("#reorder-tools").hidden = false;
      this.render();
    } catch (e) {
      setStatus(status, e.message, "err");
      toast(e.message, true);
    }
  },

  summary() {
    const kept = this.pages.length, total = this.thumbs.length;
    $("#reorder-name").textContent = `${this.file.name} — ${kept} of ${total} pages kept`;
    $("#reorder-run").disabled = !kept;
    setStatus($("#reorder-status"), kept ? "Drag to reorder · hover a page to rotate or delete it" : "Every page was deleted. Press Reset.");
  },

  render() {
    this.summary();
    $("#reorder-grid").replaceChildren(...this.pages.map((item, pos) => {
      const el = document.createElement("div");
      el.className = "page";
      el.draggable = true;
      el.innerHTML = `<div class="thumb"><img alt=""></div><div class="label">${pos + 1} · page ${item.page + 1}</div>` +
        `<div class="page-tools">` +
        `<button class="btn icon" data-act="left" title="Rotate left">↺</button>` +
        `<button class="btn icon" data-act="right" title="Rotate right">↻</button>` +
        `<button class="btn icon" data-act="del" title="Delete page">✕</button></div>`;
      const img = $("img", el);
      img.src = this.thumbs[item.page];
      img.addEventListener("load", () => this.fit(img, item.rotate), { once: true });
      if (img.complete) this.fit(img, item.rotate);
      el.addEventListener("click", (e) => {
        const act = e.target.dataset.act;
        if (!act) return;
        if (act === "del") this.pages.splice(pos, 1);
        else item.rotate = (item.rotate + (act === "left" ? 270 : 90)) % 360;
        this.render();
      });
      el.addEventListener("dragstart", () => { this.dragFrom = pos; el.classList.add("dragging"); });
      el.addEventListener("dragend", () => el.classList.remove("dragging"));
      el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop-target"); });
      el.addEventListener("dragleave", () => el.classList.remove("drop-target"));
      el.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this.dragFrom == null || this.dragFrom === pos) return this.render();
        const [moved] = this.pages.splice(this.dragFrom, 1);
        this.pages.splice(pos, 0, moved);
        this.dragFrom = null;
        this.render();
      });
      return el;
    }));
  },

  /** Turn the thumbnail inside its box. A sideways page is drawn narrower so that, once turned, it fills the box. */
  fit(img, rotate) {
    const w = img.naturalWidth, h = img.naturalHeight;
    if (!w || !h) return;
    const sideways = rotate % 180 === 90;
    img.parentElement.style.setProperty("--ratio", sideways ? h / w : w / h);
    img.style.width = sideways ? `${(w / h) * 100}%` : "100%";
    img.style.transform = `rotate(${rotate}deg)`;
  },
};

bindDropzone($("#reorder-drop"), (files) => Reorder.load(files[0]));
$("#reorder-reverse").addEventListener("click", () => { Reorder.pages.reverse(); Reorder.render(); });
$("#reorder-reset").addEventListener("click", () => { Reorder.pages = Reorder.fresh(); Reorder.render(); });
$("#reorder-run").addEventListener("click", (e) => {
  if (!Reorder.file) return;
  runTask(e.currentTarget, $("#reorder-status"), "Saving…", async () => {
    const fd = new FormData();
    fd.append("file", Reorder.file);
    fd.append("pages", JSON.stringify(Reorder.pages));
    await postAndDownload("/api/reorder", fd, "organized.pdf");
    return `Saved PDF with ${Reorder.pages.length} page(s)!`;
  });
});

// ── Compress tab ────────────────────────────────────────────────────────────

$("#compress-run").addEventListener("click", (e) => {
  const list = lists["compress-list"];
  if (!list.files.length) return toast("Please add a PDF to compress.", true);
  runTask(e.currentTarget, $("#compress-status"), "Compressing…", async () => {
    const fd = list.formData("file");
    fd.append("level", $("input[name=compress-level]:checked").value);
    const { name, headers } = await postForFile("/api/compress", fd, "compressed.pdf");
    const before = Number(headers.get("X-Original-Size")), after = Number(headers.get("X-New-Size"));
    if (after >= before) return `Saved ${name}. This PDF was already as small as it gets (${fmtSize(before)}).`;
    return `${fmtSize(before)} → ${fmtSize(after)} (${Math.round((1 - after / before) * 100)}% smaller). Saved ${name}.`;
  });
});

// ── Protect / unlock tab ────────────────────────────────────────────────────

function protectModeChanged() {
  const unlock = $("#protect-mode").value === "unlock";
  $("#protect-run-label").textContent = unlock ? "Unlock PDF" : "Protect PDF";
  $("#protect-hint").textContent = unlock
    ? "The current password of this PDF."
    : "Needed to open the PDF. If you forget it, it can't be recovered.";
}
$("#protect-mode").addEventListener("change", protectModeChanged);

$("#protect-run").addEventListener("click", (e) => {
  const list = lists["protect-list"];
  if (!list.files.length) return toast("Please add a PDF first.", true);
  const password = $("#protect-password").value;
  if (!password) return toast("Please enter a password.", true);
  const unlock = $("#protect-mode").value === "unlock";
  runTask(e.currentTarget, $("#protect-status"), unlock ? "Unlocking…" : "Protecting…", async () => {
    const fd = list.formData("file");
    fd.append("mode", $("#protect-mode").value);
    fd.append("password", password);
    const { name } = await postForFile("/api/protect", fd, "protected.pdf");
    $("#protect-password").value = "";
    return unlock ? `Unlocked. Saved ${name}.` : `Protected with your password. Saved ${name}.`;
  });
});

// ── Watermark & page numbers tab ────────────────────────────────────────────

$("#stamp-size").addEventListener("input", (e) => ($("#stamp-size-val").textContent = e.target.value));
$("#stamp-opacity").addEventListener("input", (e) => ($("#stamp-opacity-val").textContent = `${e.target.value}%`));

$("#stamp-run").addEventListener("click", (e) => {
  const list = lists["stamp-list"];
  if (!list.files.length) return toast("Please add a PDF first.", true);
  const text = $("#stamp-text").value.trim(), numbering = $("#stamp-numbering").value;
  if (!text && !numbering) return toast("Enter watermark text or choose a page-number style.", true);
  runTask(e.currentTarget, $("#stamp-status"), "Stamping…", async () => {
    const fd = list.formData("file");
    fd.append("watermark", text);
    fd.append("size", $("#stamp-size").value);
    fd.append("opacity", $("#stamp-opacity").value);
    fd.append("numbering", numbering);
    fd.append("position", $("#stamp-position").value);
    const { name } = await postForFile("/api/stamp", fd, "stamped.pdf");
    return `Saved ${name}.`;
  });
});

// ── Resize & compress images tab ────────────────────────────────────────────

function irRefresh() {
  const mode = $("#ir-mode").value, fmt = $("#ir-format").value;
  $("#ir-percent-field").hidden = mode !== "percent";
  $("#ir-box-field").hidden = mode !== "fit" && mode !== "exact";
  $("#ir-box-hint").textContent = mode === "fit" ? "Never enlarges. Leave one empty to limit only the other."
    : "Fills the whole size and trims whatever doesn't fit.";
  $("#ir-quality-field").hidden = fmt === "png";
}
$("#ir-mode").addEventListener("change", () => { $("#ir-preset").value = ""; irRefresh(); });
$("#ir-format").addEventListener("change", irRefresh);
$("#ir-percent").addEventListener("input", (e) => ($("#ir-percent-val").textContent = `${e.target.value}%`));
$("#ir-quality").addEventListener("input", (e) => ($("#ir-quality-val").textContent = e.target.value));
$("#ir-preset").addEventListener("change", (e) => {
  const opt = e.target.selectedOptions[0];
  if (!opt.value) return;
  $("#ir-mode").value = opt.dataset.mode;
  $("#ir-width").value = opt.dataset.w;
  $("#ir-height").value = opt.dataset.h;
  irRefresh();
});
["ir-width", "ir-height"].forEach((id) => $(`#${id}`).addEventListener("input", () => ($("#ir-preset").value = "")));
irRefresh();

$("#ir-run").addEventListener("click", (e) => {
  const list = lists["ir-list"];
  if (!list.files.length) return toast("Please add at least one photo.", true);
  runTask(e.currentTarget, $("#ir-status"), "Working…", async () => {
    const fd = list.formData();
    fd.append("mode", $("#ir-mode").value);
    fd.append("percent", $("#ir-percent").value);
    fd.append("width", $("#ir-width").value || "0");
    fd.append("height", $("#ir-height").value || "0");
    fd.append("format", $("#ir-format").value);
    fd.append("quality", $("#ir-quality").value);
    fd.append("max_kb", $("#ir-maxkb").value || "0");
    fd.append("strip", $("#ir-strip").checked ? "1" : "0");
    const { name, headers } = await postForFile("/api/imgresize", fd, "images");
    const before = Number(headers.get("X-Original-Size")), after = Number(headers.get("X-New-Size"));
    const notes = headers.get("X-Notes");
    const sizes = after < before ? `${fmtSize(before)} → ${fmtSize(after)}` : `${fmtSize(after)}`;
    return `${sizes}. Saved ${name}.` + (notes ? ` Note: ${notes}` : "");
  });
});

// ── Image editor (runs entirely in the browser) ─────────────────────────────

const Editor = {
  canvas: $("#ed-canvas"),
  stage: $("#ed-stage"),
  name: "image",
  original: null, // canvas with the image as opened
  current: null,  // canvas with edits applied
  scale: 1,       // display px → image px
  sel: null,      // crop selection in display coords {x1, y1, x2, y2}
  dragging: false,

  async open(file) {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      this.original = this.toCanvas(bitmap.width, bitmap.height, (ctx) => ctx.drawImage(bitmap, 0, 0));
      this.current = this.original;
      this.name = file.name.replace(/\.[^.]+$/, "") || "image";
      $("#ed-empty").hidden = true;
      this.canvas.style.display = "block";
      $$("[data-ed]").forEach((b) => (b.disabled = b.dataset.ed === "crop"));
      this.sel = null;
      this.redraw();
    } catch {
      toast("Could not open that image.", true);
    }
  },

  toCanvas(w, h, draw) {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    draw(c.getContext("2d"));
    return c;
  },

  rotate(clockwise) {
    const src = this.current;
    this.apply(this.toCanvas(src.height, src.width, (ctx) => {
      ctx.translate(src.height / 2, src.width / 2);
      ctx.rotate(((clockwise ? 90 : -90) * Math.PI) / 180);
      ctx.drawImage(src, -src.width / 2, -src.height / 2);
    }));
  },

  flip(horizontal) {
    const src = this.current;
    this.apply(this.toCanvas(src.width, src.height, (ctx) => {
      if (horizontal) { ctx.translate(src.width, 0); ctx.scale(-1, 1); }
      else { ctx.translate(0, src.height); ctx.scale(1, -1); }
      ctx.drawImage(src, 0, 0);
    }));
  },

  crop() {
    if (!this.sel) return;
    const s = this.normSel();
    const x = Math.round(s.x * this.scale), y = Math.round(s.y * this.scale);
    const w = Math.min(Math.round(s.w * this.scale), this.current.width - x);
    const h = Math.min(Math.round(s.h * this.scale), this.current.height - y);
    if (w < 1 || h < 1) return;
    this.apply(this.toCanvas(w, h, (ctx) => ctx.drawImage(this.current, x, y, w, h, 0, 0, w, h)));
  },

  apply(canvas) {
    this.current = canvas;
    this.sel = null;
    $('[data-ed="crop"]').disabled = true;
    this.redraw();
  },

  normSel() {
    const { x1, y1, x2, y2 } = this.sel;
    return { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) };
  },

  redraw() {
    if (!this.current) return;
    const maxW = this.stage.clientWidth - 24, maxH = this.stage.clientHeight - 24;
    if (maxW <= 10 || maxH <= 10) return;
    const fit = Math.min(maxW / this.current.width, maxH / this.current.height, 1);
    const dw = Math.max(1, Math.round(this.current.width * fit));
    const dh = Math.max(1, Math.round(this.current.height * fit));
    this.canvas.width = dw;
    this.canvas.height = dh;
    this.scale = this.current.width / dw;

    const ctx = this.canvas.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(this.current, 0, 0, dw, dh);

    if (this.sel) {
      const s = this.normSel();
      ctx.fillStyle = "rgba(0,0,0,.5)";
      ctx.beginPath();
      ctx.rect(0, 0, dw, dh);
      ctx.rect(s.x, s.y, s.w, s.h);
      ctx.fill("evenodd");
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = "#2ECC71";
      ctx.lineWidth = 2;
      ctx.strokeRect(s.x, s.y, s.w, s.h);
    }
  },

  pointer(e) {
    const r = this.canvas.getBoundingClientRect();
    const k = this.canvas.width / r.width;
    return {
      x: Math.max(0, Math.min(this.canvas.width, (e.clientX - r.left) * k)),
      y: Math.max(0, Math.min(this.canvas.height, (e.clientY - r.top) * k)),
    };
  },

  save() {
    const type = $("#ed-format").value;
    const out = type === "jpeg"
      ? this.toCanvas(this.current.width, this.current.height, (ctx) => {
          ctx.fillStyle = "#fff"; // JPEG has no transparency
          ctx.fillRect(0, 0, this.current.width, this.current.height);
          ctx.drawImage(this.current, 0, 0);
        })
      : this.current;
    out.toBlob((blob) => {
      saveBlob(blob, `${this.name}_edited.${type === "jpeg" ? "jpg" : "png"}`);
      toast("Image saved!");
    }, `image/${type}`, 0.95);
  },
};

$("#ed-open").addEventListener("change", (e) => {
  if (e.target.files[0]) Editor.open(e.target.files[0]);
  e.target.value = "";
});
$$("[data-ed]").forEach((b) => b.addEventListener("click", () => {
  const actions = {
    rotl: () => Editor.rotate(false),
    rotr: () => Editor.rotate(true),
    fliph: () => Editor.flip(true),
    flipv: () => Editor.flip(false),
    crop: () => Editor.crop(),
    reset: () => Editor.apply(Editor.original),
    save: () => Editor.save(),
  };
  actions[b.dataset.ed]();
}));

Editor.canvas.addEventListener("pointerdown", (e) => {
  const p = Editor.pointer(e);
  Editor.sel = { x1: p.x, y1: p.y, x2: p.x, y2: p.y };
  Editor.dragging = true;
  Editor.canvas.setPointerCapture(e.pointerId);
  $('[data-ed="crop"]').disabled = true;
});
Editor.canvas.addEventListener("pointermove", (e) => {
  if (!Editor.dragging) return;
  const p = Editor.pointer(e);
  Object.assign(Editor.sel, { x2: p.x, y2: p.y });
  Editor.redraw();
});
Editor.canvas.addEventListener("pointerup", () => {
  Editor.dragging = false;
  const s = Editor.sel && Editor.normSel();
  if (!s || s.w < 10 || s.h < 10) Editor.sel = null;
  $('[data-ed="crop"]').disabled = !Editor.sel;
  Editor.redraw();
});

Editor.stage.addEventListener("dragover", (e) => { e.preventDefault(); Editor.stage.classList.add("over"); });
Editor.stage.addEventListener("dragleave", () => Editor.stage.classList.remove("over"));
Editor.stage.addEventListener("drop", (e) => {
  e.preventDefault();
  Editor.stage.classList.remove("over");
  const file = [...e.dataTransfer.files].find((f) => f.type.startsWith("image/"));
  if (file) Editor.open(file);
});
new ResizeObserver(() => Editor.redraw()).observe(Editor.stage);

// ── Video downloaders (YouTube, Facebook, Instagram) ────────────────────────
// One instance per site. `prefix` matches the element ids in the page ("yt-url", "fb-url", …).

class Downloader {
  constructor(platform, prefix) {
    this.platform = platform;
    this.prefix = prefix;
    this.pollTimer = null;
    this.storeKey = `aura-${prefix}-job`;
  }

  el(name) { return $(`#${this.prefix}-${name}`); }

  body() {
    return {
      platform: this.platform,
      url: this.el("url").value.trim(),
      quality: this.el("quality").value,
      container: this.el("container").value,
      subtitles: this.el("subs")?.checked ?? false,
      playlist: this.el("playlist").checked,
      pace: this.el("pace").value,
      save_dir: this.el("savedir").value.trim(),
      cookies: this.el("cookies").value,
    };
  }

  log(lines) {
    const el = this.el("log");
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    el.textContent = lines.join("\n");
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  async fetchInfo() {
    const status = this.el("status");
    setStatus(status, "Fetching video info…", "busy");
    try {
      const info = await postJSON("/api/yt/info", this.body());
      this.el("title").textContent = info.title;
      const views = info.view_count != null ? `${info.view_count.toLocaleString()} views` : "";
      this.el("meta").textContent = [info.uploader, fmtTime(info.duration), views].filter(Boolean).join("   ·   ");
      const thumb = this.el("thumb");
      thumb.hidden = !info.thumbnail;
      if (info.thumbnail) thumb.src = info.thumbnail;
      setStatus(status, "Ready to download.", "ok");
    } catch (e) {
      setStatus(status, e.message, "err");
      toast(e.message, true);
    }
  }

  async start() {
    const btn = this.el("run");
    btn.disabled = true;
    btn.lastChild.textContent = "Downloading…";
    this.el("progress").hidden = false;
    this.el("files").replaceChildren();
    this.el("bar").style.width = "0";
    setStatus(this.el("status"), "Starting download…", "busy");
    try {
      const { job_id } = await postJSON("/api/yt/download", this.body());
      this.remember(job_id);
      this.poll(job_id, true);
    } catch (e) {
      this.finish();
      setStatus(this.el("status"), e.message, "err");
      toast(e.message, true);
    }
  }

  remember(jobId) {
    try { jobId ? localStorage.setItem(this.storeKey, jobId) : localStorage.removeItem(this.storeKey); } catch {}
  }

  /** After a page reload, reattach to a download that is still running or recently finished. */
  async resume() {
    let jobId;
    try { jobId = localStorage.getItem(this.storeKey); } catch {}
    if (!jobId) return;
    const res = await fetch(`/api/yt/jobs/${jobId}`).catch(() => null);
    if (!res || !res.ok) return this.remember(null);
    const job = await res.json();
    if (job.status === "error") return this.remember(null);
    this.el("progress").hidden = false;
    if (job.status !== "done") {
      this.el("run").disabled = true;
      this.el("run").lastChild.textContent = "Downloading…";
    }
    this.poll(jobId, false);
  }

  async poll(jobId, autoSave = true) {
    let job;
    try {
      const res = await fetch(`/api/yt/jobs/${jobId}`);
      if (!res.ok) throw new Error(await errorFrom(res));
      job = await res.json();
    } catch (e) {
      this.finish();
      this.remember(null);
      setStatus(this.el("status"), e.message, "err");
      return;
    }

    this.render(job);
    this.log(job.log);

    if (job.status === "done") {
      this.finish();
      const saved = job.saved_to ? `Saved ${job.files.length} file(s) to ${job.saved_to}` : "";
      if (job.warning) {
        setStatus(this.el("status"), [job.warning, saved].filter(Boolean).join(" "), "err");
        toast(job.warning, true);
      } else if (saved) {
        setStatus(this.el("status"), saved, "ok");
        toast("Download complete!");
      } else {
        setStatus(this.el("status"), "Download complete! Click a file to save it.", "ok");
        toast("Download complete!");
      }
      this.showFiles(jobId, job.files, Boolean(job.saved_to));
      // Single file: save it straight away (not again when reattaching after a reload).
      // Files saved into a chosen folder are already where they should be.
      if (autoSave && !job.saved_to && job.files.length === 1) location.href = `/api/yt/jobs/${jobId}/files/0`;
    } else if (job.status === "error") {
      this.finish();
      this.remember(null);
      setStatus(this.el("status"), job.error || "Download failed", "err");
      toast(job.error || "Download failed", true);
    } else {
      const pct = `${(job.progress * 100).toFixed(1)}%`;
      const where = job.item_count > 1 ? `Video ${job.item_index} of ${job.item_count} · ` : "";
      const label = job.status === "processing" ? `${where}Processing…` : `${where}Downloading… ${pct}`;
      setStatus(this.el("status"), label, "busy");
      this.pollTimer = setTimeout(() => this.poll(jobId, autoSave), 700);
    }
  }

  /** Bar, timer and counters. The server already smooths these, so just show them. */
  render(job) {
    const playlist = job.item_count > 1;
    const finished = job.status === "done";
    this.el("bar").style.width = `${((finished ? 1 : job.progress) * 100).toFixed(1)}%`;
    this.el("speed").textContent = job.speed != null ? fmtSize(job.speed) + "/s" : "—";
    this.el("eta").textContent = finished ? "0:00" : fmtTime(job.eta);
    this.el("done").textContent = fmtSize(job.downloaded);

    $(`#${this.prefix}-size-label`).textContent = playlist ? "Videos" : "Size";
    if (playlist) {
      const complete = finished ? job.item_count : Math.max(job.item_index - 1, 0);
      const skipped = job.skipped ? ` · ${job.skipped} skipped` : "";
      this.el("size").textContent = `${complete} of ${job.item_count} done${skipped}`;
    } else {
      this.el("size").textContent = fmtSize(job.total);
    }

    const now = this.el("now");
    const title = Object.assign(document.createElement("span"), { className: "now-title" });
    const note = Object.assign(document.createElement("span"), { className: "now-note" });
    const active = !finished && job.status !== "error";
    if (job.item_title && active) {
      title.textContent = playlist ? `${job.item_index}/${job.item_count}  ${job.item_title}` : job.item_title;
    }
    note.textContent = active ? job.note || "" : "";
    now.replaceChildren(title, note);
  }

  /** The finished files: save links for browser downloads, plain names when they went into a folder. */
  showFiles(jobId, files, savedInFolder = false) {
    this.el("files").replaceChildren(...files.map((name, i) => {
      const li = document.createElement("li");
      li.append(savedInFolder
        ? Object.assign(document.createElement("span"), { className: "name", textContent: name })
        : Object.assign(document.createElement("a"), {
          href: `/api/yt/jobs/${jobId}/files/${i}`,
          className: "name",
          textContent: name,
        }));
      return li;
    }));
  }

  /** The "Save to" row: remembered between visits and shared by all three downloader pages. */
  initSaveFolder() {
    const input = this.el("savedir"), clear = this.el("clearsave");
    const refresh = () => { clear.hidden = !input.value.trim(); };
    try { input.value = localStorage.getItem("aura-savedir") || ""; } catch {}
    refresh();
    input.addEventListener("input", () => {
      refresh();
      try { localStorage.setItem("aura-savedir", input.value.trim()); } catch {}
    });
    clear.addEventListener("click", () => { input.value = ""; input.dispatchEvent(new Event("input")); });
    this.el("browse").addEventListener("click", async (e) => {
      const button = e.currentTarget;
      button.disabled = true;
      try {
        const { path } = await postJSON("/api/pick-folder", { start: input.value.trim() });
        if (path) { input.value = path; input.dispatchEvent(new Event("input")); }
      } catch (err) {
        toast(err.message, true);
      } finally {
        button.disabled = false;
      }
    });
  }

  finish() {
    clearTimeout(this.pollTimer);
    const btn = this.el("run");
    btn.disabled = false;
    btn.lastChild.textContent = "Download";
  }

  init() {
    this.el("fetch").addEventListener("click", () => this.fetchInfo());
    this.el("url").addEventListener("keydown", (e) => e.key === "Enter" && this.fetchInfo());
    this.el("run").addEventListener("click", () => this.start());
    this.initSaveFolder();
    this.el("quality").addEventListener("change", (e) => {
      if (["4K (2160p)", "2K (1440p)"].includes(e.target.value)) {
        this.el("container").value = "mkv";
        toast("Container switched to MKV: 4K/2K streams don't fit in MP4");
      }
    });
    this.resume();
  }
}

// platform key → element-id prefix (kept in sync with PLATFORMS in yt_jobs.py)
Object.entries({ youtube: "yt", facebook: "fb", instagram: "ig" })
  .forEach(([platform, prefix]) => new Downloader(platform, prefix).init());

// ── Video & audio tools tab ─────────────────────────────────────────────────

const Media = {
  pollTimer: null,
  jobId: null,

  el(name) { return $(`#mv-${name}`); },

  refreshFields() {
    const op = this.el("op").value;
    $$("#tab-media [data-ops]").forEach((row) => { row.hidden = !row.dataset.ops.split(" ").includes(op); });
  },

  formData(file) {
    const op = this.el("op").value;
    const fd = new FormData();
    fd.append("file", file, file.name);
    fd.append("op", op);
    const set = (k, v) => fd.append(k, v);
    if (op === "convert") set("format", this.el("format").value);
    if (op === "audio") { set("format", this.el("aformat").value); set("bitrate", this.el("bitrate").value); }
    if (op === "trim") { set("start", this.el("start").value); set("end", this.el("end").value); set("mode", this.el("mode").value); }
    if (op === "compress") { set("level", this.el("level").value); set("max_height", this.el("height").value); }
    if (op === "gif") { set("start", this.el("start").value); set("length", this.el("length").value); set("width", this.el("width").value); set("fps", this.el("fps").value); }
    return fd;
  },

  busy(on) {
    this.el("run").disabled = on;
    this.el("cancel").hidden = !on;
    this.el("cancel").disabled = false;
  },

  /** POST the file with real upload progress (fetch can't report it), then follow the job. */
  start() {
    const list = lists["mv-list"];
    if (!list.files.length) return toast("Please add a video or audio file first.", true);
    this.busy(true);
    this.el("progress").hidden = false;
    this.el("files").replaceChildren();
    this.el("bar").style.width = "0";
    this.el("pct").textContent = this.el("eta").textContent = this.el("rate").textContent = "—";
    this.el("size").textContent = "—";
    const status = this.el("status");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/media/start");
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = (e.loaded / e.total) * 100;
      this.el("bar").style.width = `${pct.toFixed(1)}%`;
      setStatus(status, `Uploading… ${pct.toFixed(0)}% (${fmtSize(e.loaded)} of ${fmtSize(e.total)})`, "busy");
    };
    xhr.onerror = () => this.fail("Couldn't reach the app. Is it still running?");
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status !== 200) return this.fail(body.error || `${xhr.status} ${xhr.statusText}`);
      this.jobId = body.job_id;
      this.el("bar").style.width = "0";
      this.poll();
    };
    xhr.send(this.formData(list.files[0]));
  },

  fail(message) {
    clearTimeout(this.pollTimer);
    this.busy(false);
    setStatus(this.el("status"), message, "err");
    toast(message, true);
  },

  async poll() {
    let job;
    try {
      const res = await fetch(`/api/media/jobs/${this.jobId}`);
      if (!res.ok) throw new Error(await errorFrom(res));
      job = await res.json();
    } catch (e) {
      return this.fail(e.message);
    }
    const pct = job.progress * 100;
    this.el("bar").style.width = `${pct.toFixed(1)}%`;
    this.el("pct").textContent = job.progress > 0 ? `${pct.toFixed(0)}%` : "—";
    this.el("eta").textContent = fmtTime(job.eta);
    this.el("rate").textContent = job.rate || "—";
    const log = this.el("log");
    log.textContent = job.log.join("\n");

    if (job.status === "done") {
      this.busy(false);
      this.el("bar").style.width = "100%";
      this.el("pct").textContent = "100%";
      this.el("eta").textContent = "0:00";
      this.el("size").textContent = fmtSize(job.size);
      setStatus(this.el("status"), "Done! Your file is downloading. Click it below to save it again.", "ok");
      toast("Done!");
      const href = `/api/media/jobs/${this.jobId}/file`;
      const li = document.createElement("li");
      li.append(Object.assign(document.createElement("a"), { href, className: "name", textContent: job.files[0] }));
      this.el("files").replaceChildren(li);
      location.href = href;
    } else if (job.status === "error") {
      this.fail(job.error || "Something went wrong.");
    } else {
      setStatus(this.el("status"), job.progress > 0 ? `Working… ${pct.toFixed(0)}%` : "Working…", "busy");
      this.pollTimer = setTimeout(() => this.poll(), 600);
    }
  },

  async cancel() {
    this.el("cancel").disabled = true;
    if (this.jobId) await fetch(`/api/media/jobs/${this.jobId}/cancel`, { method: "POST" }).catch(() => {});
  },

  init() {
    this.el("op").addEventListener("change", () => this.refreshFields());
    this.el("run").addEventListener("click", () => this.start());
    this.el("cancel").addEventListener("click", () => this.cancel());
    this.refreshFields();
  },
};
Media.init();

// ── One-click ffmpeg setup ──────────────────────────────────────────────────

const Deps = {
  boxes(key) { return $$(`.dep[data-dep="${key}"]`); },

  async install(key) {
    this.boxes(key).forEach((box) => {
      $("[data-install]", box).disabled = true;
      $("[data-progress]", box).hidden = false;
    });
    try {
      const res = await fetch(`/api/deps/${key}/install`, { method: "POST" });
      if (!res.ok) throw new Error(await errorFrom(res));
      this.poll(key);
    } catch (e) {
      this.failed(key, e.message);
    }
  },

  async poll(key) {
    const boxes = this.boxes(key);
    if (!boxes.length) return;
    let all;
    try {
      const res = await fetch("/api/deps");
      if (!res.ok) throw new Error(await errorFrom(res));
      all = await res.json();
    } catch (e) {
      return this.failed(key, e.message);
    }
    const dep = all[key], st = dep.install;
    if (dep.ok || st.status === "done") {
      toast(`${boxes[0].dataset.label} installed`);
      boxes.forEach((box) => box.remove());
      $$(".deps-callout").forEach((c) => { if (!$(".dep", c)) c.remove(); });
      return;
    }
    if (st.status === "error") return this.failed(key, st.error);
    boxes.forEach((box) => {
      $("[data-msg]", box).textContent = st.message || "Working…";
      $("[data-progress] > div", box).style.width = `${(st.progress * 100).toFixed(1)}%`;
    });
    setTimeout(() => this.poll(key), 600);
  },

  failed(key, msg) {
    this.boxes(key).forEach((box) => {
      const btn = $("[data-install]", box);
      btn.disabled = false;
      btn.lastChild.textContent = "Try again";
      $("[data-progress]", box).hidden = true;
      $("[data-msg]", box).textContent = `Install failed: ${msg}`;
    });
    toast(`Install failed: ${msg}`, true);
  },
};

$$(".dep").forEach((box) => {
  $("[data-install]", box).addEventListener("click", () => Deps.install(box.dataset.dep));
});
if ($(".dep")) {
  // Resume progress for installs already running (e.g. after a page reload).
  fetch("/api/deps").then((r) => r.json()).then((all) => {
    new Set($$(".dep").map((box) => box.dataset.dep)).forEach((key) => {
      if (all[key]?.install.status === "running") {
        Deps.boxes(key).forEach((box) => {
          $("[data-install]", box).disabled = true;
          $("[data-progress]", box).hidden = false;
        });
        Deps.poll(key);
      }
    });
  }).catch(() => {});
}

showTab(location.hash.slice(1));
