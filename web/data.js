/**
 * Load a case's results and slice its fields.
 *
 * `viewer.json` carries geometry, KPIs and the residual history; `fields.bin`
 * carries the fields themselves as float32, packed back to back in [k, j, i]
 * order (z outermost, x innermost) -- the natural order of a blockMesh hex
 * block, so no index remapping is needed here.
 */

export class Results {
  constructor(meta, buffer) {
    this.meta = meta;
    const [nx, ny, nz] = meta.grid.divisions;
    this.nx = nx;
    this.ny = ny;
    this.nz = nz;
    this.fields = {};
    for (const descriptor of meta.fields) {
      this.fields[descriptor.name] = {
        ...descriptor,
        values: new Float32Array(buffer, descriptor.offset, descriptor.count),
      };
    }
    this.fields.speed = this.#speed();
  }

  static async load(baseUrl) {
    const base = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`;
    const metaResponse = await fetch(`${base}viewer.json`);
    if (!metaResponse.ok) {
      throw new Error(
        `Could not read ${base}viewer.json (HTTP ${metaResponse.status}). ` +
          `Run "aicfd post" for this case first.`,
      );
    }
    const meta = await metaResponse.json();
    const buffer = await (await fetch(`${base}fields.bin`)).arrayBuffer();
    return new Results(meta, buffer);
  }

  /** Air speed, derived rather than shipped -- it is just |U|. */
  #speed() {
    const { Ux, Uy, Uz } = this.fields;
    const values = new Float32Array(Ux.count);
    let max = 0;
    for (let i = 0; i < values.length; i += 1) {
      const v = Math.hypot(Ux.values[i], Uy.values[i], Uz.values[i]);
      values[i] = v;
      if (v > max) max = v;
    }
    return { name: 'speed', values, count: values.length, min: 0, max, units: 'm/s' };
  }

  index(i, j, k) {
    return (k * this.ny + j) * this.nx + i;
  }

  /** Physical extent of the room, in metres. */
  get size() {
    return this.meta.grid.size;
  }

  get origin() {
    return this.meta.grid.origin;
  }

  /** Cell-centre coordinate along `axis` (0=x, 1=y, 2=z) for cell index `i`. */
  coordinate(axis, i) {
    const n = [this.nx, this.ny, this.nz][axis];
    return this.origin[axis] + ((i + 0.5) * this.size[axis]) / n;
  }

  /** Cell index along `axis` nearest to a physical coordinate. */
  cellAt(axis, coordinate) {
    const n = [this.nx, this.ny, this.nz][axis];
    const t = (coordinate - this.origin[axis]) / this.size[axis];
    return Math.max(0, Math.min(n - 1, Math.floor(t * n)));
  }

  /**
   * Extract a plane normal to `axis` at cell index `at`.
   *
   * Returns the values plus the pixel dimensions of the resulting image, with
   * `width` running along the first in-plane axis and `height` along the second.
   */
  slice(fieldName, axis, at) {
    const field = this.fields[fieldName];
    const { nx, ny, nz } = this;
    let width;
    let height;
    let read;

    if (axis === 0) {
      // x = const: image is (y, z)
      [width, height] = [ny, nz];
      read = (u, v) => this.index(at, u, v);
    } else if (axis === 1) {
      // y = const: image is (x, z)
      [width, height] = [nx, nz];
      read = (u, v) => this.index(u, at, v);
    } else {
      // z = const: image is (x, y)
      [width, height] = [nx, ny];
      read = (u, v) => this.index(u, v, at);
    }

    const values = new Float32Array(width * height);
    for (let v = 0; v < height; v += 1) {
      for (let u = 0; u < width; u += 1) {
        values[v * width + u] = field.values[read(u, v)];
      }
    }
    return { values, width, height, field };
  }

  /** Value of a field at a cell, for the numeric hover readout. */
  valueAt(fieldName, i, j, k) {
    return this.fields[fieldName].values[this.index(i, j, k)];
  }
}
