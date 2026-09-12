// Three.js massing model. Reads the SAME plan JSON as the 2D renderer, so the
// two views cannot disagree. No geometry math here -- the engine owns that.
//
// Fixed in this rewrite:
//   * every failure mode now reports itself in the panel instead of leaving a
//     blank div (missing THREE, no WebGL, zero-size container, render throw)
//   * geometries and materials are disposed on every rebuild; they used to
//     leak on every keystroke because generate() fires on each input change
//   * walls are drawn once at half thickness per side instead of twice at full
//     thickness, which was causing z-fighting and 0.24 m shared walls
//   * balconies, terraces and parking are OPEN: slab and parapet, no roof or
//     walls. A balcony with four walls is just a room.
//   * columns are drawn, continuous through every floor (engine Rule D)

const ZONE_COLOR_3D = {
  social: 0x8fc7ff,
  service: 0xffbf80,
  private: 0x9fe0b8,
  circ: 0xc4a9f0,
};
const FLOOR_HEIGHT = 3.0;
const SLAB_THICKNESS = 0.3;
const WALL_HEIGHT = FLOOR_HEIGHT - SLAB_THICKNESS;
const WALL_EXT = 0.115; // exterior wall, drawn inward
const WALL_INT = 0.058; // interior: each side draws half, so the pair reads 0.116
const PARAPET_H = 1.0;
const COLUMN_SIZE = 0.23;

let _renderer, _scene, _camera, _controls, _buildingGroup, _container;
let _resizeHandler, _animId, _failed = false;

function showMessage(container, text) {
  container.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:center;height:100%;' +
    'padding:24px;text-align:center;font-size:13px;color:#6b7280;line-height:1.6">' +
    text + "</div>";
}

function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext &&
              (c.getContext("webgl") || c.getContext("experimental-webgl")));
  } catch (e) {
    return false;
  }
}

// Free GPU memory. THREE does not do this for you, and Group.remove() only
// detaches the mesh -- the geometry and material stay resident.
function clearGroup(group) {
  if (!group) return;
  for (let i = group.children.length - 1; i >= 0; i--) {
    const child = group.children[i];
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      if (Array.isArray(child.material)) child.material.forEach(m => m.dispose());
      else child.material.dispose();
    }
    group.remove(child);
  }
}

function ensureScene(container) {
  if (_renderer && _container === container) return true;

  if (typeof THREE === "undefined") {
    showMessage(container, "3D view unavailable: the Three.js library did not load.<br>" +
                           "Check the network connection to cdn.jsdelivr.net and reload.");
    return false;
  }
  if (!webglAvailable()) {
    showMessage(container, "3D view unavailable: this browser or machine has no WebGL context.<br>" +
                           "The 2D plan shows the same data.");
    return false;
  }
  // A hidden container reports 0x0 and yields an invisible canvas, which is
  // indistinguishable from "3D is broken". Bail out and let the caller retry.
  const w = container.clientWidth, h = container.clientHeight;
  if (w < 2 || h < 2) return false;

  _container = container;
  _scene = new THREE.Scene();
  _scene.background = new THREE.Color(0xeef1f5);
  _camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 2000);

  _renderer = new THREE.WebGLRenderer({ antialias: true });
  _renderer.setPixelRatio(window.devicePixelRatio || 1);
  _renderer.setSize(w, h);
  container.innerHTML = "";
  container.appendChild(_renderer.domElement);

  if (typeof THREE.OrbitControls === "function") {
    _controls = new THREE.OrbitControls(_camera, _renderer.domElement);
    _controls.enableDamping = true;
  } else {
    _controls = null; // still renders, just not orbitable
  }

  _scene.add(new THREE.AmbientLight(0xffffff, 0.72));
  const key = new THREE.DirectionalLight(0xffffff, 0.85);
  key.position.set(20, 30, 10);
  _scene.add(key);

  _buildingGroup = new THREE.Group();
  _scene.add(_buildingGroup);

  if (_resizeHandler) window.removeEventListener("resize", _resizeHandler);
  _resizeHandler = () => {
    if (!_container || !_renderer) return;
    const cw = _container.clientWidth, ch = _container.clientHeight;
    if (cw < 2 || ch < 2) return;
    _camera.aspect = cw / ch;
    _camera.updateProjectionMatrix();
    _renderer.setSize(cw, ch);
  };
  window.addEventListener("resize", _resizeHandler);

  if (_animId) cancelAnimationFrame(_animId);
  (function animate() {
    _animId = requestAnimationFrame(animate);
    if (_controls) _controls.update();
    _renderer.render(_scene, _camera);
  })();
  return true;
}

function addBox(group, x, y, z, w, h, d, color, opts) {
  if (w <= 0 || h <= 0 || d <= 0) return null;
  const geo = new THREE.BoxGeometry(w, h, d);
  const mat = new THREE.MeshLambertMaterial(
    Object.assign({ color }, opts && opts.material ? opts.material : {}));
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(x + w / 2, y + h / 2, z + d / 2);
  group.add(mesh);
  return mesh;
}

function renderPlan3D(container, plan) {
  if (_failed) return;
  if (!ensureScene(container)) {
    // zero-size container: try again once the tab has actually been shown
    if (container.clientWidth < 2 && plan) {
      requestAnimationFrame(() => renderPlan3D(container, plan));
    }
    return;
  }
  try {
    drawPlan(plan);
  } catch (err) {
    _failed = true;
    showMessage(container, "3D view failed to draw: " + (err && err.message ? err.message : err) +
                           "<br>The 2D plan is unaffected.");
    throw err;
  }
}

function drawPlan(plan) {
  clearGroup(_buildingGroup);
  if (!plan || !plan.ok) return;

  const envX = plan.setbacks.left, envY = plan.setbacks.front;
  const plotW = plan.plot_w_m, plotD = plan.plot_d_m;
  const fp = plan.footprint;

  // plot ground
  const groundGeo = new THREE.PlaneGeometry(plotW, plotD);
  const ground = new THREE.Mesh(groundGeo,
    new THREE.MeshLambertMaterial({ color: 0xd9dfc9, side: THREE.DoubleSide }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(plotW / 2, -0.02, plotD / 2);
  _buildingGroup.add(ground);

  // parking pad: open ground outside the building, not a room
  if (plan.parking) {
    const p = plan.parking;
    addBox(_buildingGroup, envX + p.x, -0.01, envY + p.y, p.w, 0.06, p.h, 0x9aa3ad);
  }

  const nFloors = plan.floors.length;

  plan.floors.forEach(floor => {
    const baseY = floor.index * FLOOR_HEIGHT;
    floor.rooms.forEach(room => {
      const x = envX + room.x, z = envY + room.y;
      const color = ZONE_COLOR_3D[room.zone] || 0xcccccc;

      addBox(_buildingGroup, x, baseY, z, room.w, SLAB_THICKNESS, room.h, 0x555b66);
      const wy = baseY + SLAB_THICKNESS;

      if (room.open) {
        // balcony / terrace: parapet only, open to the sky
        const t = 0.1, ph = PARAPET_H;
        addBox(_buildingGroup, x, wy, z, room.w, ph, t, color);
        addBox(_buildingGroup, x, wy, z + room.h - t, room.w, ph, t, color);
        addBox(_buildingGroup, x, wy, z, t, ph, room.h, color);
        addBox(_buildingGroup, x + room.w - t, wy, z, t, ph, room.h, color);
        return;
      }

      // Each edge is drawn once, inward. Interior edges use half thickness so
      // the pair of neighbouring rooms sums to one wall instead of two.
      const atFront = Math.abs(room.y - fp.y) < 0.02;
      const atRear = Math.abs(room.y + room.h - (fp.y + fp.h)) < 0.02;
      const atLeft = Math.abs(room.x - fp.x) < 0.02;
      const atRight = Math.abs(room.x + room.w - (fp.x + fp.w)) < 0.02;

      const tF = atFront ? WALL_EXT : WALL_INT;
      const tR = atRear ? WALL_EXT : WALL_INT;
      const tL = atLeft ? WALL_EXT : WALL_INT;
      const tRt = atRight ? WALL_EXT : WALL_INT;

      addBox(_buildingGroup, x, wy, z, room.w, WALL_HEIGHT, tF, color);
      addBox(_buildingGroup, x, wy, z + room.h - tR, room.w, WALL_HEIGHT, tR, color);
      addBox(_buildingGroup, x, wy, z, tL, WALL_HEIGHT, room.h, color);
      addBox(_buildingGroup, x + room.w - tRt, wy, z, tRt, WALL_HEIGHT, room.h, color);
    });
  });

  // Columns: one grid, continuous foundation to roof. Drawn last, in a dark
  // tone, so the structural frame reads through the massing.
  const colH = nFloors * FLOOR_HEIGHT;
  (plan.columns || []).forEach(c => {
    const cx = envX + c.x - COLUMN_SIZE / 2, cz = envY + c.y - COLUMN_SIZE / 2;
    addBox(_buildingGroup, cx, 0, cz, COLUMN_SIZE, colH, COLUMN_SIZE, 0x3b4250);
  });

  // frame the plot
  const buildingHeight = nFloors * FLOOR_HEIGHT;
  const dist = Math.max(plotW, plotD) * 1.3 + buildingHeight * 1.5;
  const elev = 32 * Math.PI / 180, azim = 45 * Math.PI / 180;
  const cx = plotW / 2, cy = buildingHeight / 2, cz = plotD / 2;
  _camera.position.set(
    cx + dist * Math.cos(elev) * Math.cos(azim),
    cy + dist * Math.sin(elev),
    cz + dist * Math.cos(elev) * Math.sin(azim));
  _camera.far = Math.max(2000, dist * 4);
  _camera.updateProjectionMatrix();
  if (_controls) {
    _controls.target.set(cx, cy, cz);
    _controls.update();
  } else {
    _camera.lookAt(cx, cy, cz);
  }
}

function disposePlan3D() {
  clearGroup(_buildingGroup);
  if (_animId) cancelAnimationFrame(_animId);
  if (_resizeHandler) window.removeEventListener("resize", _resizeHandler);
  if (_renderer) _renderer.dispose();
  _renderer = _scene = _camera = _controls = _buildingGroup = _container = null;
  _animId = _resizeHandler = null;
}
