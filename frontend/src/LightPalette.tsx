import { useEffect, useState } from "react";
import type { Project } from "./types";

export function TransmissionPanel({
  project,
  transmittance,
  visible,
}: {
  project: Project;
  transmittance: number;
  visible: Set<string>;
}) {
  const [profile, setProfile] = useState<{
    colored_upper_bound?: boolean;
    min_sheets: number;
    max_sheets: number;
    bins: {
      sheet_count: number;
      area_ratio: number;
      direct_transmission: number;
    }[];
  } | null>(null);
  const active = [...visible].sort().join(",");
  useEffect(() => {
    let live = true;
    fetch(
      `/projects/${project.id}/light-plan?overlap_only=true&transmittance=${transmittance}&visible=${encodeURIComponent(active)}`,
    )
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((plan) => {
        if (live) setProfile(plan.overlap);
      })
      .catch(() => {
        if (live) setProfile(null);
      });
    return () => {
      live = false;
    };
  }, [project.id, project.revision_id, transmittance, active]);
  if (!profile) return null;
  const multiple = profile.bins
    .filter((row) => row.sheet_count > 1)
    .reduce((sum, row) => sum + row.area_ratio, 0);
  const depth =
    project.config.workflow?.family === "depth_scene" ||
    project.config.mode === "lightbox";
  return (
    <details className="light-palette" aria-label="Paper transmission profile">
      <summary>
        Overlapping sheets: {profile.min_sheets}–{profile.max_sheets} sheets
      </summary>
      <table>
        <thead>
          <tr>
            <th>Sheets</th>
            <th>Area</th>
            <th>
              {profile.colored_upper_bound
                ? "Max. transmission"
                : "Direct transmission"}
            </th>
          </tr>
        </thead>
        <tbody>
          {profile.bins.map((row) => (
            <tr key={row.sheet_count}>
              <td>{row.sheet_count}</td>
              <td>{(row.area_ratio * 100).toFixed(1)}%</td>
              <td>{(row.direct_transmission * 100).toFixed(2)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      {depth && visible.size > 1 && multiple < 0.1 && (
        <p role="note">
          Limited sheet overlap. Regenerate for depth transmission.
        </p>
      )}
    </details>
  );
}

type Zone = {
  id: string;
  color: string;
  x_mm: number;
  y_mm: number;
  sigma_x_mm: number;
  sigma_y_mm: number;
  intensity: number;
};
type Plan = {
  palette: string[];
  zones: Zone[];
  preview_only: boolean;
  spread_angle_degrees: number;
};

export function LightPalette({
  project,
  distance,
  diffusion,
  onDistance,
  onDiffusion,
}: {
  project: Project;
  distance: number;
  diffusion: number;
  onDistance: (value: number) => void;
  onDiffusion: (value: number) => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setFailed(false);
    setPlan(null);
    fetch(`/projects/${project.id}/light-plan`)
      .then(async (response) => {
        if (!response.ok) throw Error("Light plan unavailable");
        return response.json();
      })
      .then((value) => {
        if (active) setPlan(value);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [project.id, project.revision_id]);
  if (failed) return <p>Unable to load light setup. Refresh to retry.</p>;
  if (!plan) return <p>Loading light setup…</p>;
  const spread =
    distance * Math.tan((plan.spread_angle_degrees * Math.PI) / 360);
  return (
    <section className="light-palette" aria-label="Light colors and placement">
      <strong>Light palette · {plan.zones.length} zones</strong>
      <div className="light-swatches">
        {plan.palette.map((color) => (
          <span key={color}>
            <i style={{ backgroundColor: color }} />
            {color.toUpperCase()}
          </span>
        ))}
      </div>
      <details className="light-placement">
        <summary>Source placement</summary>
        <label>
          Source distance (mm)
          <input
            aria-label="Source distance"
            type="range"
            min="5"
            max="100"
            step="5"
            value={distance}
            onChange={(e) => onDistance(+e.target.value)}
          />
          <span>{distance} mm</span>
        </label>
        <label>
          Diffusion (mm)
          <input
            aria-label="Light diffusion"
            type="range"
            min="0"
            max="30"
            step="1"
            value={diffusion}
            onChange={(e) => onDiffusion(+e.target.value)}
          />
          <span>{diffusion} mm</span>
        </label>
        <svg
          viewBox={`0 0 ${project.width_mm} ${project.height_mm}`}
          role="img"
          aria-label="Light placement"
        >
          <rect
            width={project.width_mm}
            height={project.height_mm}
            fill="#242829"
          />
          {plan.zones.map((zone) => (
            <g key={zone.id}>
              <ellipse
                cx={zone.x_mm}
                cy={zone.y_mm}
                rx={Math.hypot(zone.sigma_x_mm, spread, diffusion)}
                ry={Math.hypot(zone.sigma_y_mm, spread, diffusion)}
                fill={zone.color}
                opacity=".45"
              />
              <circle
                cx={zone.x_mm}
                cy={zone.y_mm}
                r={Math.max(project.width_mm, project.height_mm) * 0.009}
                fill="white"
              />
            </g>
          ))}
        </svg>
        <table>
          <thead>
            <tr>
              <th>Zone</th>
              <th>Color</th>
              <th>Center X/Y (mm)</th>
              <th>Spread 2σ (mm)</th>
              <th>Gain</th>
            </tr>
          </thead>
          <tbody>
            {plan.zones.map((zone) => (
              <tr key={zone.id}>
                <td>{zone.id}</td>
                <td>
                  <i style={{ backgroundColor: zone.color }} />
                  {zone.color}
                </td>
                <td>
                  {zone.x_mm.toFixed(0)} / {zone.y_mm.toFixed(0)}
                </td>
                <td>
                  {(2 * Math.hypot(zone.sigma_x_mm, spread, diffusion)).toFixed(
                    0,
                  )}{" "}
                  /{" "}
                  {(2 * Math.hypot(zone.sigma_y_mm, spread, diffusion)).toFixed(
                    0,
                  )}
                </td>
                <td>{zone.intensity.toFixed(2)}×</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      {plan.preview_only && (
        <p role="note">Preview setup · Regenerate to save.</p>
      )}
    </section>
  );
}
