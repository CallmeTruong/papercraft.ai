import type { Project } from "./types";

export type FrameKind = "auto" | "walnut" | "oak" | "white" | "none";

export function framePalette(project: Project, kind: FrameKind) {
  const luminance = (hex: string) => {
    const channels = [1, 3, 5]
      .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const colors = project.layers.map((l) => l.color);
  const mean =
    colors.reduce((sum, c) => sum + luminance(c), 0) /
    Math.max(1, colors.length);
  const material = kind === "auto" ? (mean < 0.28 ? "oak" : "walnut") : kind;
  const wood =
    material === "oak"
      ? "#b78956"
      : material === "white"
        ? "#f2eee5"
        : "#533b2c";
  const grain =
    material === "oak"
      ? "#caa574"
      : material === "white"
        ? "#e1d9cd"
        : "#715039";
  // The front sheet supplies the visible retained border: contrast against that edge.
  const candidates = ["#f4eddd", "#302c28"];
  const score = (candidate: string) =>
    Math.min(
      ...[colors[0] || "#ffffff"].map((c) => {
        const a = luminance(candidate),
          b = luminance(c);
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      }),
    );
  const mat = candidates.sort((a, b) => score(b) - score(a))[0];
  return { wood, grain, mat, material };
}
