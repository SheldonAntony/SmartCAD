// SVG 2D renderer. Draws only from the plan JSON -- no geometry math here.
//
// Fixed in this rewrite:
//   * door swings follow the engine's `swing` field instead of always arcing
//     toward +x/+y, which used to draw them through the neighbouring room
//   * exterior walls are heavier than interior ones. Previously every room
//     stroked its own border, so shared interior walls were double-struck and
//     read thicker than the outside of the building -- backwards.
//   * the building footprint, the open ground around it, the parking bay and
//     the column grid are all drawn, because they are all real now

const ZONE_COLORS = {
  social: "#cfe8ff", service: "#ffe4c4", private: "#d9f2e0", circ: "#e3d9f5",
};
const ZONE_STROKE = {
  social: "#5b9bd5", service: "#d98a3d", private: "#4caf7d", circ: "#8b6fc9",
};
const FURNITURE = {
  master_bedroom: "bed", bedroom: "bed", living: "sofa",
  bathroom: "toilet", kitchen: "stove",
};
const PADDING_M = 1.5;

function svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function drawFurniture(group, type, x, y, w, h) {
  const glyph = FURNITURE[type];
  if (!glyph) return;
  const cx = x + w / 2, cy = y + h / 2, m = Math.min(w, h);
  const g = svgEl("g", { class: "furniture", opacity: 0.5 });

  if (glyph === "bed") {
    const bw = m * 0.62, bh = bw * 1.3;
    g.appendChild(svgEl("rect", { x: cx - bw / 2, y: cy - bh / 2, width: bw, height: bh,
      rx: 0.06, fill: "#8b5cf6", stroke: "#5b21b6", "stroke-width": 0.02 }));
    g.appendChild(svgEl("rect", { x: cx - bw / 2, y: cy - bh / 2, width: bw, height: bh * 0.25,
      rx: 0.04, fill: "#c4b5fd" }));
  } else if (glyph === "sofa") {
    const sw = m * 0.75, sh = sw * 0.4;
    g.appendChild(svgEl("rect", { x: cx - sw / 2, y: cy - sh / 2, width: sw, height: sh,
      rx: 0.05, fill: "#f59e0b", stroke: "#b45309", "stroke-width": 0.02 }));
  } else if (glyph === "toilet") {
    g.appendChild(svgEl("circle", { cx: cx, cy: cy - m * 0.15, r: m * 0.17,
      fill: "#fff", stroke: "#94a3b8", "stroke-width": 0.02 }));
    g.appendChild(svgEl("rect", { x: cx - m * 0.14, y: cy + m * 0.02, width: m * 0.28,
      height: m * 0.17, fill: "#fff", stroke: "#94a3b8", "stroke-width": 0.02 }));
  } else if (glyph === "stove") {
    const sw = m * 0.36;
    g.appendChild(svgEl("rect", { x: cx - sw / 2, y: cy - sw / 2, width: sw, height: sw, fill: "#334155" }));
    [[-0.2, -0.2], [0.2, -0.2], [-0.2, 0.2], [0.2, 0.2]].forEach(([dx, dy]) => {
      g.appendChild(svgEl("circle", { cx: cx + dx * sw, cy: cy + dy * sw, r: sw * 0.12, fill: "#0f172a" }));
    });
  }
  group.appendChild(g);
}

function drawCar(group, x, y, w, h) {
  const cx = x + w / 2, cy = y + h / 2;
  const cw = Math.min(w * 0.7, 1.8), ch = Math.min(h * 0.8, 4.2);
  group.appendChild(svgEl("rect", { x: cx - cw / 2, y: cy - ch / 2, width: cw, height: ch,
    rx: 0.3, fill: "#60a5fa", stroke: "#1d4ed8", "stroke-width": 0.03, opacity: 0.6 }));
}

function renderPlan2D(svg, plan, floorIndex, tooltip) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!plan || !plan.ok) return;

  const floor = plan.floors.find(f => f.index === floorIndex) || plan.floors[0];
  const plotW = plan.plot_w_m, plotD = plan.plot_d_m;
  const sb = plan.setbacks, fp = plan.footprint;

  svg.setAttribute("viewBox",
    `0 0 ${plotW + PADDING_M * 2} ${plotD + PADDING_M * 2 + 1.2}`);
  const originX = PADDING_M, originY = PADDING_M + 1.2;
  const envX = originX + sb.left, envY = originY + sb.front;

  const root = svgEl("g", {});
  svg.appendChild(root);

  // 1. plot boundary, setback line, then the actual building footprint
  root.appendChild(svgEl("rect", { x: originX, y: originY, width: plotW, height: plotD,
    fill: "#eef2e6", stroke: "#9ca3af", "stroke-width": 0.05, "stroke-dasharray": "0.3,0.2" }));
  root.appendChild(svgEl("rect", { x: envX, y: envY,
    width: plan.envelope_w_m, height: plan.envelope_d_m,
    fill: "none", stroke: "#b6bcc6", "stroke-width": 0.03, "stroke-dasharray": "0.15,0.15" }));
  root.appendChild(svgEl("rect", { x: envX + fp.x, y: envY + fp.y, width: fp.w, height: fp.h,
    fill: "#fafafa", stroke: "#333", "stroke-width": 0.12 }));

  // 2. parking: open ground between the house and the street
  if (plan.parking) {
    const p = plan.parking;
    const px = envX + p.x, py = envY + p.y;
    root.appendChild(svgEl("rect", { x: px, y: py, width: p.w, height: p.h,
      fill: "#e5e7eb", stroke: "#6b7280", "stroke-width": 0.05, "stroke-dasharray": "0.25,0.15" }));
    drawCar(root, px, py, p.w, p.h);
    const pl = svgEl("text", { x: px + p.w / 2, y: py + p.h + 0.35, "text-anchor": "middle",
      "font-size": 0.28, fill: "#4b5563" });
    pl.textContent = `Parking ${p.w.toFixed(1)}x${p.h.toFixed(1)}m`;
    root.appendChild(pl);
  }

  // 3. structural grid lines, under the rooms
  const grid = plan.grid || { x: [], y: [] };
  grid.x.forEach(gxv => root.appendChild(svgEl("line", {
    x1: envX + gxv, y1: envY + fp.y, x2: envX + gxv, y2: envY + fp.y + fp.h,
    stroke: "#c9ced8", "stroke-width": 0.02, "stroke-dasharray": "0.4,0.25" })));
  grid.y.forEach(gyv => root.appendChild(svgEl("line", {
    x1: envX + fp.x, y1: envY + gyv, x2: envX + fp.x + fp.w, y2: envY + gyv,
    stroke: "#c9ced8", "stroke-width": 0.02, "stroke-dasharray": "0.4,0.25" })));

  // 4. rooms. Interior walls are drawn thin; the footprint outline above
  //    already carries the heavy exterior line.
  floor.rooms.forEach(room => {
    const rx = envX + room.x, ry = envY + room.y;
    root.appendChild(svgEl("rect", {
      x: rx, y: ry, width: room.w, height: room.h,
      fill: room.open ? "#f3f7ff" : (ZONE_COLORS[room.zone] || "#eee"),
      stroke: ZONE_STROKE[room.zone] || "#666",
      "stroke-width": 0.05,
      "stroke-dasharray": room.open ? "0.2,0.12" : "none",
      "data-room-id": room.id, class: "room-rect",
    }));

    drawFurniture(root, room.type, rx, ry, room.w, room.h);

    const fontSize = Math.max(0.16, Math.min(room.w, room.h) * 0.13);
    const label = svgEl("text", { x: rx + room.w / 2, y: ry + room.h / 2 - fontSize * 0.3,
      "text-anchor": "middle", "font-size": fontSize, fill: "#1f2937",
      "font-weight": "600", style: "pointer-events:none" });
    label.textContent = room.name;
    root.appendChild(label);

    const dims = svgEl("text", { x: rx + room.w / 2, y: ry + room.h / 2 + fontSize * 0.9,
      "text-anchor": "middle", "font-size": fontSize * 0.75, fill: "#4b5563",
      style: "pointer-events:none" });
    dims.textContent = `${room.area.toFixed(1)} m2 (${room.w.toFixed(2)}x${room.h.toFixed(2)})`;
    root.appendChild(dims);

    // flag anything the validator will complain about, right on the drawing
    if ((room.exterior_required && !room.exterior) || !room.reachable) {
      root.appendChild(svgEl("rect", { x: rx + 0.06, y: ry + 0.06,
        width: Math.max(0.1, room.w - 0.12), height: Math.max(0.1, room.h - 0.12),
        fill: "none", stroke: "#dc2626", "stroke-width": 0.06, "stroke-dasharray": "0.2,0.12" }));
    }

    const hit = svgEl("rect", { x: rx, y: ry, width: room.w, height: room.h,
      fill: "transparent", style: "cursor:pointer" });
    hit.addEventListener("mousemove", ev => {
      tooltip.style.display = "block";
      tooltip.style.left = (ev.offsetX + 12) + "px";
      tooltip.style.top = (ev.offsetY + 12) + "px";
      tooltip.textContent =
        `${room.name}: ${room.w.toFixed(2)} x ${room.h.toFixed(2)} m = ${room.area.toFixed(2)} m2 ` +
        `(min ${room.min_area}) | ${room.exterior ? "external wall" : "internal"}` +
        `${room.entered_from ? " | entered from " + room.entered_from.split("_")[1] : ""}`;
    });
    hit.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
    root.appendChild(hit);
  });

  // 5. windows -- white gap plus a glazing line, on exterior walls
  (floor.windows || []).forEach(win => {
    const x1 = envX + win.x, y1 = envY + win.y;
    const x2 = win.orientation === "v" ? x1 : x1 + win.length;
    const y2 = win.orientation === "v" ? y1 + win.length : y1;
    root.appendChild(svgEl("line", { x1, y1, x2, y2, stroke: "#ffffff", "stroke-width": 0.14 }));
    root.appendChild(svgEl("line", { x1, y1, x2, y2, stroke: "#38bdf8", "stroke-width": 0.035 }));
  });

  // 6. doors -- leaf plus swing arc, on the side the engine says it opens
  (floor.doors || []).forEach(door => {
    const g = svgEl("g", {});
    const s = door.swing >= 0 ? 1 : -1;
    const stroke = door.kind === "front" ? "#b45309" : "#92400e";
    const lw = door.kind === "front" ? 0.05 : 0.035;
    if (door.orientation === "v") {
      const x = envX + door.x, y0 = envY + door.y, y1 = y0 + door.length;
      g.appendChild(svgEl("line", { x1: x, y1: y0, x2: x, y2: y1, stroke: "#fff", "stroke-width": 0.14 }));
      g.appendChild(svgEl("path", {
        d: `M ${x} ${y1} A ${door.length} ${door.length} 0 0 ${s > 0 ? 0 : 1} ${x + s * door.length} ${y0}`,
        fill: "none", stroke, "stroke-width": 0.025, opacity: 0.8 }));
      g.appendChild(svgEl("line", { x1: x, y1: y0, x2: x + s * door.length, y2: y0, stroke, "stroke-width": lw }));
    } else {
      const y = envY + door.y, x0 = envX + door.x, x1 = x0 + door.length;
      g.appendChild(svgEl("line", { x1: x0, y1: y, x2: x1, y2: y, stroke: "#fff", "stroke-width": 0.14 }));
      g.appendChild(svgEl("path", {
        d: `M ${x1} ${y} A ${door.length} ${door.length} 0 0 ${s > 0 ? 1 : 0} ${x0} ${y + s * door.length}`,
        fill: "none", stroke, "stroke-width": 0.025, opacity: 0.8 }));
      g.appendChild(svgEl("line", { x1: x0, y1: y, x2: x0, y2: y + s * door.length, stroke, "stroke-width": lw }));
    }
    root.appendChild(g);
  });

  // 7. adjacency links
  (floor.adjacency_lines || []).forEach(link => {
    root.appendChild(svgEl("line", {
      x1: envX + link.ax, y1: envY + link.ay, x2: envX + link.bx, y2: envY + link.by,
      stroke: "#22c55e", "stroke-width": 0.04, "stroke-dasharray": "0.15,0.1", opacity: 0.75 }));
  });

  // 8. columns, on top -- the structural frame is the thing that must not move
  (plan.columns || []).forEach(c => {
    root.appendChild(svgEl("rect", {
      x: envX + c.x - 0.115, y: envY + c.y - 0.115, width: 0.23, height: 0.23,
      fill: "#1f2937", stroke: "#000", "stroke-width": 0.01 }));
  });

  // 9. dimension lines + a street marker, so "front" is unambiguous
  const dimY = originY - 0.5;
  root.appendChild(svgEl("line", { x1: originX, y1: dimY, x2: originX + plotW, y2: dimY,
    stroke: "#374151", "stroke-width": 0.02 }));
  [originX, originX + plotW].forEach(x => root.appendChild(svgEl("line", {
    x1: x, y1: dimY - 0.08, x2: x, y2: dimY + 0.08, stroke: "#374151", "stroke-width": 0.02 })));
  const topLabel = svgEl("text", { x: originX + plotW / 2, y: dimY - 0.15,
    "text-anchor": "middle", "font-size": 0.32, fill: "#374151" });
  topLabel.textContent = `${plotW.toFixed(2)} m  —  STREET SIDE`;
  root.appendChild(topLabel);

  const dimX = originX - 0.5;
  root.appendChild(svgEl("line", { x1: dimX, y1: originY, x2: dimX, y2: originY + plotD,
    stroke: "#374151", "stroke-width": 0.02 }));
  const leftLabel = svgEl("text", { x: dimX - 0.15, y: originY + plotD / 2,
    "text-anchor": "middle", "font-size": 0.32, fill: "#374151",
    transform: `rotate(-90 ${dimX - 0.15} ${originY + plotD / 2})` });
  leftLabel.textContent = `${plotD.toFixed(2)} m`;
  root.appendChild(leftLabel);
}
