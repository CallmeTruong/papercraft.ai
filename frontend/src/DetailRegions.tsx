import { useRef, useState } from "react";
import type { Config } from "./types";

type Box = [number, number, number, number];
export function DetailRegions({
  projectId,
  config,
  disabled,
  onChange,
  onPolicy,
  automatic = [],
  onAdopt,
}: {
  projectId: string;
  config: Config;
  disabled: boolean;
  onChange: (boxes: Box[]) => void;
  onPolicy: (policy: "prefer" | "strict") => void;
  automatic?: { box: Box; label: string; source: string }[];
  onAdopt?: (box: Box) => void;
}) {
  const start = useRef<[number, number] | null>(null);
  const [draft, setDraft] = useState<Box | null>(null);
  const boxes = config.detail_boxes || [];
  function point(e: React.PointerEvent<HTMLDivElement>): [number, number] {
    const r = e.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    ];
  }
  function rectangle(a: [number, number], b: [number, number]): Box {
    return [
      Math.min(a[0], b[0]),
      Math.min(a[1], b[1]),
      Math.max(a[0], b[0]),
      Math.max(a[1], b[1]),
    ];
  }
  return (
    <details className="field-group">
      <summary>Detail protection ({boxes.length}/16)</summary>

      {!!automatic.length && (
        <details className="suggested-regions">
          <summary>Suggested regions ({automatic.length})</summary>
          {automatic.map((region, i) => (
            <div key={`auto-${i}`} className="detail-region-row">
              <small>
                {region.label} ·{" "}
                {region.source === "model+structure"
                  ? "Model + structure"
                  : "Structure"}
              </small>
              <button
                type="button"
                disabled={disabled || boxes.length >= 16}
                onClick={() => onAdopt?.(region.box)}
              >
                Keep suggestion {i + 1}
              </button>
            </div>
          ))}
        </details>
      )}
      <label
        className="checkbox-row"
        title="Reject results that change a selected region by more than 10%."
      >
        <input
          type="checkbox"
          disabled={disabled}
          checked={config.detail_policy === "strict"}
          onChange={(e) => onPolicy(e.target.checked ? "strict" : "prefer")}
        />
        <span>Strict protection</span>
      </label>
      <small>
        Draw regions to protect. Dashed green: suggestions. Purple: selected.
      </small>

      <div
        role="img"
        aria-label="Draw detail regions"
        style={{
          position: "relative",
          touchAction: "none",
          cursor: disabled ? "default" : "crosshair",
          aspectRatio: config.height_mm
            ? `${config.width_mm - 2 * config.frame_mm}/${config.height_mm - 2 * config.frame_mm}`
            : undefined,
          background: config.backing,
        }}
        onPointerDown={(e) => {
          if (disabled || boxes.length >= 16) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          start.current = point(e);
          setDraft(null);
        }}
        onPointerMove={(e) => {
          if (start.current) setDraft(rectangle(start.current, point(e)));
        }}
        onPointerCancel={() => {
          start.current = null;
          setDraft(null);
        }}
        onPointerUp={(e) => {
          if (!start.current) return;
          const b = rectangle(start.current, point(e));
          start.current = null;
          setDraft(null);
          if (b[2] - b[0] > 0.01 && b[3] - b[1] > 0.01) onChange([...boxes, b]);
        }}
      >
        <img
          src={`/projects/${projectId}/source`}
          alt="Source for detail selection"
          draggable={false}
          style={{
            display: "block",
            width: "100%",
            height: config.height_mm ? "100%" : "auto",
            objectFit: config.artwork_fit === "cover" ? "cover" : "contain",
            pointerEvents: "none",
          }}
        />
        <svg
          viewBox="0 0 1000 1000"
          preserveAspectRatio="none"
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            pointerEvents: "none",
          }}
        >
          {automatic.map(({ box: b, label }, i) => (
            <rect
              key={`auto-${i}`}
              x={b[0] * 1000}
              y={b[1] * 1000}
              width={(b[2] - b[0]) * 1000}
              height={(b[3] - b[1]) * 1000}
              fill="#26845b18"
              stroke="#26845b"
              strokeDasharray="12 8"
              strokeWidth="5"
            >
              <title>{label}</title>
            </rect>
          ))}
          {[...boxes, ...(draft ? [draft] : [])].map((b, i) => (
            <rect
              key={i}
              x={b[0] * 1000}
              y={b[1] * 1000}
              width={(b[2] - b[0]) * 1000}
              height={(b[3] - b[1]) * 1000}
              fill="#915bd430"
              stroke="#6331a3"
              strokeWidth="5"
            />
          ))}
        </svg>
      </div>
      {boxes.map((b, i) => (
        <div key={i} className="detail-region-row">
          <small>
            Region {i + 1}:{" "}
            {Math.round(
              (b[2] - b[0]) * (config.width_mm - 2 * config.frame_mm),
            )}{" "}
            mm wide
          </small>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChange(boxes.filter((_, j) => i !== j))}
          >
            Remove region {i + 1}
          </button>
        </div>
      ))}
    </details>
  );
}
