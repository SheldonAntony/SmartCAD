// SVG 2D renderer. Draws only from the plan JSON -- no geometry math here
// (rule 2: all geometry lives in the Python engine).

const ZONE_COLORS = {
  social: "#cfe8ff",
  service: "#ffe4c4",
  private: "#d9f2e0",
  circ: "#e3d9f5",
};
const ZONE_STROKE = {
  social: "#5b9bd5",
  service: "#d98a3d",
  private: "#4caf7d",
  circ: "#8b6fc9",
};

const FURNITURE = {
  master_bedroom: "bed",
  bedroom: "bed",
  living: "sofa",
  parking: "car",
  bathroom: "toilet",
  kitchen: "stove",
};

const PADDING_M = 1.5; // drawing padding around the plot boundary, in metres

function svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function drawFurniture(group, type, x, y, w, h, scale) {
  const glyph = FURNITURE[type];
  if (!glyph) return;
  const cx = x + w / 2, cy = y + h / 2;
  const g = svgEl("g", { class: "furniture", opacity: 0.55 });

  if (glyph === "bed") {
    const bw = Math.min(w, h) * 0.7, bh = bw * 1.3;
    g.appendChild(svgEl("rect", { x: cx - bw / 2, y: cy - bh / 2, width: bw, height: bh, rx: 0.06, fill: "#8b5cf6", stroke: "#5b21b6", "stroke-width": 0.02 }));
    g.appendChild(svgEl("rect", { x: cx - bw / 2, y: cy - bh / 2, width: bw, height: bh * 0.25, rx: 0.04, fill: "#c4b5fd" }));
  } else if (glyph === "sofa") {
    const sw = Math.min(w, h) * 0.8, sh = sw * 0.4;
    g.appendChild(svgEl("rect", { x: cx - sw / 2, y: cy - sh / 2, width: sw, height: sh, rx: 0.05, fill: "#f59e0b", stroke: "#b45309", "stroke-width": 0.02 }));
  } else if (glyph === "car") {
    const cw = Math.min(w * 0.7, 2.2), ch = Math.min(h * 0.7, 4.6);
    g.appendChild(svgEl("rect", { x: cx - cw / 2, y: cy - ch / 2, width: cw, height: ch, rx: 0.3, fill: "#60a5fa", stroke: "#1d4ed8", "stroke-width": 0.03 }));
  } else if (glyph === "toilet") {
    g.appendChild(svgEl("circle", { cx: cx, cy: cy - Math.min(w, h) * 0.15, r: Math.min(w, h) * 0.18, fill: "#fff", stroke: "#94a3b8", "stroke-width": 0.02 }));
    g.appendChild(svgEl("rect", { x: cx - Math.min(w, h) * 0.15, y: cy + Math.min(w, h) * 0.02, width: Math.min(w, h) * 0.3, height: Math.min(w, h) * 0.18, fill: "#fff", stroke: "#94a3b8", "stroke-width": 0.02 }));
  } else if (glyph === "stove") {
    const sw = Math.min(w, h) * 0.4;
    g.appendChild(svgEl("rect", { x: cx - sw / 2, y: cy - sw / 2, width: sw, height: sw, fill: "#334155" }));
    [[-0.2, -0.2], [0.2, -0.2], [-0.2, 0.2], [0.2, 0.2]].forEach(([dx, dy]) => {
      g.appendChild(svgEl("circle", { cx: cx + dx * sw, cy: cy + dy * sw, r: sw * 0.12, fill: "#0f172a" }));
    });
  }
  group.appendChild(g);
}

function renderPlan2D(svg, plan, floorIndex, tooltip) {
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const floor = plan.floors.find(f => f.index === floorIndex) || plan.floors[0];
  const plotW = plan.plot_w_m, plotD = plan.plot_d_m;
  const bw = plan.envelope_w_m, bd = plan.envelope_d_m;
  const sb = plan.setbacks;

  const totalW = plotW + PADDING_M * 2;
  const totalH = plotD + PADDING_M * 2 + 1.2; // extra headroom for top dimension line
  svg.setAttribute("viewBox", `0 0 ${totalW} ${totalH}`);

  const originX = PADDING_M;
  const originY = PADDING_M + 1.2;

  // building envelope sits inset from the plot by the setbacks
  const envX = originX + sb.left, envY = originY + sb.front;

  const root = svgEl("g", {});
  svg.appendChild(root);

  // 1. plot boundary (dashed) + setback line (dotted) + envelope
  root.appendChild(svgEl("rect", {
    x: originX, y: originY, width: plotW, height: plotD,
    fill: "none", stroke: "#9ca3af", "stroke-width": 0.05, "stroke-dasharray": "0.3,0.2",
  }));
  root.appendChild(svgEl("rect", {
    x: envX, y: envY, width: bw, height: bd,
    fill: "#fafafa", stroke: "#333", "stroke-width": 0.06,
  }));

  // 2 & 3. room rectangles + labels, 4. thick walls
  const roomById = {};
  floor.rooms.forEach(r => { roomById[r.id] = r; });

  floor.rooms.forEach(room => {
    const rx = envX + room.x, ry = envY + room.y;
    roomById[room.id]._screen = { x: rx, y: ry, w: room.w, h: room.h };

    root.appendChild(svgEl("rect", {
      x: rx, y: ry, width: room.w, height: room.h,
      fill: ZONE_COLORS[room.zone] || "#eee",
      stroke: ZONE_STROKE[room.zone] || "#666",
      "stroke-width": 0.09, // wall thickness, not a hairline
      "data-room-id": room.id,
      class: "room-rect",
    }));

    drawFurniture(root, room.type, rx, ry, room.w, room.h);

    const fontSize = Math.max(0.16, Math.min(room.w, room.h) * 0.13);
    const label = svgEl("text", {
      x: rx + room.w / 2, y: ry + room.h / 2 - fontSize * 0.3,
      "text-anchor": "middle", "font-size": fontSize, fill: "#1f2937", "font-weight": "600",
      style: "pointer-events:none",
    });
    label.textContent = room.name;
    root.appendChild(label);

    const dims = svgEl("text", {
      x: rx + room.w / 2, y: ry + room.h / 2 + fontSize * 0.9,
      "text-anchor": "middle", "font-size": fontSize * 0.75, fill: "#4b5563",
      style: "pointer-events:none",
    });
    dims.textContent = `${room.area.toFixed(1)} m2 (${room.w.toFixed(2)}x${room.h.toFixed(2)})`;
    root.appendChild(dims);

    // hover tooltip
    const hitRect = svgEl("rect", {
      x: rx, y: ry, width: room.w, height: room.h, fill: "transparent", style: "cursor:pointer",
    });
    hitRect.addEventListener("mousemove", (ev) => {
      tooltip.style.display = "block";
      tooltip.style.left = (ev.offsetX + 12) + "px";
      tooltip.style.top = (ev.offsetY + 12) + "px";
      tooltip.textContent = `${room.name}: ${room.w.toFixed(2)}m x ${room.h.toFixed(2)}m = ${room.area.toFixed(2)} m2 (min ${room.min_area} m2)`;
    });
    hitRect.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
    root.appendChild(hitRect);
  });

  // 6. windows -- thin white gaps on exterior walls
  (floor.windows || []).forEach(win => {
    if (win.orientation === "v") {
      root.appendChild(svgEl("line", {
        x1: envX + win.x, y1: envY + win.y, x2: envX + win.x, y2: envY + win.y + win.length,
        stroke: "#ffffff", "stroke-width": 0.11,
      }));
      root.appendChild(svgEl("line", {
        x1: envX + win.x, y1: envY + win.y, x2: envX + win.x, y2: envY + win.y + win.length,
        stroke: "#38bdf8", "stroke-width": 0.03,
      }));
    } else {
      root.appendChild(svgEl("line", {
        x1: envX + win.x, y1: envY + win.y, x2: envX + win.x + win.length, y2: envY + win.y,
        stroke: "#ffffff", "stroke-width": 0.11,
      }));
      root.appendChild(svgEl("line", {
        x1: envX + win.x, y1: envY + win.y, x2: envX + win.x + win.length, y2: envY + win.y,
        stroke: "#38bdf8", "stroke-width": 0.03,
      }));
    }
  });

  // 5. doors -- small arc on the best shared-wall segment
  (floor.doors || []).forEach(door => {
    const gap = document.createElementNS("http://www.w3.org/2000/svg", "g");
    if (door.orientation === "v") {
      const x = envX + door.x, y0 = envY + door.y, y1 = y0 + door.length;
      gap.appendChild(svgEl("line", { x1: x, y1: y0, x2: x, y2: y1, stroke: "#fff", "stroke-width": 0.11 }));
      gap.appendChild(svgEl("path", {
        d: `M ${x} ${y0} A ${door.length} ${door.length} 0 0 1 ${x + door.length} ${y0 + door.length}`,
        fill: "none", stroke: "#92400e", "stroke-width": 0.025,
      }));
      gap.appendChild(svgEl("line", { x1: x, y1: y0, x2: x + door.length, y2: y0, stroke: "#92400e", "stroke-width": 0.04 }));
    } else {
      const y = envY + door.y, x0 = envX + door.x, x1 = x0 + door.length;
      gap.appendChild(svgEl("line", { x1: x0, y1: y, x2: x1, y2: y, stroke: "#fff", "stroke-width": 0.11 }));
      gap.appendChild(svgEl("path", {
        d: `M ${x0} ${y} A ${door.length} ${door.length} 0 0 1 ${x0 + door.length} ${y + door.length}`,
        fill: "none", stroke: "#92400e", "stroke-width": 0.025,
      }));
      gap.appendChild(svgEl("line", { x1: x0, y1: y, x2: x0, y2: y + door.length, stroke: "#92400e", "stroke-width": 0.04 }));
    }
    root.appendChild(gap);
  });

  // 9. green adjacency lines
  (floor.adjacency_lines || []).forEach(link => {
    root.appendChild(svgEl("line", {
      x1: envX + link.ax, y1: envY + link.ay, x2: envX + link.bx, y2: envY + link.by,
      stroke: "#22c55e", "stroke-width": 0.045, "stroke-dasharray": "0.15,0.1", opacity: 0.85,
    }));
  });

  // 7. dimension lines along outer edges
  const dimY = originY - 0.5;
  root.appendChild(svgEl("line", { x1: originX, y1: dimY, x2: originX + plotW, y2: dimY, stroke: "#374151", "stroke-width": 0.02 }));
  [originX, originX + plotW].forEach(x => {
    root.appendChild(svgEl("line", { x1: x, y1: dimY - 0.08, x2: x, y2: dimY + 0.08, stroke: "#374151", "stroke-width": 0.02 }));
  });
  const dimLabelTop = svgEl("text", { x: originX + plotW / 2, y: dimY - 0.15, "text-anchor": "middle", "font-size": 0.32, fill: "#374151" });
  dimLabelTop.textContent = `${plotW.toFixed(2)} m`;
  root.appendChild(dimLabelTop);

  const dimX = originX - 0.5;
  root.appendChild(svgEl("line", { x1: dimX, y1: originY, x2: dimX, y2: originY + plotD, stroke: "#374151", "stroke-width": 0.02 }));
  const dimLabelLeft = svgEl("text", {
    x: dimX - 0.15, y: originY + plotD / 2, "text-anchor": "middle", "font-size": 0.32, fill: "#374151",
    transform: `rotate(-90 ${dimX - 0.15} ${originY + plotD / 2})`,
  });
  dimLabelLeft.textContent = `${plotD.toFixed(2)} m`;
  root.appendChild(dimLabelLeft);
}
