// Three.js massing model. Same plan JSON as the 2D renderer -- same source
// of truth, so 2D and 3D cannot disagree (rule 1). No textures, shadows,
// doors/windows or roof detail: intentionally out of scope for the 3D view.

const ZONE_COLOR_3D = {
  social: 0x8fc7ff,
  service: 0xffbf80,
  private: 0x9fe0b8,
  circ: 0xc4a9f0,
};
const FLOOR_HEIGHT = 3.0;
const WALL_THICKNESS = 0.12;
const WALL_HEIGHT = 2.7;
const SLAB_THICKNESS = 0.3; // thicker + dark so each floor reads as a distinct band

let _renderer, _scene, _camera, _controls, _buildingGroup, _container, _resizeHandler;

function ensureScene(container) {
  if (_renderer && _container === container) return;
  _container = container;

  _scene = new THREE.Scene();
  _scene.background = new THREE.Color(0xeef1f5);

  _camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 500);

  _renderer = new THREE.WebGLRenderer({ antialias: true });
  _renderer.setSize(container.clientWidth, container.clientHeight);
  container.innerHTML = "";
  container.appendChild(_renderer.domElement);

  _controls = new THREE.OrbitControls(_camera, _renderer.domElement);
  _controls.enableDamping = true;

  const ambient = new THREE.AmbientLight(0xffffff, 0.7);
  _scene.add(ambient);
  const directional = new THREE.DirectionalLight(0xffffff, 0.8);
  directional.position.set(20, 30, 10);
  _scene.add(directional);

  _buildingGroup = new THREE.Group();
  _scene.add(_buildingGroup);

  if (_resizeHandler) window.removeEventListener("resize", _resizeHandler);
  _resizeHandler = () => {
    if (!_container) return;
    _camera.aspect = _container.clientWidth / _container.clientHeight;
    _camera.updateProjectionMatrix();
    _renderer.setSize(_container.clientWidth, _container.clientHeight);
  };
  window.addEventListener("resize", _resizeHandler);

  (function animate() {
    requestAnimationFrame(animate);
    _controls.update();
    _renderer.render(_scene, _camera);
  })();
}

function addBox(group, x, y, z, w, h, d, color) {
  const geo = new THREE.BoxGeometry(w, h, d);
  const mat = new THREE.MeshLambertMaterial({ color });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(x + w / 2, y + h / 2, z + d / 2);
  group.add(mesh);
  return mesh;
}

function renderPlan3D(container, plan) {
  ensureScene(container);
  while (_buildingGroup.children.length) _buildingGroup.remove(_buildingGroup.children[0]);
  if (!plan || !plan.ok) return;

  const envX = plan.setbacks.left, envY = plan.setbacks.front;
  const plotW = plan.plot_w_m, plotD = plan.plot_d_m;

  // ground plane for the plot
  const groundGeo = new THREE.PlaneGeometry(plotW, plotD);
  const groundMat = new THREE.MeshLambertMaterial({ color: 0xd9dfc9, side: THREE.DoubleSide });
  const ground = new THREE.Mesh(groundGeo, groundMat);
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(plotW / 2, -0.02, plotD / 2);
  _buildingGroup.add(ground);

  plan.floors.forEach(floor => {
    const baseY = floor.index * FLOOR_HEIGHT;
    floor.rooms.forEach(room => {
      const x = envX + room.x, z = envY + room.y;
      const color = ZONE_COLOR_3D[room.zone] || 0xcccccc;

      // floor slab -- dark band so each storey reads as visually distinct
      addBox(_buildingGroup, x, baseY, z, room.w, SLAB_THICKNESS, room.h, 0x555b66);

      // 4 perimeter walls
      const wt = WALL_THICKNESS, wy = baseY + SLAB_THICKNESS;
      addBox(_buildingGroup, x, wy, z, room.w, WALL_HEIGHT, wt, color); // north
      addBox(_buildingGroup, x, wy, z + room.h - wt, room.w, WALL_HEIGHT, wt, color); // south
      addBox(_buildingGroup, x, wy, z, wt, WALL_HEIGHT, room.h, color); // west
      addBox(_buildingGroup, x + room.w - wt, wy, z, wt, WALL_HEIGHT, room.h, color); // east
    });
  });

  const buildingHeight = plan.floors.length * FLOOR_HEIGHT;
  const footprint = Math.max(plotW, plotD);
  const dist = footprint * 1.3 + buildingHeight * 1.5;
  const elev = THREE.MathUtils.degToRad(32);
  const azim = THREE.MathUtils.degToRad(45);
  const cx = plotW / 2, cy = buildingHeight / 2, cz = plotD / 2;
  _camera.position.set(
    cx + dist * Math.cos(elev) * Math.cos(azim),
    cy + dist * Math.sin(elev),
    cz + dist * Math.cos(elev) * Math.sin(azim),
  );
  _controls.target.set(cx, cy, cz);
  _controls.update();
}

function disposePlan3D() {}
