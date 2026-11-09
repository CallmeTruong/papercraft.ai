type SceneObject = {
  object_id: string;
  layer_index: number;
  role: "retain" | "cutout";
  support_role: string;
  locked: boolean;
  color: string;
  [key: string]: unknown;
};
type Scene = { objects: SceneObject[]; [key: string]: unknown };

export function ScenePlanner({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  let scene: Scene;
  try {
    scene = JSON.parse(value);
    if (!Array.isArray(scene.objects)) return null;
  } catch {
    return null;
  }
  function update(index: number, key: string, next: unknown) {
    onChange(
      JSON.stringify(
        {
          ...scene,
          objects: scene.objects.map((object, i) =>
            i === index ? { ...object, [key]: next } : object,
          ),
        },
        null,
        2,
      ),
    );
  }
  return (
    <div className="scene-planner">
      <p>Scene objects</p>
      {scene.objects.map((object, index) => (
        <fieldset disabled={disabled} key={object.object_id}>
          <legend>{object.object_id}</legend>
          <label>
            Operation
            <select
              aria-label={`Role ${object.object_id}`}
              value={object.role}
              onChange={(e) => update(index, "role", e.target.value)}
            >
              <option value="retain">Keep paper</option>
              <option value="cutout">Remove</option>
            </select>
          </label>
          <label>
            Layer (0 = front)
            <input
              aria-label={`Object layer ${object.object_id}`}
              type="number"
              min="0"
              max="23"
              value={object.layer_index}
              onChange={(e) => update(index, "layer_index", +e.target.value)}
            />
          </label>
          <label>
            Object type
            <select
              aria-label={`Object type ${object.object_id}`}
              value={object.support_role}
              onChange={(e) => update(index, "support_role", e.target.value)}
            >
              {["subject", "ground", "tree", "frame", "background"].map(
                (role) => (
                  <option key={role}>{role}</option>
                ),
              )}
            </select>
          </label>
          <label>
            Paper color
            <input
              aria-label={`Object color ${object.object_id}`}
              type="color"
              value={object.color}
              onChange={(e) => update(index, "color", e.target.value)}
            />
          </label>
          <label>
            <input
              aria-label={`Lock object ${object.object_id}`}
              type="checkbox"
              checked={object.locked}
              onChange={(e) => update(index, "locked", e.target.checked)}
            />{" "}
            Lock geometry
          </label>
        </fieldset>
      ))}
    </div>
  );
}
