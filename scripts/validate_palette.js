#!/usr/bin/env node
/**
 * Validate a categorical or ordinal palette against a surface, in one mode.
 *
 * Six checks, and the reason each one exists.
 *
 * 1. Lightness separation. Two series that differ only in hue are two series a
 *    reader has to hold in working memory. A lightness step makes them
 *    distinguishable in a thumbnail, in greyscale and in peripheral vision.
 * 2. Chroma separation. Two hues at the same chroma and lightness read as the
 *    same colour under a projector.
 * 3. Contrast against the surface. A 2px line below 3:1 disappears.
 * 4. Normal vision separation, as OKLab delta E. The floor is low on purpose:
 *    this check is about adjacent slots, not about every pair.
 * 5. Colour vision deficiency separation. Simulated for protanopia,
 *    deuteranopia and tritanopia, taking the worst. Roughly one man in twelve
 *    has one of these and a red to green pair is the classic failure.
 * 6. Ordinal monotonicity, for a sequential ramp: lightness has to move one
 *    way, every step, or the ramp does not encode magnitude.
 *
 * Usage:
 *   node scripts/validate_palette.js "#0891B2,#D97706,..." --mode dark \
 *        --surface "#0B0F14" [--pairs adjacent|all] [--ordinal] [--json]
 */

"use strict";

const HARD = {
  lightness: 0.045,
  chroma: 0.02,
  contrast: 3.0,
  // A sequential ramp is filled area, not a 2px line, and its darkest step
  // sits deliberately close to a dark surface so the scale has somewhere to
  // start. Holding a fill to the line floor would fail the ramp for doing the
  // job it was designed for. The fill floor is the point where an area stops
  // being readable as distinct from the background.
  contrastFill: 1.5,
  deltaNormal: 12.0,
  deltaCvd: 8.0,
  ordinalLightnessStep: 0.06,
};

function parseHex(hex) {
  const value = hex.trim().replace(/^#/, "");
  if (!/^[0-9a-fA-F]{6}$/.test(value)) {
    throw new Error(`Not a six digit hex colour: ${hex}`);
  }
  return [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16) / 255);
}

function toLinear(channel) {
  return channel <= 0.04045
    ? channel / 12.92
    : Math.pow((channel + 0.055) / 1.055, 2.4);
}

/** sRGB to OKLab, Bjorn Ottosson's matrices. */
function oklab([r, g, b]) {
  const lr = toLinear(r);
  const lg = toLinear(g);
  const lb = toLinear(b);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return {
    L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  };
}

function oklch(rgb) {
  const { L, a, b } = oklab(rgb);
  return { L, C: Math.hypot(a, b), h: (Math.atan2(b, a) * 180) / Math.PI };
}

function relativeLuminance([r, g, b]) {
  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
}

function contrastRatio(a, b) {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Brettel style dichromat simulation in linear RGB.
 * The matrices are the widely used Viénot, Brettel and Mollon approximations.
 */
const CVD_MATRICES = {
  protanopia: [
    [0.1121, 0.8853, -0.0005],
    [0.1127, 0.8897, -0.0001],
    [0.0045, 0.0, 1.0],
  ],
  deuteranopia: [
    [0.292, 0.7054, -0.0003],
    [0.2934, 0.7089, 0.0],
    [-0.0209, 0.0272, 0.9979],
  ],
  tritanopia: [
    [1.0, 0.1502, -0.1387],
    [0.0, 0.8595, 0.1405],
    [0.0, 0.2645, 0.7355],
  ],
};

function simulate(rgb, kind) {
  const m = CVD_MATRICES[kind];
  const lin = rgb.map(toLinear);
  const out = m.map((row) => row[0] * lin[0] + row[1] * lin[1] + row[2] * lin[2]);
  return out.map((c) => {
    const clamped = Math.min(1, Math.max(0, c));
    return clamped <= 0.0031308
      ? clamped * 12.92
      : 1.055 * Math.pow(clamped, 1 / 2.4) - 0.055;
  });
}

/** OKLab delta E, scaled by 100 so the numbers read like CIE delta E. */
function deltaE(a, b) {
  const x = oklab(a);
  const y = oklab(b);
  return (
    100 * Math.hypot(x.L - y.L, x.a - y.a, x.b - y.b)
  );
}

function worstCvdDelta(a, b) {
  let worst = Infinity;
  let kind = null;
  for (const name of Object.keys(CVD_MATRICES)) {
    const d = deltaE(simulate(a, name), simulate(b, name));
    if (d < worst) {
      worst = d;
      kind = name;
    }
  }
  return { delta: worst, kind };
}

function pairs(list, mode) {
  const out = [];
  if (mode === "all") {
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) out.push([i, j]);
    }
  } else {
    for (let i = 0; i + 1 < list.length; i += 1) out.push([i, i + 1]);
  }
  return out;
}

function rangeDown(from, to) {
  const out = [];
  for (let i = from; i >= to; i -= 1) out.push(i);
  return out;
}

function rangeUp(from, to) {
  const out = [];
  for (let i = from; i <= to; i += 1) out.push(i);
  return out;
}

function parseArgs(argv) {
  const options = {
    mode: "dark",
    surface: "#0B0F14",
    pairs: "adjacent",
    ordinal: false,
    diverging: false,
    json: false,
  };
  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--mode") options.mode = argv[++i];
    else if (arg === "--surface") options.surface = argv[++i];
    else if (arg === "--pairs") options.pairs = argv[++i];
    else if (arg === "--ordinal") options.ordinal = true;
    else if (arg === "--diverging") {
      options.ordinal = true;
      options.diverging = true;
    }
    else if (arg === "--json") options.json = true;
    else positional.push(arg);
  }
  return { options, positional };
}

function main(argv) {
  const { options, positional } = parseArgs(argv);
  if (positional.length === 0) {
    console.error(
      'Usage: node scripts/validate_palette.js "#0891B2,#D97706,..." ' +
        '--mode dark --surface "#0B0F14" [--pairs all] [--ordinal]'
    );
    return 2;
  }

  const hexes = positional
    .join(",")
    .split(/[,\s]+/)
    .filter(Boolean);
  const colours = hexes.map(parseHex);
  const surface = parseHex(options.surface);

  const rows = [];
  let failures = 0;

  // A diverging scale's midpoint means "no deviation", so it is supposed to
  // sit against the surface. Holding it to a fill floor would fail it for
  // being what it is. It is checked for neutrality instead: a midpoint with
  // chroma is a third hue nobody asked for.
  const midpoint = options.diverging ? (colours.length - 1) / 2 : -1;
  if (options.diverging) {
    if (!Number.isInteger(midpoint)) {
      console.error(
        "A diverging scale needs an odd number of steps so the midpoint is a step."
      );
      return 2;
    }
    const { C } = oklch(colours[midpoint]);
    const pass = C < 0.02;
    if (!pass) failures += 1;
    rows.push({
      check: "diverging midpoint is neutral",
      subject: hexes[midpoint],
      value: Number(C.toFixed(4)),
      floor: 0.02,
      pass,
      extra: "chroma, lower is more neutral",
    });
  }

  colours.forEach((rgb, index) => {
    if (index === midpoint) return;
    const { L, C } = oklch(rgb);
    const ratio = contrastRatio(rgb, surface);
    const floor = options.ordinal ? HARD.contrastFill : HARD.contrast;
    const pass = ratio >= floor;
    if (!pass) failures += 1;
    rows.push({
      check: options.ordinal ? "contrast vs surface (fill)" : "contrast vs surface",
      subject: hexes[index],
      value: Number(ratio.toFixed(2)),
      floor,
      pass,
      extra: `L ${L.toFixed(3)} C ${C.toFixed(3)}`,
    });
  });

  // An ordinal ramp is one hue by construction, so the categorical pair checks
  // below do not apply to it: adjacent steps of a sequential scale are supposed
  // to be similar, and failing them for being similar would be the check
  // misunderstanding the encoding. A ramp is judged on monotone lightness.
  const categoricalPairs = options.ordinal ? [] : pairs(colours, options.pairs);

  for (const [i, j] of categoricalPairs) {
    const left = oklch(colours[i]);
    const right = oklch(colours[j]);
    const subject = `${hexes[i]} to ${hexes[j]}`;

    const lightness = Math.abs(left.L - right.L);
    const chroma = Math.abs(left.C - right.C);
    const separated = lightness >= HARD.lightness || chroma >= HARD.chroma;
    if (!separated) failures += 1;
    rows.push({
      check: "lightness or chroma separation",
      subject,
      value: Number(lightness.toFixed(3)),
      floor: HARD.lightness,
      pass: separated,
      extra: `chroma delta ${chroma.toFixed(3)}`,
    });

    const normal = deltaE(colours[i], colours[j]);
    const normalPass = normal >= HARD.deltaNormal;
    if (!normalPass) failures += 1;
    rows.push({
      check: "normal vision delta E",
      subject,
      value: Number(normal.toFixed(1)),
      floor: HARD.deltaNormal,
      pass: normalPass,
      extra: "",
    });

    const cvd = worstCvdDelta(colours[i], colours[j]);
    const cvdPass = cvd.delta >= HARD.deltaCvd;
    if (!cvdPass) failures += 1;
    rows.push({
      check: "worst colour vision deficiency delta E",
      subject,
      value: Number(cvd.delta.toFixed(1)),
      floor: HARD.deltaCvd,
      pass: cvdPass,
      extra: cvd.kind,
    });
  }

  if (options.diverging) {
    const lightnesses = colours.map((c) => oklch(c).L);
    const arms = [
      { name: "cool arm", indices: rangeDown(midpoint, 0) },
      { name: "warm arm", indices: rangeUp(midpoint, colours.length - 1) },
    ];
    for (const arm of arms) {
      for (let k = 0; k + 1 < arm.indices.length; k += 1) {
        const a = arm.indices[k];
        const b = arm.indices[k + 1];
        const step = Math.abs(lightnesses[b] - lightnesses[a]);
        const pass = step >= HARD.ordinalLightnessStep;
        if (!pass) failures += 1;
        rows.push({
          check: `${arm.name} lightness step`,
          subject: `${hexes[a]} to ${hexes[b]}`,
          value: Number(step.toFixed(3)),
          floor: HARD.ordinalLightnessStep,
          pass,
          extra: "measured from the midpoint outwards",
        });
      }
    }
  } else if (options.ordinal) {
    const lightnesses = colours.map((c) => oklch(c).L);
    const ascending = lightnesses[lightnesses.length - 1] > lightnesses[0];
    for (let i = 0; i + 1 < lightnesses.length; i += 1) {
      const step = lightnesses[i + 1] - lightnesses[i];
      const signed = ascending ? step : -step;
      const pass = signed >= HARD.ordinalLightnessStep;
      if (!pass) failures += 1;
      rows.push({
        check: "ordinal lightness step",
        subject: `${hexes[i]} to ${hexes[i + 1]}`,
        value: Number(signed.toFixed(3)),
        floor: HARD.ordinalLightnessStep,
        pass,
        extra: ascending ? "ascending" : "descending",
      });
    }
  }

  if (options.json) {
    console.log(
      JSON.stringify(
        { mode: options.mode, surface: options.surface, failures, rows },
        null,
        2
      )
    );
    return failures === 0 ? 0 : 1;
  }

  console.log(
    `palette: ${hexes.length} colours, mode ${options.mode}, surface ${options.surface}, pairs ${options.pairs}`
  );
  const width = Math.max(...rows.map((r) => r.subject.length), 8);
  for (const row of rows) {
    const mark = row.pass ? "pass" : "FAIL";
    console.log(
      `  ${mark}  ${row.check.padEnd(38)} ${row.subject.padEnd(width)} ` +
        `${String(row.value).padStart(7)} (floor ${row.floor}) ${row.extra}`
    );
  }
  console.log(
    failures === 0
      ? `\nAll ${rows.length} checks pass.`
      : `\n${failures} of ${rows.length} checks FAIL.`
  );
  return failures === 0 ? 0 : 1;
}

process.exit(main(process.argv.slice(2)));
