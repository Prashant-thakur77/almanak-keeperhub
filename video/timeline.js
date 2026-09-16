/* Deterministic: every visual is a pure function of absolute time t.
   The renderer calls __renderAt(t) per frame, so output never depends on
   wall-clock or frame pacing and the encode is perfectly smooth. */

const W = 1920, H = 1080;
const C = { alm: 0xffb454, kh: 0x41e0d0, bad: 0xff6b6b, ink: 0xf2f5fb };

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const ease = (x) => (x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);   // inOutCubic
const outExpo = (x) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x));
const outBack = (x) => 1 + 2.7 * Math.pow(x - 1, 3) + 1.7 * Math.pow(x - 1, 2);
/** 0 -> 1 over [a,b] */
const ramp = (t, a, b, fn = ease) => fn(clamp((t - a) / Math.max(1e-6, b - a)));
/** fade in, hold, fade out */
const win = (t, a, b, fi = .55, fo = .55) =>
  Math.min(ramp(t, a, a + fi), 1 - ramp(t, b - fo, b));

/* ---------------------------------------------------------------- three.js */
const renderer = new THREE.WebGLRenderer({
  canvas: document.getElementById("gl"), antialias: true, alpha: false,
});
renderer.setPixelRatio(1);
renderer.setSize(W, H, false);
renderer.setClearColor(0x07090f, 1);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x07090f, 0.028);
const camera = new THREE.PerspectiveCamera(42, W / H, 0.1, 300);

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 0.9); key.position.set(6, 10, 8); scene.add(key);
const rimA = new THREE.PointLight(C.kh, 2.2, 60); scene.add(rimA);
const rimB = new THREE.PointLight(C.alm, 1.8, 60); scene.add(rimB);

const groups = {};
const mkGroup = (n) => { const g = new THREE.Group(); g.visible = false; scene.add(g); groups[n] = g; return g; };

const glass = (color, opacity = .18) => new THREE.MeshStandardMaterial({
  color, metalness: .15, roughness: .45, transparent: true, opacity,
  emissive: color, emissiveIntensity: .55,
});
const solid = (color, e = .5) => new THREE.MeshStandardMaterial({
  color, metalness: .4, roughness: .3, emissive: color, emissiveIntensity: e,
});

/* --- starfield / drifting particle field, present in most scenes --------- */
const FIELD_N = 420;
const fieldGeo = new THREE.BufferGeometry();
{
  const p = new Float32Array(FIELD_N * 3);
  for (let i = 0; i < FIELD_N; i++) {
    p[i * 3] = (Math.random() - .5) * 90;
    p[i * 3 + 1] = (Math.random() - .5) * 50;
    p[i * 3 + 2] = (Math.random() - .5) * 70 - 10;
  }
  fieldGeo.setAttribute("position", new THREE.BufferAttribute(p, 3));
}
const field = new THREE.Points(fieldGeo, new THREE.PointsMaterial({
  color: 0x9fb2d0, size: .13, transparent: true, opacity: .5, sizeAttenuation: true,
}));
scene.add(field);

/* --- 1/8 TITLE: slow rotating wireframe lattice -------------------------- */
{
  const g = mkGroup("title");
  const ico = new THREE.Mesh(
    new THREE.IcosahedronGeometry(7.5, 1),
    new THREE.MeshBasicMaterial({ color: C.kh, wireframe: true, transparent: true, opacity: .30 })
  );
  const ico2 = new THREE.Mesh(
    new THREE.IcosahedronGeometry(5.0, 0),
    new THREE.MeshBasicMaterial({ color: C.alm, wireframe: true, transparent: true, opacity: .22 })
  );
  g.add(ico, ico2); g.userData = { ico, ico2 };
  g.position.set(13, 0, -6);
}

/* --- 2 PROBLEM: strategy cube shedding calldata bytes into a gap --------- */
{
  const g = mkGroup("problem");
  const cube = new THREE.Mesh(new THREE.BoxGeometry(3.4, 3.4, 3.4), glass(C.alm, .30));
  cube.position.set(0, 4.2, 0);
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(3.4, 3.4, 3.4)),
    new THREE.LineBasicMaterial({ color: C.alm, transparent: true, opacity: .85 })
  );
  edges.position.copy(cube.position);

  const bytes = [];
  const byteGeo = new THREE.BoxGeometry(.34, .34, .34);
  const byteMat = solid(C.alm, .6);
  for (let i = 0; i < 90; i++) {
    const m = new THREE.Mesh(byteGeo, byteMat.clone());
    m.userData = {
      ph: Math.random(), sx: (Math.random() - .5) * 7, sz: (Math.random() - .5) * 5,
      rot: (Math.random() - .5) * 4, sp: .55 + Math.random() * .7,
    };
    bytes.push(m); g.add(m);
  }
  // the gap: a dark disc the bytes fall into
  const gap = new THREE.Mesh(
    new THREE.CircleGeometry(6.2, 64),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: .85 })
  );
  gap.rotation.x = -Math.PI / 2; gap.position.y = -5.6;
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(6.2, 6.55, 64),
    new THREE.MeshBasicMaterial({ color: C.bad, transparent: true, opacity: .55, side: THREE.DoubleSide })
  );
  ring.rotation.x = -Math.PI / 2; ring.position.y = -5.58;

  g.add(cube, edges, gap, ring);
  g.userData = { cube, edges, bytes, ring };
}

/* --- 3 SEAM: Almanak slab -> 3 conduits -> KeeperHub slab ---------------- */
{
  const g = mkGroup("seam");
  const slab = (x, color) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(4.6, 9.2, 1.5), glass(color, .22));
    m.position.set(x, 0, 0);
    const e = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(4.6, 9.2, 1.5)),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: .9 })
    );
    e.position.copy(m.position);
    g.add(m, e); return { m, e };
  };
  const left = slab(-11, C.alm), right = slab(11, C.kh);
  left.m.material.emissiveIntensity = 1.15; right.m.material.emissiveIntensity = .85;

  // three conduits + packets flowing left -> right
  const conduits = [], packets = [];
  const ys = [3.1, 0, -3.1];
  ys.forEach((y, i) => {
    const tube = new THREE.Mesh(
      new THREE.CylinderGeometry(.09, .09, 17.2, 12),
      new THREE.MeshBasicMaterial({ color: 0x5b6b86, transparent: true, opacity: .45 })
    );
    tube.rotation.z = Math.PI / 2; tube.position.set(0, y, 0);
    g.add(tube); conduits.push(tube);
    for (let k = 0; k < 5; k++) {
      const p = new THREE.Mesh(new THREE.SphereGeometry(.22, 16, 16), solid(C.kh, .9));
      p.userData = { lane: i, y, off: k / 5 };
      g.add(p); packets.push(p);
    }
  });
  g.userData = { left, right, conduits, packets };
}

/* --- 4 BUYS / 6 FAIL / 7 PROOF: soft 3D backdrop ------------------------- */
{
  const g = mkGroup("grid");
  const grid = new THREE.GridHelper(120, 60, 0x1b2740, 0x121a2b);
  grid.position.y = -9; g.add(grid);
  const glow = new THREE.Mesh(
    new THREE.SphereGeometry(26, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0x0d2a3a, transparent: true, opacity: .30, side: THREE.BackSide })
  );
  g.add(glow); g.userData = { grid };
}

/* --- 7 PROOF: 3D bars --------------------------------------------------- */
{
  const g = mkGroup("bars");
  const bars = [];
  const vals = [1, 1, 1, .82];
  for (let i = 0; i < 4; i++) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(1.5, 1, 1.5), solid(i === 3 ? C.alm : C.kh, .55));
    m.position.set(-7.5 + i * 5, -8, -4);
    m.userData = { v: vals[i] };
    g.add(m); bars.push(m);
  }
  g.userData = { bars };
}

/* ------------------------------------------------------------------- DOM */
const $ = (id) => document.getElementById(id);
const layers = {
  title: $("L_title"), problem: $("L_problem"), seam: $("L_seam"), buys: $("L_buys"),
  demo: $("L_demo"), phone: $("L_phone"), fail: $("L_fail"), proof: $("L_proof"), close: $("L_close"),
};
const cap = $("cap"), capScrim = $("capscrim");

$("buys_cards").innerHTML = [
  ["Enclave-held key", "Turnkey wallet. The private key never enters the strategy process."],
  ["Spend caps", "0.02 ETH/day and a per-transaction stablecoin ceiling, enforced before signing."],
  ["Simulation", "Every bundle dry-run through KeeperHub before a single byte is broadcast."],
  ["Sponsored gas", "KeeperHub pays on Base. The org wallet holds no native balance."],
  ["Audit log", "Every execution is a row with a verified receipt, not a log line."],
  ["Idempotency", "A key bound to the intent, so a retry can never double-spend."],
].map(([t, d], i) => {
  const col = i % 3, row = (i / 3) | 0;
  return `<div class="card" id="bc${i}" style="left:${140 + col * 552}px;top:${400 + row * 216}px;width:504px;opacity:0">
            <div class="t">${t}</div><div class="d">${d}</div></div>`;
}).join("");

$("fail_cards").innerHTML = [
  ["Unknown selector", "Refused before signing", "bad"],
  ["Would revert", "Caught by the dry run", "bad"],
  ["Over the cap", "Refused at preflight", "bad"],
  ["Duplicate", "Blocked by idempotency key", "good"],
  ["RPC outage", "Settles from KeeperHub's receipt", "good"],
  ["Killed mid-bundle", "Resumes from the receipts log", "good"],
].map(([t, d, k], i) => {
  const col = i % 3, row = (i / 3) | 0;
  return `<div class="card" id="fc${i}" style="left:${140 + col * 552}px;top:${360 + row * 226}px;width:504px;opacity:0">
            <div class="t ${k}">${t}</div><div class="d">${d}</div></div>`;
}).join("");

$("d_txlist").innerHTML = [
  ["approve", "0x29dd40a6db7016bf0b…", "az13hw7qn9y9dhs52s4rg"],
  ["deposit", "0x70b453be43f4b8c4d4…", "au5z8vtzv8s9xm811z93j"],
  ["redeem",  "0x91777e39d4fc1748f6…", "7rshlqcgwoxkia3iz052b"],
].map(([fn, tx, ex], i) =>
  `<div class="txrow" id="tx${i}" style="opacity:0;display:flex;align-items:center;gap:26px;
       padding:22px 30px;margin-bottom:16px;border-radius:14px;
       background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09)">
     <div style="font-size:30px;font-weight:700;width:180px" class="amber">${fn}</div>
     <div class="mono" style="font-size:27px;flex:1">${tx}</div>
     <div class="mono" style="font-size:21px;color:#8c97ad;width:330px">exec ${ex}</div>
     <div style="font-size:24px;font-weight:700" class="good">verified &middot; sponsored</div>
   </div>`).join("");

$("d_surflist").innerHTML = [
  ["Local console", "Live proof page: pipeline, executions, dry runs, failure verdicts"],
  ["KeeperHub dashboard", "Every execution is a row with a verified receipt"],
  ["Telegram bot", "/status, /verify, /simulate, /tick from a phone"],
].map(([t, d], i) =>
  `<div class="card" id="sf${i}" style="left:${140 + i * 552}px;top:200px;width:504px;opacity:0">
     <div class="t teal">${t}</div><div class="d">${d}</div></div>`).join("");

$("proof_stats").innerHTML = [
  ["20/20", "unsafe calls refused"],
  ["10/10", "dry runs caught"],
  ["5/5", "transactions landed"],
  ["6.9s", "median time to receipt"],
].map(([n, l], i) =>
  `<div class="stat" id="ps${i}" style="left:${140 + i * 430}px;top:${430}px;opacity:0">
     <div class="n" id="psn${i}">${n}</div><div class="l">${l}</div></div>`
).join("");

/* --------------------------------------------------------------- timeline */
/* Scene boundaries come from the real narration durations, so picture is cut
   to the voice rather than the voice squeezed into a guessed picture. */
const SCENE_OF = {
  s1a: "title", s2a: "problem", s2b: "problem", s3a: "seam", s3b: "seam",
  s3c: "buys", s4a: "demo", s4b: "demo", s4c: "demo", s4d: "demo",
  p1: "phone", p2: "phone", p3: "phone", p4: "phone", p5: "phone", p6: "phone", p7: "phone", p8: "phone", p9: "phone", p10: "phone",
  s5a: "fail", s5b: "fail", s5c: "fail",
  s6a: "proof", s6b: "proof", s7a: "close",
};
const LEAD = 0.45, TAIL = 0.75;   // picture starts before and holds after the voice

let T = { scenes: {}, lines: [], total: 30 };
function buildTimeline(timings) {
  const byScene = {};
  timings.lines.forEach((l) => {
    const s = SCENE_OF[l.id] || "title";
    (byScene[s] ||= []).push(l);
  });
  const scenes = {};
  Object.entries(byScene).forEach(([name, ls]) => {
    scenes[name] = {
      a: Math.max(0, ls[0].start - LEAD),
      b: ls[ls.length - 1].start + ls[ls.length - 1].dur + TAIL,
      lines: ls,
    };
  });
  T = { scenes, lines: timings.lines, total: timings.total + TAIL + 0.5 };
  window.__duration = T.total;
}

/* ------------------------------------------------------------ per-frame */
function setLayer(name, v) {
  const el = layers[name]; if (!el) return;
  el.style.opacity = v.toFixed(4);
  // a little parallax lift as each layer arrives
  el.style.transform = `translateY(${((1 - v) * 26).toFixed(2)}px)`;
}

function sceneWin(name, t) {
  const s = T.scenes[name];
  if (!s) return { v: 0, u: 0 };
  return { v: win(t, s.a, s.b, .5, .5), u: clamp((t - s.a) / Math.max(.001, s.b - s.a)) };
}

window.__renderAt = function (t) {
  // captions
  const cur = T.lines.find((l) => t >= l.start - .12 && t <= l.start + l.dur + .28);
  if (cur) { cap.textContent = cur.text; cap.style.opacity = win(t, cur.start - .12, cur.start + cur.dur + .28, .22, .3).toFixed(3); }
  else cap.style.opacity = "0";
  capScrim.style.opacity = (Number(cap.style.opacity) * .95).toFixed(3);

  Object.keys(layers).forEach((n) => setLayer(n, sceneWin(n, t).v));
  Object.values(groups).forEach((g) => (g.visible = false));

  const title = sceneWin("title", t), problem = sceneWin("problem", t),
        seam = sceneWin("seam", t), buys = sceneWin("buys", t),
        demo = sceneWin("demo", t), phone = sceneWin("phone", t), fail = sceneWin("fail", t),
        proof = sceneWin("proof", t), close = sceneWin("close", t);

  field.material.opacity = .18 + .32 * Math.max(title.v, close.v);
  field.rotation.y = t * 0.012;
  rimA.position.set(Math.sin(t * .35) * 16, 6, 12);
  rimB.position.set(-14, Math.cos(t * .3) * 6, 10);

  // default camera
  let cam = { x: 0, y: 0, z: 34, lx: 0, ly: 0 };

  if (title.v > .01 || close.v > .01) {
    const g = groups.title; g.visible = true;
    const k = Math.max(title.v, close.v);
    g.userData.ico.rotation.set(t * .10, t * .14, 0);
    g.userData.ico2.rotation.set(-t * .17, t * .11, 0);
    g.userData.ico.material.opacity = .30 * k;
    g.userData.ico2.material.opacity = .22 * k;
    const u = close.v > .01 ? close.u : title.u;
    cam = { x: 0, y: 0, z: 30 - 2.5 * ease(u), lx: 4, ly: 0 };
  }

  if (problem.v > .01) {
    const g = groups.problem; g.visible = true;
    const { cube, edges, bytes, ring } = g.userData;
    cube.rotation.set(t * .22, t * .30, 0); edges.rotation.copy(cube.rotation);
    cube.material.opacity = .30 * problem.v;
    edges.material.opacity = .85 * problem.v;
    ring.material.opacity = .55 * problem.v * (.6 + .4 * Math.sin(t * 2.4));
    const shed = ramp(t, T.scenes.problem.a + 1.6, T.scenes.problem.b - .6, outExpo);
    bytes.forEach((m, i) => {
      const d = m.userData;
      const ph = (d.ph + t * d.sp * .16) % 1;
      const y = 3.4 - ph * 9.6;
      m.position.set(d.sx * (.35 + ph), y, d.sz * (.35 + ph));
      m.rotation.set(ph * d.rot * 6, ph * d.rot * 4, 0);
      const alive = i / bytes.length < shed;
      m.material.opacity = alive ? problem.v * (1 - ph) * .95 : 0;
      m.material.transparent = true;
      m.visible = alive;
    });
    cam = { x: 0, y: -.6 + 1.2 * ease(problem.u), z: 26, lx: 0, ly: -.8 };
  }

  if (seam.v > .01) {
    const g = groups.seam; g.visible = true;
    const { left, right, conduits, packets } = g.userData;
    const open = ramp(t, T.scenes.seam.a + .6, T.scenes.seam.a + 2.4, outBack);
    left.m.position.x = left.e.position.x = -11 - 2 * (1 - open);
    right.m.position.x = right.e.position.x = 11 + 2 * (1 - open);
    [left, right].forEach((s) => { s.m.material.opacity = .40 * seam.v; s.e.material.opacity = .95 * seam.v; });
    conduits.forEach((c, i) => {
      const o = ramp(t, T.scenes.seam.a + 1.1 + i * .22, T.scenes.seam.a + 2.0 + i * .22);
      c.material.opacity = .45 * seam.v * o;
      const lab = $("cl" + i);
      if (lab) lab.style.opacity = (win(t, T.scenes.seam.a + 1.3 + i * .22, T.scenes.seam.b, .45, .45)).toFixed(3);
    });
    const flow = ramp(t, T.scenes.seam.a + 2.0, T.scenes.seam.a + 3.0);
    packets.forEach((p) => {
      const d = p.userData;
      const u = (d.off + t * .22 + d.lane * .07) % 1;
      p.position.set(-8.7 + u * 17.4, d.y, 0);
      p.material.opacity = seam.v * flow * (u < .06 || u > .94 ? 0 : 1);
      p.material.transparent = true;
    });
    cam = { x: 0, y: 0, z: 30, lx: 0, ly: 0 };
  }

  if (demo.v > .01) {
    const g = groups.grid; g.visible = true;
    g.userData.grid.material.opacity = .28; g.userData.grid.material.transparent = true;
    cam = { x: 0, y: 1.0, z: 32, lx: 0, ly: .6 };

    const at = (id) => T.lines.find((l) => l.id === id);
    const a = at("s4a"), b = at("s4b"), c = at("s4c"), d2 = at("s4d");
    const end = T.scenes.demo.b;

    $("d_title").style.opacity = (b ? win(t, T.scenes.demo.a, b.start - .1, .5, .45) : demo.v).toFixed(3);

    if (b) {
      // the real console screenshot, slow push-in
      const shot = $("d_shot"), img = $("d_shot_img");
      const v = win(t, b.start - .35, (c ? c.start - .15 : end), .5, .45);
      const k = ramp(t, b.start - .35, (c ? c.start : end), (x) => x);
      shot.style.opacity = v.toFixed(3);
      // slow pan down the real console: the pipeline strip into the executions
      img.style.transform = `translateY(${(-90 - 470 * k).toFixed(1)}px) scale(${(1.0 + .03 * k).toFixed(4)})`;
    }
    if (c) {
      const v = win(t, c.start - .3, (d2 ? d2.start - .15 : end), .45, .4);
      $("d_txs").style.opacity = v.toFixed(3);
      for (let i = 0; i < 3; i++) {
        const el = $("tx" + i); if (!el) continue;
        const rv = win(t, c.start + .15 + i * .5, (d2 ? d2.start : end), .35, .35);
        el.style.opacity = rv.toFixed(3);
        el.style.transform = `translateX(${((1 - rv) * 34).toFixed(1)}px)`;
      }
    }
    if (d2) {
      const v = win(t, d2.start - .3, end, .45, .45);
      $("d_surfaces").style.opacity = v.toFixed(3);
      for (let i = 0; i < 3; i++) {
        const el = $("sf" + i); if (!el) continue;
        const sv = win(t, d2.start + .1 + i * .3, end, .4, .4);
        el.style.opacity = sv.toFixed(3);
        el.style.transform = `translateY(${((1 - sv) * 26).toFixed(1)}px)`;
      }
    }
  }

  if (phone.v > .01) {
    const g = groups.grid; g.visible = true;
    g.userData.grid.material.opacity = .22; g.userData.grid.material.transparent = true;
    cam = { x: -2, y: 1.2, z: 31, lx: -1, ly: .8 };
    // the step list lights up as the narration reaches each step
    const steps = ["status", "executions", "verify", "simulate", "real tick", "alerts", "stale exit", "duplicate", "guarded exit"];
    const ids = ["p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10"];
    const box = $("ph_steps");
    if (!box.childElementCount) box.innerHTML = steps.map((x, i) => `<div class="mono" id="phs${i}" style="width:300px;font-size:26px;line-height:1.6;color:var(--dim);opacity:.45">/${x}</div>`).join("");
    ids.forEach((id, i) => {
      const l = T.lines.find((q) => q.id === id); const el = $("phs" + i); if (!el) return;
      if (!l) { el.style.display = "none"; return; }
      const on = win(t, l.start - .2, T.scenes.phone.b, .35, .6);
      el.style.opacity = (.45 + .55 * on).toFixed(3);
      el.style.color = on > .5 ? "var(--kh)" : "var(--dim)";
    });
  }

  if (buys.v > .01 || fail.v > .01 || proof.v > .01) {
    const g = groups.grid; g.visible = true;
    g.userData.grid.material.opacity = .5; g.userData.grid.material.transparent = true;
    cam = { x: 0, y: 1.4, z: 30, lx: 0, ly: 1 };
  }

  // staggered card reveals
  const stagger = (sceneName, prefix, n, delay = .17, from = 1.0) => {
    const s = T.scenes[sceneName]; if (!s) return;
    for (let i = 0; i < n; i++) {
      const el = $(prefix + i); if (!el) continue;
      const a = s.a + from + i * delay;
      const v = win(t, a, s.b, .42, .45);
      el.style.opacity = v.toFixed(3);
      el.style.transform = `translateY(${((1 - v) * 30).toFixed(2)}px) scale(${(.985 + .015 * v).toFixed(4)})`;
    }
  };
  if (buys.v > .01) stagger("buys", "bc", 6);
  if (fail.v > .01) stagger("fail", "fc", 6);

  if (proof.v > .01) {
    stagger("proof", "ps", 4, .22, .8);
    const g = groups.bars; g.visible = true;
    g.userData.bars.forEach((m, i) => {
      const gr = ramp(t, T.scenes.proof.a + 1.0 + i * .2, T.scenes.proof.a + 2.4 + i * .2, outExpo);
      const h = Math.max(.001, m.userData.v * 7 * gr);
      m.scale.y = h; m.position.y = -8 + h / 2;
      m.material.opacity = proof.v; m.material.transparent = true;
    });
    const chips = $("proof_chips");
    chips.style.opacity = win(t, T.scenes.proof.a + 4.6, T.scenes.proof.b, .5, .4).toFixed(3);
  }

  if (seam.v > .01) {
    const note = $("seam_note");
    note.style.opacity = win(t, T.scenes.seam.a + 4.2, T.scenes.seam.b, .5, .4).toFixed(3);
  }

  if (problem.v > .01) {
    ["p1", "p2", "p3", "p4"].forEach((id, i) => {
      const el = $(id);
      el.style.opacity = win(t, T.scenes.problem.a + 2.6 + i * .55, T.scenes.problem.b, .35, .4).toFixed(3);
    });
  }

  camera.position.set(cam.x, cam.y, cam.z);
  camera.lookAt(cam.lx, cam.ly, 0);
  renderer.render(scene, camera);
};

window.__ready = (async () => {
  // injected by the renderer; fetch() is blocked on file:// URLs
  const data = window.__TIMINGS || await (await fetch("timings.json")).json();
  buildTimeline(data);
  if (document.fonts && document.fonts.ready) await document.fonts.ready;
  window.__renderAt(0);
  return true;
})();
