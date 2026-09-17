/**
 * The 3D room view: geometry, a draggable field slice, and a hover readout.
 *
 * Coordinates are OpenFOAM's, unchanged -- x along the airflow, y across, z up.
 * The camera's up vector is set to +z so the room is not silently rotated on
 * its way to the screen; an engineer reading a dimension off this view has to
 * be reading the same axes as the case file.
 */

import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { buildLut } from './colormaps.js';

const AXIS_NAMES = ['x', 'y', 'z'];

export class RoomScene {
  /**
   * @param {HTMLElement} container
   * @param {import('./data.js').Results} results
   */
  constructor(container, results) {
    this.container = container;
    this.results = results;
    this.mode = 'light';
    this.sliceAxis = 2;
    this.sliceIndex = Math.floor(results.nz / 2);
    this.fieldName = 'T';
    this.scale = null;
    this.lutKind = 'diverging';

    this.scene = new THREE.Scene();
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(this.renderer.domElement);

    const [sx, sy, sz] = results.size;
    const [ox, oy, oz] = results.origin;
    const centre = new THREE.Vector3(ox + sx / 2, oy + sy / 2, oz + sz / 2);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.05, 500);
    this.camera.up.set(0, 0, 1);

    // Frame the room from its bounding sphere so any room size arrives on
    // screen at the same apparent size, with a margin for the rack labels.
    const radius = Math.hypot(sx, sy, sz) / 2;
    const distance = (radius / Math.sin((this.camera.fov * Math.PI) / 360)) * 1.15;
    const direction = new THREE.Vector3(-0.62, -0.62, 0.48).normalize();
    this.camera.position.copy(centre).addScaledVector(direction, distance);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.target.copy(centre);

    this.scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(-sx, -sy * 1.5, sz * 3);
    this.scene.add(key);

    this.#buildRoom();
    this.#buildZones();
    this.#buildSlice();

    this.overlay = document.createElement('div');
    this.overlay.className = 'scene-overlay';
    container.appendChild(this.overlay);
    this.#buildLabels();

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.onProbe = null;
    this.renderer.domElement.addEventListener('pointermove', (e) => this.#probe(e));
    this.renderer.domElement.addEventListener('pointerleave', () => this.onProbe?.(null));

    this.resize();
    window.addEventListener('resize', () => this.resize());
    this.#animate();
  }

  // --- static geometry ------------------------------------------------------

  #buildRoom() {
    const [sx, sy, sz] = this.results.size;
    const [ox, oy, oz] = this.results.origin;
    const centre = this.controls.target;

    const box = new THREE.BoxGeometry(sx, sy, sz).translate(...centre.toArray());
    this.roomEdges = new THREE.LineSegments(
      new THREE.EdgesGeometry(box),
      new THREE.LineBasicMaterial({ transparent: true, opacity: 0.85 }),
    );
    this.scene.add(this.roomEdges);

    // A floor grid gives the eye a sense of scale without adding data-weight ink.
    this.grid = new THREE.GridHelper(Math.max(sx, sy), Math.round(Math.max(sx, sy)));
    this.grid.rotation.x = Math.PI / 2;
    this.grid.position.set(ox + sx / 2, oy + sy / 2, oz + 0.002);
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.5;
    this.scene.add(this.grid);

    // The supply patch, so it is obvious which wall is blowing.
    this.inletMesh = null;
    const inlet = this.results.meta.geometry.inlet_patch;
    if (inlet) {
      const quad = quadFor(0, ox, [ox, oy, oz], [sx, sy, sz]);
      const mesh = new THREE.Mesh(
        quad,
        new THREE.MeshBasicMaterial({
          transparent: true,
          opacity: 0.22,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      this.inletMesh = mesh;
      this.scene.add(mesh);
    }
  }

  #buildZones() {
    this.zoneMeshes = [];
    for (const zone of this.results.meta.geometry.zones) {
      const size = zone.hi.map((h, i) => h - zone.lo[i]);
      const centre = zone.lo.map((l, i) => l + size[i] / 2);
      const geometry = new THREE.BoxGeometry(...size).translate(...centre);
      const mesh = new THREE.Mesh(
        geometry,
        new THREE.MeshLambertMaterial({ transparent: true, opacity: 0.5 }),
      );
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry),
        new THREE.LineBasicMaterial(),
      );
      mesh.userData.zone = zone;
      this.scene.add(mesh, edges);
      this.zoneMeshes.push({ mesh, edges, zone, centre });
    }
  }

  #buildSlice() {
    this.sliceTexture = null;
    this.sliceMesh = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, transparent: true }),
    );
    this.scene.add(this.sliceMesh);
  }

  #buildLabels() {
    this.labels = this.zoneMeshes.map(({ zone }) => {
      // Anchor above the zone, not at its centre: a label drawn over the rack
      // hides the very geometry it is naming.
      const anchor = [
        (zone.lo[0] + zone.hi[0]) / 2,
        (zone.lo[1] + zone.hi[1]) / 2,
        zone.hi[2],
      ];
      const element = document.createElement('div');
      element.className = 'scene-label';
      element.innerHTML =
        `<span class="scene-label-name">${zone.name}</span>` +
        `<span class="scene-label-load">${(zone.load_w / 1000).toFixed(1)} kW</span>`;
      this.overlay.appendChild(element);
      return {
        element,
        position: new THREE.Vector3(...anchor),
        offset: 18,
      };
    });
  }

  // --- theming --------------------------------------------------------------

  /** Re-read colors from CSS so the scene follows the page's light/dark theme. */
  applyTheme(mode, tokens) {
    this.mode = mode;
    this.scene.background = null;
    this.roomEdges.material.color.set(tokens.axis);
    this.grid.material.color.set(tokens.grid);
    if (this.inletMesh) this.inletMesh.material.color.set(tokens.accent);
    for (const { mesh, edges } of this.zoneMeshes) {
      mesh.material.color.set(tokens.zoneFill);
      edges.material.color.set(tokens.zoneEdge);
    }
    this.setField(this.fieldName, this.scaleOptions);
  }

  // --- field & slice --------------------------------------------------------

  /**
   * @param {string} fieldName
   * @param {import('./colormaps.js').Scale} scale
   * @param {'diverging'|'sequential'} kind
   */
  setField(fieldName, scale, kind = this.lutKind) {
    this.fieldName = fieldName;
    this.scaleOptions = scale;
    this.lutKind = kind;
    this.lut = buildLut(kind, this.mode);
    this.updateSlice();
  }

  setSlice(axis, index) {
    this.sliceAxis = axis;
    this.sliceIndex = index;
    this.updateSlice();
  }

  updateSlice() {
    if (!this.scaleOptions) return;
    const { results, sliceAxis: axis, sliceIndex: at } = this;
    const { values, width, height } = results.slice(this.fieldName, axis, at);

    const rgba = new Uint8Array(width * height * 4);
    for (let i = 0; i < values.length; i += 1) {
      const t = this.scaleOptions.position(values[i]);
      const step = Math.round(t * 255) * 3;
      rgba[i * 4] = this.lut[step];
      rgba[i * 4 + 1] = this.lut[step + 1];
      rgba[i * 4 + 2] = this.lut[step + 2];
      rgba[i * 4 + 3] = 255;
    }

    this.sliceTexture?.dispose();
    this.sliceTexture = new THREE.DataTexture(rgba, width, height, THREE.RGBAFormat);
    // Linear filtering: the underlying field is continuous, so smoothing between
    // cell centres is honest -- it is the same interpolation the solver assumes.
    this.sliceTexture.minFilter = THREE.LinearFilter;
    this.sliceTexture.magFilter = THREE.LinearFilter;
    this.sliceTexture.needsUpdate = true;

    this.sliceMesh.material.map = this.sliceTexture;
    this.sliceMesh.material.needsUpdate = true;
    this.sliceMesh.geometry.dispose();
    this.sliceMesh.geometry = quadFor(
      axis,
      results.coordinate(axis, at),
      results.origin,
      results.size,
    );
  }

  /** Where the slice plane currently sits, in metres. */
  slicePosition() {
    return this.results.coordinate(this.sliceAxis, this.sliceIndex);
  }

  sliceLabel() {
    return `${AXIS_NAMES[this.sliceAxis]} = ${this.slicePosition().toFixed(2)} m`;
  }

  // --- interaction ----------------------------------------------------------

  #probe(event) {
    if (!this.onProbe) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const [hit] = this.raycaster.intersectObject(this.sliceMesh);
    if (!hit) {
      this.onProbe(null);
      return;
    }
    const { point } = hit;
    const cell = [
      this.results.cellAt(0, point.x),
      this.results.cellAt(1, point.y),
      this.results.cellAt(2, point.z),
    ];
    cell[this.sliceAxis] = this.sliceIndex;
    this.onProbe({
      point: [point.x, point.y, point.z],
      cell,
      values: {
        T: this.results.valueAt('T', ...cell),
        speed: this.results.valueAt('speed', ...cell),
        Ux: this.results.valueAt('Ux', ...cell),
        Uy: this.results.valueAt('Uy', ...cell),
        Uz: this.results.valueAt('Uz', ...cell),
      },
    });
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  #animate() {
    const tick = () => {
      requestAnimationFrame(tick);
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      this.#positionLabels();
    };
    tick();
  }

  /**
   * Place rack labels, dropping any that would collide with one already placed.
   *
   * A row of racks projects to a tight cluster from most angles, and stacked
   * labels are worse than missing ones: they hide each other's text and the
   * geometry behind them. Nearer racks win, since they are the ones the viewer
   * is looking at, and orbiting reveals the rest.
   */
  #positionLabels() {
    const { clientWidth: w, clientHeight: h } = this.container;
    const placed = [];

    const candidates = this.labels
      .map((label) => ({ label, projected: label.position.clone().project(this.camera) }))
      .sort((a, b) => a.projected.z - b.projected.z); // nearest first

    for (const { label, projected } of candidates) {
      const { element, offset } = label;
      if (projected.z >= 1) {
        element.style.display = 'none';
        continue;
      }
      const x = ((projected.x + 1) / 2) * w;
      const y = ((1 - projected.y) / 2) * h - offset;
      element.style.display = 'flex';
      element.style.transform = `translate(-50%, -100%) translate(${x}px, ${y}px)`;

      const half = element.offsetWidth / 2 || 32;
      const height = element.offsetHeight || 30;
      const box = { x0: x - half, x1: x + half, y0: y - height, y1: y };
      if (placed.some((other) => overlaps(box, other))) {
        element.style.display = 'none';
        continue;
      }
      placed.push(box);
    }
  }
}

/**
 * A single quad lying in the plane `axis = position`, with UVs mapped so that
 * texel (0,0) is the low corner of both in-plane axes -- matching the row order
 * of Results.slice(). Built explicitly rather than by rotating a PlaneGeometry,
 * because getting that rotation subtly wrong mirrors the field without any
 * visible sign that it happened.
 */
function overlaps(a, b) {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
}

function quadFor(axis, position, origin, size) {
  const [ox, oy, oz] = origin;
  const [sx, sy, sz] = size;
  const corners =
    axis === 0
      ? [
          [position, oy, oz],
          [position, oy + sy, oz],
          [position, oy + sy, oz + sz],
          [position, oy, oz + sz],
        ]
      : axis === 1
        ? [
            [ox, position, oz],
            [ox + sx, position, oz],
            [ox + sx, position, oz + sz],
            [ox, position, oz + sz],
          ]
        : [
            [ox, oy, position],
            [ox + sx, oy, position],
            [ox + sx, oy + sy, position],
            [ox, oy + sy, position],
          ];

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(corners.flat(), 3),
  );
  geometry.setAttribute(
    'uv',
    new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2),
  );
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  geometry.computeVertexNormals();
  return geometry;
}
