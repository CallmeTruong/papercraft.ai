import { useId } from "react";
import type { Finding, Project } from "./types";

export function FlatPreview({
  project,
  visible,
  selected,
  focusedIssue,
  shadows,
}: {
  project: Project;
  visible: Set<string>;
  selected: string | null;
  focusedIssue: Finding | null;
  shadows: boolean;
}) {
  const uid = "paper-" + useId().replace(/[^a-zA-Z0-9]/g, "");
  const width = project.width_mm,
    height = project.height_mm;
  const shown = project.layers.filter((layer) => visible.has(layer.id));
  const single = selected
    ? shown.find((layer) => layer.id === selected)
    : shown.length === 1
      ? shown[0]
      : undefined;
  const layers = single ? [single] : shown;
  const spaced =
    project.config.workflow?.assembly === "spaced" ||
    (!project.config.workflow && project.config.spacing_mm > 0);
  const depth =
    project.config.workflow?.family === "depth_scene" ||
    (!project.config.workflow && project.config.mode === "lightbox");
  const shade = shadows && !single && (depth || spaced);
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      aria-label="Flat paper design"
      data-testid="flat-preview"
      data-display-mode={single ? "layer" : "stack"}
      data-preview-layer={single?.id || ""}
    >
      <defs>
        <pattern
          id={`${uid}-hatch`}
          width="4"
          height="4"
          patternUnits="userSpaceOnUse"
        >
          <path d="M0 4L4 0" fill="none" stroke="#9aa69d" strokeWidth="0.18" />
        </pattern>
        {single && (
          <mask
            id={`${uid}-waste`}
            maskUnits="userSpaceOnUse"
            x="0"
            y="0"
            width={width}
            height={height}
          >
            <rect width={width} height={height} fill="white" />
            <path d={single.path} fill="black" fillRule="evenodd" />
          </mask>
        )}
        {shade &&
          layers.map((layer) => {
            const gap = Math.max(0, layer.spacing_mm);
            return (
              <filter
                key={layer.id}
                id={`${uid}-shadow-${layer.id}`}
                filterUnits="userSpaceOnUse"
                x="0"
                y="0"
                width={width}
                height={height}
                colorInterpolationFilters="sRGB"
              >
                <feDropShadow
                  dx={gap * 0.5}
                  dy={gap * 0.65}
                  stdDeviation={Math.max(0.12, gap * 0.28)}
                  floodColor="#27332d"
                  floodOpacity={gap > 0 ? 0.43 : 0}
                />
              </filter>
            );
          })}
      </defs>
      <rect
        width={width}
        height={height}
        fill={single ? "#ffffff" : project.config.backing}
      />
      {single && (
        <rect
          data-testid="waste-hatch"
          width={width}
          height={height}
          fill={`url(#${uid}-hatch)`}
          mask={`url(#${uid}-waste)`}
        />
      )}
      {[...layers].reverse().map((layer) => (
        <g
          key={layer.id}
          filter={shade ? `url(#${uid}-shadow-${layer.id})` : undefined}
        >
          <path
            d={layer.path}
            fill={layer.color}
            fillRule="evenodd"
            stroke={single ? "#43564d" : "none"}
            strokeWidth={single ? 0.15 : 0}
          />
        </g>
      ))}
      {focusedIssue && (
        <circle
          cx={focusedIssue.location_mm[0]}
          cy={focusedIssue.location_mm[1]}
          r={Math.max(width, height) * 0.025}
          fill="none"
          stroke="#ed466a"
          strokeWidth="1"
        />
      )}
    </svg>
  );
}
