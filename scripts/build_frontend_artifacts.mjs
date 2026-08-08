#!/usr/bin/env node
import { createRequire } from "node:module";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const repoDir = path.resolve(process.env.REPO_DIR ?? path.join(import.meta.dirname, ".."));
const comfyUrl = process.env.COMFY_URL ?? "http://127.0.0.1:8191";
const chromiumPath = process.env.CHROMIUM_PATH;
const playwrightModule = process.env.PLAYWRIGHT_MODULE ?? "playwright";
const viewport = { width: 3200, height: 1800 };

if (!chromiumPath) throw new Error("Set CHROMIUM_PATH to a Chromium/Chrome executable.");
const { chromium } = require(playwrightModule);

const commonModels = [
  "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors (FL2VA)",
  "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
  "vae/minimax_h3_video_vae_fp16.safetensors",
];

const workflowSpecs = [
  {
    slug: "H3_T2I",
    api: "H3_T2I_API.json",
    title: "Text to Image",
    quick: "1. Check the three loader filenames.\n2. Edit the prompt in Text to Image.\n3. Choose resolution and frame profile.\n4. Queue Prompt.\n\nThe saved result is one still; the temporal packet is internal context.",
    models: commonModels.join("\n"),
    settings: "Base speed uses RES Multistep + simple at 12 steps. For maximum base quality choose the 20-step profile. Exact Frame Decode emits every requested frame; Single Image Output receives the decoder's recommended index and defaults to frame 0 for T2I.",
    optional: "LightX and Larry Turbo adapters use different recipes. Do not reuse a generic Turbo preset. For LightX v0.1 open the dedicated workflow. KJNodes memory-efficient attention and Tiny VAE preview are optional; neither is required here.",
  },
  {
    slug: "H3_I2I",
    api: "H3_I2I_API.json",
    title: "Image to Image",
    quick: "1. Select input.png in Load Image.\n2. Check FL2VA, Qwen and VAE filenames.\n3. Describe only the desired edit.\n4. Queue Prompt.\n\nStart with 5 frames. Increase temporal context only when it visibly helps.",
    models: commonModels.join("\n"),
    settings: "source_fidelity is prompt guidance, not a denoise slider. The 5-frame profile normally recommends frame 0; the 20-frame I2I profile can measure a later stable edit. The decoder's recommended_index is wired directly into Single Image Output.",
    optional: "Metric strategies remain available if you want to compare frames, but they can favor sharpness over edit intent. Frame selection alone cannot fix source copying: edit wording and conditioning remain decisive. Keep 5/9/13/20-frame image profiles; do not convert this into a long video workflow.",
  },
  {
    slug: "H3_REFERENCE_EDIT",
    api: "H3_REFERENCE_EDIT_API.json",
    title: "Reference Edit (REF2VA)",
    quick: "1. Picture 1 is the source to preserve.\n2. Picture 2 is the donor reference.\n3. Name both pictures explicitly in the edit instruction.\n4. Queue Prompt.\n\nAdd further ordered references only when each has a clear role.",
    models: [
      "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors (REF2VA)",
      commonModels[1],
      commonModels[2],
    ].join("\n"),
    settings: "A robust instruction says: keep Picture 1 person/face/body/pose/camera/framing/background; replace only the named feature from Picture 2. source_fidelity strengthens that wording but cannot enforce a pixel lock. The standard workflow stays at the recommended 5-frame profile.",
    optional: "REF2VA supports up to nine ordered references. More references are not automatically better and can introduce ambiguity. Experimental w4a8 checkpoints may reduce memory, but require a current ComfyUI build and remain outside this dependency-free workflow.",
  },
  {
    slug: "H3_I2I_LIGHTX_TURBO",
    api: "H3_I2I_LIGHTX_TURBO_API.json",
    title: "Image to Image · LightX v0.1",
    quick: "1. Install the exact LightX v0.1 Comfy LoRA.\n2. Select input.png.\n3. Keep strength 0.75 as the published starting point.\n4. Queue Prompt.\n\nThis is the Kijai/LightX recipe, not Larry Turbo.",
    models: [...commonModels, "loras/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"].join("\n"),
    settings: "Published LightX v0.1 recipe: 4 steps, simple scheduler, ER-SDE (this workflow) or SA-Solver (alternate preset), H3 shifts 12/3, LoRA strength 0.75. The older generic RES Turbo options are intentionally deprecated.",
    optional: "KJNodes can add MiniMax H3 memory-efficient SageAttention, low-VRAM attention and chunked feed-forward patches. They are optional and hardware/build dependent. Larry's adapter uses its own loader/sampler and recommended v4 checkpoint; follow that extension's workflow instead of substituting it here.",
  },
];

const basePositions = {
  "1": [0, 430],
  "15": [470, 430],
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
  "900": [0, 0],
  "901": [850, 0],
  "902": [1700, 0],
  "903": [2550, 0],
};

function documentationNodes(spec) {
  return {
    "900": { class_type: "H3WorkflowNote", inputs: { section: "quick start", text: spec.quick }, _meta: { title: `START HERE · ${spec.title}` } },
    "901": { class_type: "H3WorkflowNote", inputs: { section: "models", text: `MODEL LOCATIONS\n\n${spec.models}` }, _meta: { title: "MODELS & FOLDERS" } },
    "902": { class_type: "H3WorkflowNote", inputs: { section: "settings", text: spec.settings }, _meta: { title: "WHY THESE SETTINGS" } },
    "903": { class_type: "H3WorkflowNote", inputs: { section: "optional / experimental", text: spec.optional }, _meta: { title: "OPTIONAL / EXPERIMENTAL" } },
  };
}

const browser = await chromium.launch({
  executablePath: chromiumPath,
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-webgl", "--no-zygote", "--single-process"],
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
  const apiWithNotes = { ...api, ...documentationNodes(spec) };

  const workflow = await page.evaluate(async ({ prompt, positions, slug }) => {
    const { app } = await import("/scripts/app.js");
    app.loadApiJson(prompt, slug);
    await new Promise((resolve) => setTimeout(resolve, 300));

    for (const [id, pos] of Object.entries(positions)) {
      const node = app.rootGraph.getNodeById(Number(id));
      if (!node) continue;
      node.pos = [...pos];
      if (Number(id) >= 900) node.setSize([760, 310]);
      else node.setSize([Math.max(node.size?.[0] ?? 0, 390), node.size?.[1] ?? 0]);
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
    serialized.extra.image_studio = { release: "v15.0.0", source_api: `examples/api/${slug}_API.json` };
    return serialized;
  }, { prompt: apiWithNotes, positions: basePositions, slug: spec.slug });

  const expectedNodeCount = Object.keys(apiWithNotes).length;
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
