"""Plain-tuple geometry: a rect is (x0, y0, x1, y1) in metres."""
from __future__ import annotations

EPS = 1e-6


def rect_of(points) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def width(r) -> float:
    return r[2] - r[0]


def height(r) -> float:
    return r[3] - r[1]


def area(r) -> float:
    return max(0.0, width(r)) * max(0.0, height(r))


def centre(r) -> tuple[float, float]:
    return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)


def contains(outer, inner, tol=0.0) -> bool:
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def contains_point(r, x, y, tol=0.0) -> bool:
    return r[0] - tol <= x <= r[2] + tol and r[1] - tol <= y <= r[3] + tol


def overlap(a, b, tol=EPS) -> bool:
    """True when the two rects share area (touching edges do not count)."""
    return not (a[2] <= b[0] + tol or b[2] <= a[0] + tol
                or a[3] <= b[1] + tol or b[3] <= a[1] + tol)


def intersection(a, b):
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[2] > r[0] and r[3] > r[1] else None


def union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def dims_match(r, size: tuple[float, float], tol: float) -> bool:
    """True when the rect measures size (either orientation) to tol."""
    w, h = width(r), height(r)
    a, b = size
    return (near(w, a, tol) and near(h, b, tol)) or (near(w, b, tol) and near(h, a, tol))


def transpose(r):
    return (r[1], r[0], r[3], r[2])


def is_axis_rect(points, tol) -> bool:
    """A closed polyline of 4 (or 5 with the first repeated) corners on the
    axes: every edge is horizontal or vertical."""
    pts = [tuple(p[:2]) for p in points]
    if len(pts) == 5 and near(pts[0][0], pts[4][0], tol) and near(pts[0][1], pts[4][1], tol):
        pts = pts[:4]
    if len(pts) != 4:
        return False
    for i in range(4):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % 4]
        if not (near(x0, x1, tol) or near(y0, y1, tol)):
            return False
    return True
