import { app } from "../../../scripts/app.js";

const PALETTES = {
  prepare: { color: "#24395c", bgcolor: "#101c31" },
  sampling: { color: "#5a3424", bgcolor: "#2c1810" },
  output: { color: "#25534d", bgcolor: "#102a27" },
  utility: { color: "#443066", bgcolor: "#211635" },
  note: { color: "#4b5563", bgcolor: "#171c27" },
};

function paletteFor(nodeType) {
  if (nodeType.includes("WorkflowNote")) return PALETTES.note;
  if (nodeType.includes("Prepare") || nodeType.includes("TextToImage") || nodeType.includes("ImageToImage") || nodeType.includes("ReferenceEdit")) {
    return PALETTES.prepare;
  }
  if (nodeType.includes("Sampling")) return PALETTES.sampling;
  if (nodeType.includes("Decode") || nodeType.includes("Selector")) return PALETTES.output;
  return PALETTES.utility;
}

app.registerExtension({
  name: "MiniMaxH3.ImageStudio.Theme",
  nodeCreated(node) {
    const nodeType = node.constructor?.comfyClass ?? node.comfyClass ?? node.type ?? "";
    if (!nodeType.startsWith("H3")) return;

    const palette = paletteFor(nodeType);
    node.color = palette.color;
    node.bgcolor = palette.bgcolor;
    node.properties ??= {};
    node.properties["Image Studio"] = "v17";

    const [width, height] = node.size ?? [0, 0];
    const minWidth = nodeType.includes("WorkflowNote") ? 520 : 360;
    node.setSize?.([Math.max(width, minWidth), height]);
  },
});
