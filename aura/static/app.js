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

/** POST a form and download whatever file the server sends back. */
async function postAndDownload(url, form, fallbackName) {
  const res = await fetch(url, { method: "POST", body: form });
  if (!res.ok) throw new Error(await errorFrom(res));
  const name = filenameFrom(res, fallbackName);
  saveBlob(await res.blob(), name);
  return name;
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
  $("#fc-quality-field").hidden = out !== "jpg";
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

// ── Reorder tab ─────────────────────────────────────────────────────────────

const Reorder = {
  file: null,
  thumbs: [],
  order: [],
  dragFrom: null,

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
      this.order = this.thumbs.map((_, i) => i);
      $("#reorder-name").textContent = `${file.name} — ${this.thumbs.length} pages`;
      $("#reorder-tools").hidden = false;
      $("#reorder-run").disabled = false;
      setStatus(status, "Drag pages to rearrange them");
      this.render();
    } catch (e) {
      setStatus(status, e.message, "err");
      toast(e.message, true);
    }
  },

  render() {
    $("#reorder-grid").replaceChildren(...this.order.map((page, pos) => {
      const el = document.createElement("div");
      el.className = "page";
      el.draggable = true;
      el.innerHTML = `<img alt=""><div class="label">${pos + 1} · page ${page + 1}</div>`;
      $("img", el).src = this.thumbs[page];
      el.addEventListener("dragstart", () => { this.dragFrom = pos; el.classList.add("dragging"); });
      el.addEventListener("dragend", () => el.classList.remove("dragging"));
      el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop-target"); });
      el.addEventListener("dragleave", () => el.classList.remove("drop-target"));
      el.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this.dragFrom == null || this.dragFrom === pos) return this.render();
        const [moved] = this.order.splice(this.dragFrom, 1);
        this.order.splice(pos, 0, moved);
        this.dragFrom = null;
        this.render();
      });
      return el;
    }));
  },
};

bindDropzone($("#reorder-drop"), (files) => Reorder.load(files[0]));
$("#reorder-reverse").addEventListener("click", () => { Reorder.order.reverse(); Reorder.render(); });
$("#reorder-reset").addEventListener("click", () => {
  Reorder.order = Reorder.thumbs.map((_, i) => i);
  Reorder.render();
});
$("#reorder-run").addEventListener("click", (e) => {
  if (!Reorder.file) return;
  runTask(e.currentTarget, $("#reorder-status"), "Saving…", async () => {
    const fd = new FormData();
    fd.append("file", Reorder.file);
    fd.append("order", Reorder.order.join(","));
    await postAndDownload("/api/reorder", fd, "reordered.pdf");
    return `Saved reordered PDF (${Reorder.order.length} pages)!`;
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

// ── YouTube downloader ──────────────────────────────────────────────────────

const YT = {
  pollTimer: null,

  body() {
    return {
      url: $("#yt-url").value.trim(),
      quality: $("#yt-quality").value,
      container: $("#yt-container").value,
      subtitles: $("#yt-subs").checked,
      playlist: $("#yt-playlist").checked,
      cookies: $("#yt-cookies").value,
    };
  },

  log(lines) {
    const el = $("#yt-log");
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    el.textContent = lines.join("\n");
    if (atBottom) el.scrollTop = el.scrollHeight;
  },

  async fetchInfo() {
    const status = $("#yt-status");
    setStatus(status, "Fetching video info…", "busy");
    try {
      const info = await postJSON("/api/yt/info", this.body());
      $("#yt-title").textContent = info.title;
      const views = info.view_count != null ? `${info.view_count.toLocaleString()} views` : "";
      $("#yt-meta").textContent = [info.uploader, fmtTime(info.duration), views].filter(Boolean).join("   ·   ");
      const thumb = $("#yt-thumb");
      thumb.hidden = !info.thumbnail;
      if (info.thumbnail) thumb.src = info.thumbnail;
      setStatus(status, "Ready to download.", "ok");
    } catch (e) {
      setStatus(status, e.message, "err");
      toast(e.message, true);
    }
  },

  async start() {
    const btn = $("#yt-run");
    btn.disabled = true;
    btn.lastChild.textContent = "Downloading…";
    $("#yt-progress").hidden = false;
    $("#yt-files").replaceChildren();
    $("#yt-bar").style.width = "0";
    setStatus($("#yt-status"), "Starting download…", "busy");
    try {
      const { job_id } = await postJSON("/api/yt/download", this.body());
      this.remember(job_id);
      this.poll(job_id, true);
    } catch (e) {
      this.finish();
      setStatus($("#yt-status"), e.message, "err");
      toast(e.message, true);
    }
  },

  remember(jobId) {
    try { jobId ? localStorage.setItem("aura-yt-job", jobId) : localStorage.removeItem("aura-yt-job"); } catch {}
  },

  /** After a page reload, reattach to a download that is still running or recently finished. */
  async resume() {
    let jobId;
    try { jobId = localStorage.getItem("aura-yt-job"); } catch {}
    if (!jobId) return;
    const res = await fetch(`/api/yt/jobs/${jobId}`).catch(() => null);
    if (!res || !res.ok) return this.remember(null);
    const job = await res.json();
    if (job.status === "error") return this.remember(null);
    $("#yt-progress").hidden = false;
    if (job.status !== "done") {
      $("#yt-run").disabled = true;
      $("#yt-run").lastChild.textContent = "Downloading…";
    }
    this.poll(jobId, false);
  },

  async poll(jobId, autoSave = true) {
    let job;
    try {
      const res = await fetch(`/api/yt/jobs/${jobId}`);
      if (!res.ok) throw new Error(await errorFrom(res));
      job = await res.json();
    } catch (e) {
      this.finish();
      this.remember(null);
      setStatus($("#yt-status"), e.message, "err");
      return;
    }

    $("#yt-bar").style.width = `${(job.progress * 100).toFixed(1)}%`;
    $("#yt-speed").textContent = job.speed != null ? fmtSize(job.speed) + "/s" : "—";
    $("#yt-eta").textContent = fmtTime(job.eta);
    $("#yt-size").textContent = fmtSize(job.total);
    $("#yt-done").textContent = fmtSize(job.downloaded);
    this.log(job.log);

    if (job.status === "done") {
      this.finish();
      setStatus($("#yt-status"), "Download complete! Click a file to save it.", "ok");
      toast("Download complete!");
      this.showFiles(jobId, job.files);
      // Single file: save it straight away (not again when reattaching after a reload).
      if (autoSave && job.files.length === 1) location.href = `/api/yt/jobs/${jobId}/files/0`;
    } else if (job.status === "error") {
      this.finish();
      this.remember(null);
      setStatus($("#yt-status"), job.error || "Download failed", "err");
      toast(job.error || "Download failed", true);
    } else {
      const label = job.status === "processing" ? "Processing…" : `Downloading… ${(job.progress * 100).toFixed(1)}%`;
      setStatus($("#yt-status"), label, "busy");
      this.pollTimer = setTimeout(() => this.poll(jobId, autoSave), 700);
    }
  },

  showFiles(jobId, files) {
    $("#yt-files").replaceChildren(...files.map((name, i) => {
      const li = document.createElement("li");
      const a = Object.assign(document.createElement("a"), {
        href: `/api/yt/jobs/${jobId}/files/${i}`,
        className: "name",
        textContent: name,
      });
      li.append(a);
      return li;
    }));
  },

  finish() {
    clearTimeout(this.pollTimer);
    const btn = $("#yt-run");
    btn.disabled = false;
    btn.lastChild.textContent = "Download";
  },
};

$("#yt-fetch").addEventListener("click", () => YT.fetchInfo());
YT.resume();
$("#yt-url").addEventListener("keydown", (e) => e.key === "Enter" && YT.fetchInfo());
$("#yt-run").addEventListener("click", () => YT.start());
$("#yt-quality").addEventListener("change", (e) => {
  if (["4K (2160p)", "2K (1440p)"].includes(e.target.value)) {
    $("#yt-container").value = "mkv";
    toast("Container switched to MKV: 4K/2K streams don't fit in MP4");
  }
});

// ── One-click ffmpeg setup ──────────────────────────────────────────────────

const Deps = {
  async install(key) {
    const box = $(`.dep[data-dep="${key}"]`);
    $("[data-install]", box).disabled = true;
    $("[data-progress]", box).hidden = false;
    try {
      const res = await fetch(`/api/deps/${key}/install`, { method: "POST" });
      if (!res.ok) throw new Error(await errorFrom(res));
      this.poll(key);
    } catch (e) {
      this.failed(key, e.message);
    }
  },

  async poll(key) {
    const box = $(`.dep[data-dep="${key}"]`);
    if (!box) return;
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
      box.remove();
      toast(`${box.dataset.label} installed`);
      if (!$(".dep")) $("#deps-callout").remove();
      return;
    }
    if (st.status === "error") return this.failed(key, st.error);
    $("[data-msg]", box).textContent = st.message || "Working…";
    $("[data-progress] > div", box).style.width = `${(st.progress * 100).toFixed(1)}%`;
    setTimeout(() => this.poll(key), 600);
  },

  failed(key, msg) {
    const box = $(`.dep[data-dep="${key}"]`);
    const btn = $("[data-install]", box);
    btn.disabled = false;
    btn.lastChild.textContent = "Try again";
    $("[data-progress]", box).hidden = true;
    $("[data-msg]", box).textContent = `Install failed: ${msg}`;
    toast(`Install failed: ${msg}`, true);
  },
};

$$(".dep").forEach((box) => {
  $("[data-install]", box).addEventListener("click", () => Deps.install(box.dataset.dep));
});
if ($(".dep")) {
  // Resume progress for installs already running (e.g. after a page reload).
  fetch("/api/deps").then((r) => r.json()).then((all) => {
    $$(".dep").forEach((box) => {
      if (all[box.dataset.dep]?.install.status === "running") {
        $("[data-install]", box).disabled = true;
        $("[data-progress]", box).hidden = false;
        Deps.poll(box.dataset.dep);
      }
    });
  }).catch(() => {});
}

showTab(location.hash.slice(1));
