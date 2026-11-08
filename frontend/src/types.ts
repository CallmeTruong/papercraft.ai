export type Config = {
  mode: "multicolor" | "lightbox";
  colors: number;
  layers: number;
  detail: number;
  palette: string[] | null;
  seed: number;
  max_side: number;
  width_mm: number;
  height_mm: number | null;
  spacing_mm: number;
  assembly_mode: "frame_connected" | "full_sheet" | "mounted_pieces";
  backing: string;
  frame_mm: number;
  artwork_fit?: "contain" | "cover";
  workflow?: Workflow | null;
};
export type Workflow = {
  workflow_schema?: 1;
  family: "stencil" | "multicolor" | "depth_scene";
  style:
    | "bold"
    | "detailed"
    | "colorblock"
    | "splash_portrait"
    | "landscape"
    | "portal";
  assembly: "flat" | "spaced";
  lighting: "ambient" | "backlit";
};
export type Layer = {
  id: string;
  color: string;
  index: number;
  spacing_mm: number;
  path: string;
  geometry: { type: string; coordinates: number[][][] | number[][][][] };
  region_ids: number[];
};
export type Finding = {
  code: string;
  message: string;
  layer_id: string;
  location_mm: number[];
};
export type Project = {
  id: string;
  revision_id: string;
  config: Config;
  layers: Layer[];
  width_mm: number;
  height_mm: number;
  report_current: boolean;
  can_undo: boolean;
  can_redo: boolean;
  metrics: Record<string, number>;
  profile: Record<string, string | number>;
  scene_plan?: object | null;
  production_receipts?: {
    layer_id: string;
    status: string;
    bridge_count: number;
    material_change_ratio: number;
  }[];
  fabrication_state?: "ready" | "draft_unfabricable";
  light_plan?: {
    mode: string;
    paper_transmittance_assumption: number;
    optical_validation_status: string;
  } | null;
  legacy_requires_conversion?: boolean;
  material_accounting?: {
    cut_sheet_count: number;
    colors_used: number;
    uncut_backing_count: number;
    diffuser_count: number;
    colored_diffuser_count?: number;
    spacer_count: number;
  };
  regions: { id: number; area_px: number }[];
  depth_assignments: Record<number, number> | null;
  repairs: {
    index: number;
    operation: string;
    layer_id: string;
    enabled?: boolean;
    adjusted_width_mm?: number;
  }[];
  report: null | {
    status: string;
    layers: { layer_id: string; findings: Finding[] }[];
  };
};
