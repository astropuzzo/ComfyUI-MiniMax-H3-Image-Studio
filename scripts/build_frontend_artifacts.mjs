#!/usr/bin/env node
import { createRequire } from "node:module";
import { execFile } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const require = createRequire(import.meta.url);
const execFileAsync = promisify(execFile);
const repoDir = path.resolve(process.env.REPO_DIR ?? path.join(import.meta.dirname, ".."));
const comfyUrl = process.env.COMFY_URL ?? "http://127.0.0.1:8191";
const chromiumPath = process.env.CHROMIUM_PATH;
const playwrightModule = process.env.PLAYWRIGHT_MODULE ?? "playwright";
const viewport = { width: 3200, height: 1800 };

const projectMetadata = await readFile(path.join(repoDir, "pyproject.toml"), "utf8");
const versionMatch = projectMetadata.match(/^version\s*=\s*"([^"]+)"/m);
if (!versionMatch) throw new Error("Could not read the project version from pyproject.toml.");
const release = `v${versionMatch[1]}`;

if (!chromiumPath) throw new Error("Set CHROMIUM_PATH to a Chromium/Chrome executable.");
const { chromium } = require(playwrightModule);

const workflowSpecs = [
  { slug: "H3_T2I", api: "H3_T2I_API.json" },
  { slug: "H3_T2I_SINGLE", api: "H3_T2I_SINGLE_API.json" },
  { slug: "H3_I2I", api: "H3_I2I_API.json" },
  { slug: "H3_I2I_SINGLE", api: "H3_I2I_SINGLE_API.json" },
  { slug: "H3_REFERENCE_EDIT", api: "H3_REFERENCE_EDIT_API.json" },
  { slug: "H3_REFERENCE_SINGLE", api: "H3_REFERENCE_SINGLE_API.json" },
  { slug: "H3_I2I_TURBO", api: "H3_I2I_TURBO_API.json" },
  { slug: "H3_FAST_REFINER", api: "H3_FAST_REFINER_API.json" },
];

const basePositions = {
  "1": [0, 430],
  "15": [470, 430],
  "16": [470, 780],
  "8": [940, 430],
  "7": [1410, 430],
  "10": [1880, 430],
  "11": [2350, 430],
  "12": [2820, 430],
  "13": [3290, 430],
  "2": [0, 820],
  "3": [0, 1160],
  "0": [470, 1120],
  "14": [470, 1480],
  "4": [940, 1040],
  "5": [1410, 900],
  "6": [1880, 980],
};

const refinerPositions = {
  "0": [0, 430],
  "4": [380, 430],
  "5": [760, 430],
  "1": [0, 1020],
  "2": [380, 1020],
  "3": [760, 1020],
  "6": [1140, 720],
  "7": [1520, 1120],
  "8": [1140, 430],
  "9": [1520, 720],
  "10": [1520, 980],
  "11": [1900, 790],
  "12": [1140, 1370],
  "13": [1520, 1370],
  "14": [1900, 1370],
  "15": [2280, 1370],
  "16": [2280, 790],
  "17": [2660, 790],
  "18": [3040, 790],
  "19": [3420, 790],
};

const browser = await chromium.launch({
  executablePath: chromiumPath,
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-webgl"],
  env: {
    ...process.env,
    XDG_CACHE_HOME: process.env.XDG_CACHE_HOME ?? "/tmp/minimax-browser-cache",
    FONTCONFIG_PATH: process.env.FONTCONFIG_PATH ?? "/tmp/fonts",
  },
});

const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error.stack ?? error)));
page.on("console", (message) => {
  const text = message.text();
  if (
    message.type() === "error"
    && !text.includes("Failed to load resource")
    && !text.includes("graph accessed before initialization")
  ) {
    pageErrors.push(`console: ${text}`);
  }
});

await page.goto(comfyUrl, { waitUntil: "domcontentloaded", timeout: 120_000 });
await page.waitForFunction(() => window.comfyAPI?.app?.app?.isGraphReady, null, { timeout: 120_000 });
await page.waitForFunction(
  () => window.comfyAPI?.app?.app?.extensions?.some((extension) => extension.name === "MiniMaxH3.ImageStudio.Theme"),
  null,
  { timeout: 120_000 },
);
// isGraphReady becomes true before every backend definition has completed its
// frontend registration. Give the current Vue/LiteGraph bridge one paint turn.
await page.waitForTimeout(2_000);
await page.evaluate(() => {
  const canvas = document.querySelector("#graph-canvas");
  const graphContainer = canvas?.parentElement;
  const hideOverlayAt = (x, y) => {
    let overlay = document.elementsFromPoint(x, y).find((element) => element !== canvas && !element.contains(canvas));
    if (!overlay) return;
    while (
      overlay.parentElement
      && overlay.parentElement !== graphContainer
      && !overlay.parentElement.contains(canvas)
    ) {
      overlay = overlay.parentElement;
    }
    overlay.style.visibility = "hidden";
  };
  for (const [x, y] of [
    [10, 120],
    [90, 48],
    [1600, 10],
    [3180, 55],
    [3180, 1780],
    [3060, 1620],
  ]) {
    hideOverlayAt(x, y);
  }
  for (const toast of document.querySelectorAll(".p-toast, .p-dialog-mask")) toast.style.visibility = "hidden";
});

for (const spec of workflowSpecs) {
  const apiPath = path.join(repoDir, "examples", "api", spec.api);
  const api = JSON.parse(await readFile(apiPath, "utf8"));

  const positions = spec.slug === "H3_FAST_REFINER" ? refinerPositions : basePositions;
  const workflow = await page.evaluate(async ({ prompt, positions, slug, release }) => {
    const { app } = await import("/scripts/app.js");
    app.loadApiJson(prompt, slug);
    await new Promise((resolve) => setTimeout(resolve, 300));

    for (const [id, pos] of Object.entries(positions)) {
      const node = app.rootGraph.getNodeById(Number(id));
      if (!node) continue;
      node.pos = [...pos];
      node.setSize([Math.max(node.size?.[0] ?? 0, 390), node.size?.[1] ?? 0]);
    }

    const nodes = app.rootGraph._nodes ?? [];
    const minX = Math.min(...nodes.map((node) => node.pos[0]));
    const minY = Math.min(...nodes.map((node) => node.pos[1]));
    const maxX = Math.max(...nodes.map((node) => node.pos[0] + node.size[0]));
    const maxY = Math.max(...nodes.map((node) => node.pos[1] + node.size[1]));
    const graphWidth = maxX - minX;
    const graphHeight = maxY - minY;
    const margin = 55;
    const scale = Math.min(0.84, (3200 - margin * 2) / graphWidth, (1800 - margin * 2) / graphHeight);

    app.canvas.ds.scale = scale;
    app.canvas.ds.offset = [margin / scale - minX, margin / scale - minY];
    app.canvas.ds.computeVisibleArea?.(app.canvas.viewport);
    app.canvas.setDirty(true, true);
    app.canvas.draw(true, true);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

    const serialized = app.rootGraph.serialize();
    serialized.extra ??= {};
    serialized.extra.ds = { scale, offset: [...app.canvas.ds.offset] };
    serialized.extra.image_studio = { release, source_api: `examples/api/${slug}_API.json` };
    return serialized;
  }, { prompt: api, positions, slug: spec.slug, release });

  const expectedNodeCount = Object.keys(api).length;
  if (workflow.nodes.length !== expectedNodeCount) {
    throw new Error(`${spec.slug}: expected ${expectedNodeCount} nodes, frontend serialized ${workflow.nodes.length}`);
  }

  const uiPath = path.join(repoDir, "examples", "ui", `${spec.slug}.json`);
  const pngPath = path.join(repoDir, "examples", "png", `${spec.slug}.png`);
  await writeFile(uiPath, `${JSON.stringify(workflow, null, 2)}\n`, "utf8");
  await page.locator("#graph-canvas").screenshot({ path: pngPath, type: "png" });

  const roundTrip = await page.evaluate(async (savedWorkflow) => {
    const { app } = await import("/scripts/app.js");
    await app.loadGraphData(savedWorkflow, true, false, "release-round-trip", {
      deferWarnings: true,
      skipAssetScans: true,
      silentAssetErrors: true,
    });
    const converted = await app.graphToPrompt();
    return {
      nodeCount: app.rootGraph._nodes?.length ?? 0,
      promptNodeCount: Object.keys(converted.output ?? {}).length,
    };
  }, workflow);
  if (roundTrip.nodeCount !== expectedNodeCount || roundTrip.promptNodeCount !== expectedNodeCount) {
    throw new Error(
      `${spec.slug}: UI round trip produced ${roundTrip.nodeCount} canvas / ${roundTrip.promptNodeCount} prompt nodes`,
    );
  }
  console.log(`built ${path.relative(repoDir, uiPath)} and ${path.relative(repoDir, pngPath)}`);
}

await browser.close();
if (pageErrors.length) {
  throw new Error(`Frontend errors:\n${pageErrors.join("\n")}`);
}

const python = process.env.PYTHON_PATH ?? (process.platform === "win32" ? "python" : "python3");
const metadataScript = path.join(repoDir, "scripts", "embed_workflow_png.py");
const { stdout } = await execFileAsync(python, [metadataScript, "--repo-dir", repoDir]);
if (stdout.trim()) console.log(stdout.trim());
