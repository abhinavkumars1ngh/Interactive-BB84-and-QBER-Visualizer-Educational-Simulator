// BB84 + QBER visualizer. Pure client-side; no dependencies.
"use strict";

const THRESHOLD = 0.11;
const $ = id => document.getElementById(id);

// ---------- seeded PRNG (mulberry32) ----------
function makeRng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- BB84 simulation ----------
// basis: 0 = rectilinear (+), 1 = diagonal (x)
function simulate(n, pEve, pNoise, sampleFrac, rng) {
  const q = [];
  for (let i = 0; i < n; i++) {
    const aBit = rng() < 0.5 ? 1 : 0;
    const aBasis = rng() < 0.5 ? 1 : 0;
    // Alice's state
    let sBasis = aBasis, sBit = aBit;

    // Eve: intercept-resend
    const eve = rng() < pEve;
    let eBasis = null, eBit = null;
    if (eve) {
      eBasis = rng() < 0.5 ? 1 : 0;
      eBit = eBasis === sBasis ? sBit : (rng() < 0.5 ? 1 : 0); // wrong basis -> random result
      sBasis = eBasis; sBit = eBit;                             // resends what she measured
    }

    // Channel noise: bit flip
    const flip = rng() < pNoise;
    if (flip) sBit ^= 1;

    // Bob measures
    const bBasis = rng() < 0.5 ? 1 : 0;
    const bBit = bBasis === sBasis ? sBit : (rng() < 0.5 ? 1 : 0);

    q.push({ aBit, aBasis, eve, eBasis, eBit, flip, bBasis, bBit,
             match: aBasis === bBasis, error: aBasis === bBasis && aBit !== bBit,
             sampled: false });
  }
  // Sifting + QBER sampling
  const sifted = q.filter(x => x.match);
  const k = Math.max(sifted.length ? 1 : 0, Math.round(sifted.length * sampleFrac));
  // random subset via shuffle of indices
  const idx = sifted.map((_, i) => i);
  for (let i = idx.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [idx[i], idx[j]] = [idx[j], idx[i]];
  }
  idx.slice(0, k).forEach(i => sifted[i].sampled = true);

  const sample = sifted.filter(x => x.sampled);
  const sampleErr = sample.filter(x => x.error).length;
  const totalErr = sifted.filter(x => x.error).length;
  return {
    q, nSifted: sifted.length, nSample: sample.length, sampleErr, totalErr,
    qberSample: sample.length ? sampleErr / sample.length : 0,
    qberTrue: sifted.length ? totalErr / sifted.length : 0,
    keyLen: sifted.length - sample.length,
  };
}

// ---------- stages ----------
const STAGES = [
  ["Alice picks random bits & bases",
   "Alice generates a random bit string and, for each bit, randomly chooses the + (rectilinear) or × (diagonal) basis."],
  ["Alice encodes & sends qubits",
   "Each bit is encoded as a photon polarization in the chosen basis. Bits 0/1 in + → ↑/→; in × → ↗/↖."],
  ["Eve intercepts (optional)",
   "Eve measures some photons in a random basis and resends what she saw. Right basis (50%): no disturbance. Wrong basis: result is random, so Bob later errs 50% of the time. Red-tinted columns were intercepted."],
  ["Channel noise",
   "Even without Eve, real channels flip some bits (detector dark counts, misalignment). Orange outlines show flipped photons."],
  ["Bob measures in random bases",
   "Bob does not know Alice's bases. He picks his own at random. If his basis ≠ the photon's, his result is random."],
  ["Sifting (public basis comparison)",
   "Alice and Bob announce bases (not bits) over a public channel and keep only positions where they match (~50%). Others are discarded."],
  ["QBER estimation",
   "They publicly compare a random sample of the sifted bits. QBER = mismatches ÷ sample size. Sampled bits are sacrificed (shown with a border); the rest form the raw key."],
  ["Decision",
   "If QBER exceeds the threshold (11%), eavesdropping can't be ruled out: abort. Otherwise proceed to error correction and privacy amplification."],
];
let stage = 0, sim = null;

function newRun() {
  const seed = parseInt($("seed").value, 10) || 0;
  sim = simulate(+$("n").value, $("eve").value / 100, $("noise").value / 100, $("sample").value / 100, makeRng(seed));
  stage = 0;
  render();
}

// ---------- rendering ----------
const SYM = [["↑", "→"], ["↗", "↖"]];

function row(label, cls, cells, sep) {
  return `<tr class="${sep ? "sep" : ""}"><th>${label}</th>${cells.map(c => `<td class="${c[1] || ""}">${c[0]}</td>`).join("")}</tr>`;
}

function renderTable() {
  const q = sim.q, R = [];
  const bas = b => [b ? "×" : "+", b ? "x" : "z"];
  R.push(row("Alice bit", "", q.map(x => [x.aBit])));
  R.push(row("Alice basis", "", q.map(x => bas(x.aBasis))));
  if (stage >= 1) R.push(row("Photon sent", "", q.map(x => [SYM[x.aBasis][x.aBit]]), true));
  if (stage >= 2) {
    R.push(row("Eve basis", "", q.map(x => x.eve ? bas(x.eBasis) : ["–", "drop"]), true));
    R.push(row("Eve result", "", q.map(x => x.eve ? [x.eBit, "eveyes"] : ["–", "drop"])));
  }
  if (stage >= 3) R.push(row("Channel flip?", "", q.map(x => x.flip ? ["✱", "flip"] : [""])));
  if (stage >= 4) {
    R.push(row("Bob basis", "", q.map(x => bas(x.bBasis)), true));
    R.push(row("Bob result", "", q.map(x => [x.bBit, stage >= 5 && !x.match ? "drop" : ""])));
  }
  if (stage >= 5) R.push(row("Bases match?", "", q.map(x => x.match ? ["✓", "ok"] : ["✗", "drop"]), true));
  if (stage >= 6) {
    R.push(row("Sample compared", "", q.map(x => x.match ? (x.sampled ? [x.error ? "≠" : "=", x.error ? "err" : "ok"] : ["·"]) : ["", "drop"])));
    R.push(row("Raw key", "", q.map(x => x.match && !x.sampled ? [x.aBit, "ok"] : ["", "drop"])));
  }
  $("table").innerHTML = R.join("");
}

function renderStats() {
  const s = sim, S = [];
  S.push(["Qubits sent", s.q.length]);
  if (stage >= 2) S.push(["Intercepted by Eve", s.q.filter(x => x.eve).length]);
  if (stage >= 3) S.push(["Channel flips", s.q.filter(x => x.flip).length]);
  if (stage >= 5) S.push(["Sifted bits (bases match)", `${s.nSifted} (${(100 * s.nSifted / s.q.length).toFixed(0)}%)`]);
  if (stage >= 6) {
    S.push(["Bits sampled", s.nSample]);
    S.push(["Errors in sample", s.sampleErr]);
    S.push(["Estimated QBER", (100 * s.qberSample).toFixed(1) + "%"]);
    S.push(["True QBER (all sifted)", (100 * s.qberTrue).toFixed(1) + "%"]);
    S.push(["Raw key length", s.keyLen]);
  }
  $("stats").innerHTML = S.map(r => `<div><span>${r[0]}</span><span>${r[1]}</span></div>`).join("");
  const v = $("verdict");
  if (stage >= 7) {
    const bad = s.qberSample > THRESHOLD;
    v.className = bad ? "abort" : "safe";
    v.textContent = bad
      ? `QBER ${(100 * s.qberSample).toFixed(1)}% > 11%: ABORT. Eavesdropper or excessive noise detected.`
      : `QBER ${(100 * s.qberSample).toFixed(1)}% ≤ 11%: channel accepted. Continue with error correction & privacy amplification.`;
  } else { v.className = ""; v.textContent = ""; }
}

function render() {
  $("stages").innerHTML = STAGES.map((s, i) =>
    `<li data-i="${i}" class="${i < stage ? "done" : ""} ${i === stage ? "current" : ""}">${i + 1}. ${s[0]}</li>`).join("");
  $("stages").querySelectorAll("li").forEach(li => li.onclick = () => { stage = +li.dataset.i; render(); });
  $("explain").textContent = STAGES[stage][1];
  renderTable(); renderStats(); drawChart();
}

// ---------- chart ----------
function drawChart() {
  const c = $("chart"), g = c.getContext("2d"), W = c.width, H = c.height;
  const L = 50, B = 36, T = 14, Rr = 14, pw = W - L - Rr, ph = H - T - B;
  const noise = $("noise").value / 100;
  const maxY = 0.4;
  const X = p => L + p * pw, Y = v => T + ph * (1 - Math.min(v, maxY) / maxY);
  g.clearRect(0, 0, W, H);
  g.font = "11px system-ui"; g.fillStyle = "#8c98b3"; g.strokeStyle = "#2a3550";
  for (let v = 0; v <= maxY + 1e-9; v += 0.1) {
    g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(W - Rr, Y(v)); g.stroke();
    g.fillText((v * 100).toFixed(0) + "%", 8, Y(v) + 4);
  }
  for (let p = 0; p <= 1.001; p += 0.25) g.fillText((p * 100).toFixed(0) + "%", X(p) - 10, H - 18);
  g.fillText("Eve intercept probability", W / 2 - 60, H - 4);
  // threshold
  g.strokeStyle = "#ff5c5c"; g.setLineDash([6, 4]);
  g.beginPath(); g.moveTo(L, Y(THRESHOLD)); g.lineTo(W - Rr, Y(THRESHOLD)); g.stroke(); g.setLineDash([]);
  // theory: error = noise*(1-0.25p..) approx; exact for combined independent flips:
  const theory = p => { const e = 0.25 * p; return e * (1 - noise) + (1 - e) * noise; };
  g.strokeStyle = "#4da3ff"; g.lineWidth = 2; g.beginPath();
  for (let i = 0; i <= 100; i++) { const p = i / 100; i ? g.lineTo(X(p), Y(theory(p))) : g.moveTo(X(p), Y(theory(p))); }
  g.stroke(); g.lineWidth = 1;
  // Monte Carlo dots (cached per noise value)
  const rng = makeRng(7);
  g.fillStyle = "#2ecc71";
  for (let p = 0; p <= 1.001; p += 0.1) {
    let sum = 0;
    for (let t = 0; t < 20; t++) sum += simulate(400, p, noise, 1, rng).qberTrue;
    g.beginPath(); g.arc(X(p), Y(sum / 20), 3.5, 0, 7); g.fill();
  }
  // current run
  if (sim && stage >= 6) {
    g.fillStyle = "#ffa94d";
    g.beginPath(); g.arc(X($("eve").value / 100), Y(sim.qberTrue), 6, 0, 7); g.fill();
  }
}

// ---------- wiring ----------
[["n", "vN", ""], ["eve", "vEve", "%"], ["noise", "vNoise", "%"], ["sample", "vSample", "%"]].forEach(([id, out, suf]) => {
  const upd = () => $(out).textContent = $(id).value + suf;
  $(id).addEventListener("input", () => { upd(); newRun(); });
  upd();
});
$("seed").addEventListener("change", newRun);
$("new").onclick = () => { $("seed").value = Math.floor(Math.random() * 1e6); newRun(); };
$("next").onclick = () => { if (stage < STAGES.length - 1) { stage++; render(); } };
$("prev").onclick = () => { if (stage > 0) { stage--; render(); } };
$("all").onclick = () => { stage = STAGES.length - 1; render(); };
newRun();
