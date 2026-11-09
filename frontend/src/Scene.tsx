import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { Project } from "./types";
import { framePalette, type FrameKind } from "./presentation";

export function Scene({
  project,
  view,
  visible,
  intensity,
  lightColor,
  shadows,
  lightMode,
  transmittance,
  frameKind = "auto",
  sourceDistance = 25,
  diffusion = 6,
  photoOverlay = false,
  photoStrength = 1,
}: {
  project: Project;
  view: string;
  visible: Set<string>;
  intensity: number;
  lightColor: string;
  shadows: boolean;
  lightMode: string;
  transmittance: number;
  frameKind?: FrameKind;
  sourceDistance?: number;
  diffusion?: number;
  photoOverlay?: boolean;
  photoStrength?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [loadedFields, setLoadedFields] = useState(0);
  const [textureError, setTextureError] = useState(false);
  const [previewGain, setPreviewGain] = useState(intensity);
  const [previewPhotoStrength, setPreviewPhotoStrength] =
    useState(photoStrength);
  useEffect(() => {
    const timer = setTimeout(() => setPreviewPhotoStrength(photoStrength), 150);
    return () => clearTimeout(timer);
  }, [photoStrength]);
  useEffect(() => {
    const timer = setTimeout(() => setPreviewGain(intensity), 150);
    return () => clearTimeout(timer);
  }, [intensity]);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setUnsupported(true);
      return;
    }
    setUnsupported(false);
    setLoadedFields(0);
    setTextureError(false);
    let disposed = false;
    const textures: THREE.Texture[] = [];
    const lightbox = view === "lightbox";
    const colorLight = lightbox && lightMode === "source";
    const previewUrl = (layer: string) =>
      `/projects/${project.id}/light-preview/${layer}?revision_id=${project.revision_id}&transmittance=${transmittance}&source_distance_mm=${sourceDistance}&diffusion_mm=${diffusion}&source_gain=${previewGain}&photo_overlay=${photoOverlay}&photo_strength=${previewPhotoStrength}&visible=${encodeURIComponent([...visible].join(","))}${!colorLight ? `&uniform_color=${encodeURIComponent(lightColor)}` : ""}`;
    const loader = new THREE.TextureLoader();
    function loadField(
      layer: string,
      onLoad: (texture: THREE.Texture) => void,
    ) {
      const texture = loader.load(
        previewUrl(layer),
        (loaded) => {
          if (!disposed) {
            onLoad(loaded);
            setLoadedFields((count) => count + 1);
          }
        },
        undefined,
        () => {
          if (!disposed) setTextureError(true);
        },
      );
      texture.colorSpace = THREE.SRGBColorSpace;
      textures.push(texture);
      return texture;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.shadowMap.enabled = shadows;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.setClearColor(0xf0eee9, 0);
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const size = Math.max(project.width_mm, project.height_mm);
    // Millimetre-thin touching sheets need a useful depth-buffer range. A0.1mm
    // near plane at a several-hundred-mm camera distance causes surface fighting.
    const camera = new THREE.PerspectiveCamera(
      34,
      1,
      Math.max(1, size * 0.02),
      size * 25,
    );
    camera.position.set(
      size * (view === "exploded" ? 0.8 : 0.12),
      size * (view === "exploded" ? 0.7 : 0.08),
      size * 1.75,
    );
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.minDistance = size * 0.3;
    controls.maxDistance = size * 6;
    scene.add(new THREE.AmbientLight(0xffffff, lightbox ? 0.06 : 0.65));
    const key = new THREE.DirectionalLight(0xffffff, lightbox ? 0.15 : 2.8);
    key.castShadow = shadows;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.left = -project.width_mm * 0.65;
    key.shadow.camera.right = project.width_mm * 0.65;
    key.shadow.camera.top = project.height_mm * 0.65;
    key.shadow.camera.bottom = -project.height_mm * 0.65;
    key.shadow.camera.near = size * 0.4;
    key.shadow.camera.far = size * 4;
    key.shadow.normalBias = 0.18;
    key.shadow.bias = -0.0008;
    key.shadow.radius = 2;
    key.position.set(-size * 0.8, size * 0.9, size * 1.3);
    scene.add(key);
    const meshes: THREE.Mesh[] = [];
    const thickness = Number(project.profile.thickness_mm);
    let offset = 0;
    if (frameKind !== "none" && visible.size > 1 && view !== "exploded") {
      const colors = framePalette(project, frameKind);
      const mat = size * 0.022,
        rail = size * 0.035;
      function ring(
        innerW: number,
        innerH: number,
        width: number,
        color: string,
        depth: number,
      ) {
        const material = new THREE.MeshStandardMaterial({
          color,
          roughness: 0.85,
        });
        for (const [w, h, x, y] of [
          [innerW + 2 * width, width, 0, (innerH + width) / 2],
          [innerW + 2 * width, width, 0, -(innerH + width) / 2],
          [width, innerH, -(innerW + width) / 2, 0],
          [width, innerH, (innerW + width) / 2, 0],
        ]) {
          const mesh = new THREE.Mesh(
            new THREE.BoxGeometry(w, h, depth),
            material.clone(),
          );
          mesh.position.set(x, y, 1);
          mesh.castShadow = true;
          mesh.receiveShadow = true;
          meshes.push(mesh);
          scene.add(mesh);
        }
        material.dispose();
      }
      ring(project.width_mm, project.height_mm, mat, colors.mat, 1);
      ring(
        project.width_mm + 2 * mat,
        project.height_mm + 2 * mat,
        rail,
        colors.wood,
        5,
      );
    }
    for (const [index, layer] of project.layers.entries()) {
      const layerZ = -offset;
      offset +=
        thickness +
        (view === "exploded"
          ? Math.max(layer.spacing_mm * 4, 12)
          : layer.spacing_mm);
      if (!visible.has(layer.id)) continue;
      const polygons =
        layer.geometry.type === "Polygon"
          ? [layer.geometry.coordinates as number[][][]]
          : (layer.geometry.coordinates as number[][][][]);
      const shapes: THREE.Shape[] = [];
      for (const rings of polygons) {
        const points = rings[0].map(
          ([x, y]) =>
            new THREE.Vector2(
              x - project.width_mm / 2,
              project.height_mm / 2 - y,
            ),
        );
        const shape = new THREE.Shape(points);
        for (const ring of rings.slice(1)) {
          shape.holes.push(
            new THREE.Path(
              ring.map(
                ([x, y]) =>
                  new THREE.Vector2(
                    x - project.width_mm / 2,
                    project.height_mm / 2 - y,
                  ),
              ),
            ),
          );
        }
        shapes.push(shape);
      }
      const geometry = new THREE.ExtrudeGeometry(shapes, {
        depth: thickness,
        bevelEnabled: false,
      });
      if (lightbox) {
        const position = geometry.getAttribute("position");
        const uv = new Float32Array(position.count * 2);
        for (let i = 0; i < position.count; i++) {
          uv[i * 2] =
            (position.getX(i) + project.width_mm / 2) / project.width_mm;
          uv[i * 2 + 1] =
            (position.getY(i) + project.height_mm / 2) / project.height_mm;
        }
        geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
      }
      const material = new THREE.MeshStandardMaterial({
        color: layer.color,
        roughness: 0.9,
        metalness: 0,
        emissive: lightbox ? "#ffffff" : "#000000",
        emissiveIntensity: lightbox ? 1 : 0,
        side: THREE.DoubleSide,
        shadowSide: THREE.BackSide,
      });
      if (lightbox) {
        material.emissiveMap = loadField(layer.id, () => {
          material.needsUpdate = true;
        });
        material.needsUpdate = true;
      }
      const edgeMaterial = new THREE.MeshStandardMaterial({
        color: layer.color,
        roughness: 1,
        side: THREE.DoubleSide,
      });
      const mesh = new THREE.Mesh(geometry, [material, edgeMaterial]);
      mesh.position.z = layerZ;
      mesh.castShadow = shadows;
      mesh.receiveShadow = shadows;
      scene.add(mesh);
      meshes.push(mesh);
    }
    controls.target.set(0, 0, -offset / 2);
    camera.position.z -= offset / 2;
    if (!lightbox) {
      const backing = new THREE.Mesh(
        new THREE.PlaneGeometry(project.width_mm, project.height_mm),
        new THREE.MeshStandardMaterial({
          color: project.config.backing,
          roughness: 1,
          side: THREE.DoubleSide,
        }),
      );
      backing.position.z = -offset;
      backing.receiveShadow = shadows;
      scene.add(backing);
      meshes.push(backing);
    }
    if (lightbox) {
      const background = new THREE.Mesh(
        new THREE.PlaneGeometry(project.width_mm, project.height_mm),
        new THREE.MeshBasicMaterial({
          color: new THREE.Color("#ffffff"),
          side: THREE.DoubleSide,
        }),
      );
      if (lightbox) {
        background.material.map = loadField("diffuser", () => {
          background.material.needsUpdate = true;
        });
        background.material.needsUpdate = true;
      }
      background.position.z = -offset - (photoOverlay ? 1 : 0);
      scene.add(background);
      meshes.push(background);
      if (photoOverlay) {
        const photoMaterial = new THREE.MeshBasicMaterial({
          color: "#ffffff",
          side: THREE.DoubleSide,
        });
        photoMaterial.map = loadField("photo", () => {
          photoMaterial.needsUpdate = true;
        });
        const photo = new THREE.Mesh(
          new THREE.PlaneGeometry(project.width_mm, project.height_mm),
          photoMaterial,
        );
        photo.position.z = -offset + 0.1;
        scene.add(photo);
        meshes.push(photo);
      }
      const backlight = new THREE.PointLight(lightColor, 0, size * 6);
      backlight.position.set(0, 0, -size);
      scene.add(backlight);
    }
    const resize = () => {
      const { width, height } = element.getBoundingClientRect();
      renderer.setSize(width, height);
      camera.aspect = width / Math.max(height, 1);
      const radius =
        Math.hypot(project.width_mm, project.height_mm, offset) / 2;
      const fitDistance =
        radius /
        Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) /
        Math.min(1, camera.aspect);
      const direction = camera.position
        .clone()
        .sub(controls.target)
        .normalize();
      camera.position
        .copy(controls.target)
        .addScaledVector(direction, fitDistance * 1.05);
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    let animation = 0;
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      animation = requestAnimationFrame(render);
    };
    render();
    return () => {
      disposed = true;
      cancelAnimationFrame(animation);
      observer.disconnect();
      controls.dispose();
      for (const mesh of meshes) {
        mesh.geometry.dispose();
        for (const material of Array.isArray(mesh.material)
          ? mesh.material
          : [mesh.material])
          material.dispose();
      }
      renderer.dispose();
      textures.forEach((texture) => texture.dispose());
      key.shadow.map?.dispose();
      renderer.domElement.remove();
    };
  }, [
    project,
    view,
    visible,
    previewGain,
    lightColor,
    shadows,
    lightMode,
    transmittance,
    frameKind,
    sourceDistance,
    diffusion,
    photoOverlay,
    previewPhotoStrength,
  ]);
  return (
    <div
      ref={host}
      className="three-scene"
      aria-label="3D preview"
      data-light-mode={view === "lightbox" ? lightMode : undefined}
      data-light-fields-loaded={loadedFields}
      data-photo-overlay={
        view === "lightbox" && photoOverlay ? "true" : "false"
      }
    >
      {!unsupported &&
        view === "lightbox" &&
        loadedFields < visible.size + 1 + (photoOverlay ? 1 : 0) && (
          <div className="scene-loading">
            {textureError
              ? "Unable to load lighting. Refresh to retry."
              : "Rendering light…"}
          </div>
        )}
      {unsupported && (
        <div className="canvas-empty">
          <p>3D preview unavailable.</p>
          <p>Switch to Flat view to continue.</p>
        </div>
      )}
    </div>
  );
}
