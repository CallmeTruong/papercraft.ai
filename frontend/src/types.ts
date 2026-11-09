export type Config = {
  abstraction_policy?: "cut_aware_v2" | "detail_aware_v3" | null;
  depth_paper?: "white" | "multicolor" | null;
  detail_boxes?: [number, number, number, number][];
  detail_policy?: "prefer" | "strict";
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
  ai?: {
    segmentation: boolean;
    depth: boolean;
    face_features: boolean;
    important_details?: boolean;
    detail_crops?: number;
    detail_seconds?: number;
    device?: "cpu" | "cuda";
    failure_policy?: "fallback" | "strict";
    subject_box?: [number, number, number, number] | null;
    max_feature_change?: number;
  } | null;
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
  metrics: {
    [key: string]: unknown;
    ai_diagnostics?: {
      component: string;
      status: string;
      reason?: string;
      model_id?: string;
      revision?: string;
      method?: string;
      region_count?: number;
      regions?: {
        box: [number, number, number, number];
        label: string;
        source: string;
      }[];
    }[];
  };
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
    planning_version?: number;
    source_distance_mm?: number;
    diffusion_mm_assumption?: number;
  } | null;
  legacy_requires_conversion?: boolean;
  material_accounting?: {
    cut_sheet_count: number;
    colors_used: number;
    uncut_backing_count: number;
    diffuser_count: number;
    colored_diffuser_count?: number;
    colored_source_filter_count?: number;
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
    image_fidelity?: {
      status: string;
      source_structure_similarity?: number;
      recognition_review?: string;
      construction?: string;
      detail_regions?: {
        index: number;
        color_agreement: number;
        status: string;
      }[];
      automatic_detail_regions?: {
        index: number;
        label: string;
        color_agreement: number;
        contrast_ratio: number;
        status: string;
      }[];
    };
    layers: { layer_id: string; findings: Finding[] }[];
  };
};
