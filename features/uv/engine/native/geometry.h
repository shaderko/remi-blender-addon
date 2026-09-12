// Exact geometry checks and contact queries for UV fitting. No raster masks.
#pragma once

#include <array>
#include <limits>
#include <map>
#include <numeric>

namespace uv_geometry {

struct Point {
    double x, y;
    Point operator+(Point b) const { return {x + b.x, y + b.y}; }
    Point operator-(Point b) const { return {x - b.x, y - b.y}; }
    Point operator*(double s) const { return {x * s, y * s}; }
};
inline double dot(Point a, Point b) { return a.x * b.x + a.y * b.y; }
inline double cross(Point a, Point b) { return a.x * b.y - a.y * b.x; }

struct Bounds {
    Point low{INFINITY, INFINITY}, high{-INFINITY, -INFINITY};
    void add(Point p) {
        low.x = std::min(low.x, p.x); low.y = std::min(low.y, p.y);
        high.x = std::max(high.x, p.x); high.y = std::max(high.y, p.y);
    }
    void add(const Bounds &b) { add(b.low); add(b.high); }
    double distance2(const Bounds &b) const {
        double x = std::max(0.0, std::max(low.x - b.high.x, b.low.x - high.x));
        double y = std::max(0.0, std::max(low.y - b.high.y, b.low.y - high.y));
        return x * x + y * y;
    }
};

// Median-split BVH bounds both ordinary atlases and pathological stacked inputs.
class Tree {
    struct Node { Bounds bounds; int start, end, left = -1, right = -1; };
    std::vector<Node> nodes;
    std::vector<int> order;
    const std::vector<Bounds> &boxes;
    int build(int start, int end) {
        int index = static_cast<int>(nodes.size());
        nodes.push_back(Node{});
        nodes[index].start = start; nodes[index].end = end;
        for (int i = start; i < end; ++i) nodes[index].bounds.add(boxes[order[i]]);
        if (end - start <= 8) return index;
        const Bounds bounds = nodes[index].bounds;
        bool x = bounds.high.x - bounds.low.x >= bounds.high.y - bounds.low.y;
        int middle = (start + end) / 2;
        std::nth_element(order.begin() + start, order.begin() + middle, order.begin() + end,
            [&](int a, int b) {
                double ca = x ? boxes[a].low.x + boxes[a].high.x : boxes[a].low.y + boxes[a].high.y;
                double cb = x ? boxes[b].low.x + boxes[b].high.x : boxes[b].low.y + boxes[b].high.y;
                return ca == cb ? a < b : ca < cb;
            });
        int left = build(start, middle), right = build(middle, end);
        nodes[index].left = left; nodes[index].right = right;
        return index;
    }
    template<class Radius, class Visit>
    bool queryNode(int index, const Bounds &box, Radius &radius, Visit &visit) const {
        const Node &node = nodes[index];
        if (node.bounds.distance2(box) > radius()) return true;
        if (node.left < 0) {
            for (int i = node.start; i < node.end; ++i)
                if (boxes[order[i]].distance2(box) <= radius() && !visit(order[i])) return false;
            return true;
        }
        return queryNode(node.left, box, radius, visit) && queryNode(node.right, box, radius, visit);
    }
public:
    explicit Tree(const std::vector<Bounds> &input) : boxes(input) {
        order.resize(input.size()); std::iota(order.begin(), order.end(), 0);
        nodes.reserve(input.size() * 2);
        if (!input.empty()) build(0, static_cast<int>(input.size()));
    }
    template<class Radius, class Visit>
    bool query(const Bounds &box, Radius radius, Visit visit) const {
        return nodes.empty() || queryNode(0, box, radius, visit);
    }
};

using Array = py::array_t<double, py::array::c_style | py::array::forcecast>;
using Indices = py::array_t<uint32_t, py::array::c_style | py::array::forcecast>;

inline std::vector<std::array<Point, 3>> read_triangles(const Array &uvs, const Indices &indices) {
    if (uvs.ndim() != 2 || uvs.shape(1) != 2 || indices.ndim() != 2 || indices.shape(1) != 3)
        throw std::invalid_argument("expected UVs (N,2) and triangles (M,3)");
    auto uv = uvs.unchecked<2>(); auto ids = indices.unchecked<2>();
    std::vector<std::array<Point, 3>> triangles(indices.shape(0));
    for (py::ssize_t i = 0; i < indices.shape(0); ++i) for (int j = 0; j < 3; ++j) {
        uint32_t v = ids(i, j);
        if (v >= uvs.shape(0) || !std::isfinite(uv(v, 0)) || !std::isfinite(uv(v, 1)))
            throw std::invalid_argument("invalid UV triangle coordinate or index");
        triangles[i][j] = {uv(v, 0), uv(v, 1)};
    }
    return triangles;
}

inline bool overlap(const std::array<Point, 3> &a, const std::array<Point, 3> &b) {
    for (const auto *triangle : {&a, &b}) for (int e = 0; e < 3; ++e) {
        Point edge = (*triangle)[(e + 1) % 3] - (*triangle)[e];
        double length = std::sqrt(dot(edge, edge));
        if (length == 0.0) continue;
        Point axis{-edge.y / length, edge.x / length};
        double amin = INFINITY, amax = -INFINITY, bmin = INFINITY, bmax = -INFINITY;
        for (int k = 0; k < 3; ++k) {
            double av = dot(a[k], axis), bv = dot(b[k], axis);
            amin = std::min(amin, av); amax = std::max(amax, av);
            bmin = std::min(bmin, bv); bmax = std::max(bmax, bv);
        }
        if (std::min(amax, bmax) - std::max(amin, bmin) <= 1.0e-12) return false;
    }
    return true;
}

inline py::dict uv_overlaps(const Array &uvs, const Indices &indices, uint32_t limit) {
    auto triangles = read_triangles(uvs, indices);
    std::vector<Bounds> boxes(triangles.size());
    for (size_t i = 0; i < triangles.size(); ++i) for (Point p : triangles[i]) boxes[i].add(p);
    Tree tree(boxes);
    std::vector<std::array<uint32_t, 2>> pairs;
    bool complete = true;
    for (size_t i = 0; i < triangles.size() && complete; ++i) {
        complete = tree.query(boxes[i], [] { return 0.0; }, [&](int j) {
            if (j <= static_cast<int>(i) || !overlap(triangles[i], triangles[j])) return true;
            if (limit && pairs.size() >= limit) return false;
            pairs.push_back({static_cast<uint32_t>(i), static_cast<uint32_t>(j)});
            return true;
        });
    }
    std::sort(pairs.begin(), pairs.end());
    py::array_t<uint32_t> output({static_cast<py::ssize_t>(pairs.size()), py::ssize_t(2)});
    auto values = output.mutable_unchecked<2>();
    for (size_t i = 0; i < pairs.size(); ++i) { values(i, 0) = pairs[i][0]; values(i, 1) = pairs[i][1]; }
    py::dict result; result["pairs"] = output; result["complete"] = complete;
    return result;
}

inline py::dict uv_boundaries(const Array &uvs, const Indices &indices, const Indices &charts) {
    auto triangles = read_triangles(uvs, indices);
    if (charts.ndim() != 1 || charts.shape(0) != indices.shape(0))
        throw std::invalid_argument("expected one chart id per triangle");
    auto ids = indices.unchecked<2>(); auto chart = charts.unchecked<1>();
    struct Edge { int count; uint32_t a, b, chart; };
    std::map<std::array<double, 5>, Edge> edges;
    for (size_t i = 0; i < triangles.size(); ++i) for (int k = 0; k < 3; ++k) {
        Point a = triangles[i][k], b = triangles[i][(k + 1) % 3];
        if (a.x > b.x || (a.x == b.x && a.y > b.y)) std::swap(a, b);
        std::array<double, 5> key{{double(chart(i)), a.x, a.y, b.x, b.y}};
        auto found = edges.find(key);
        if (found == edges.end()) edges.emplace(key, Edge{1, ids(i, k), ids(i, (k + 1) % 3), chart(i)});
        else ++found->second.count;
    }
    std::vector<Edge> boundary;
    for (const auto &item : edges) if (item.second.count != 2) boundary.push_back(item.second);
    py::array_t<uint32_t> vertices({static_cast<py::ssize_t>(boundary.size()), py::ssize_t(2)});
    py::array_t<uint32_t> chart_ids(boundary.size());
    auto v = vertices.mutable_unchecked<2>(); auto c = chart_ids.mutable_unchecked<1>();
    for (size_t i = 0; i < boundary.size(); ++i) { v(i, 0) = boundary[i].a; v(i, 1) = boundary[i].b; c(i) = boundary[i].chart; }
    py::dict result; result["vertices"] = vertices; result["charts"] = chart_ids;
    return result;
}

inline Point closest(Point p, Point a, Point b) {
    Point d = b - a; double length2 = dot(d, d);
    return a + d * (length2 > 0.0 ? std::max(0.0, std::min(1.0, dot(p - a, d) / length2)) : 0.0);
}

inline std::pair<Point, Point> closest_pair(Point a, Point b, Point c, Point d) {
    Point ab = b - a, cd = d - c;
    double det = cross(ab, cd);
    if (std::abs(det) > 1.0e-30) {
        double t = cross(c - a, cd) / det, u = cross(c - a, ab) / det;
        if (t >= 0.0 && t <= 1.0 && u >= 0.0 && u <= 1.0) {
            Point p = a + ab * t; return {p, p};
        }
    }
    std::array<std::pair<Point, Point>, 4> pairs{{
        {a, closest(a, c, d)}, {b, closest(b, c, d)},
        {closest(c, a, b), c}, {closest(d, a, b), d}}};
    return *std::min_element(pairs.begin(), pairs.end(), [](const std::pair<Point, Point> &p, const std::pair<Point, Point> &q) {
        return dot(p.first - p.second, p.first - p.second) < dot(q.first - q.second, q.first - q.second);
    });
}

inline py::dict boundary_contacts(const Array &segments, const Indices &charts, double clearance) {
    if (segments.ndim() != 3 || segments.shape(1) != 2 || segments.shape(2) != 2 ||
        charts.ndim() != 1 || charts.shape(0) != segments.shape(0) || !std::isfinite(clearance) || clearance < 0)
        throw std::invalid_argument("invalid boundary contact arrays or clearance");
    auto p = segments.unchecked<3>(); auto c = charts.unchecked<1>();
    std::vector<Bounds> boxes(segments.shape(0));
    for (size_t i = 0; i < boxes.size(); ++i) for (int k = 0; k < 2; ++k) {
        if (!std::isfinite(p(i, k, 0)) || !std::isfinite(p(i, k, 1))) throw std::invalid_argument("non-finite boundary");
        boxes[i].add(Point{p(i, k, 0), p(i, k, 1)});
    }
    Tree tree(boxes);
    double minimum2 = INFINITY, radius2 = clearance * clearance;
    // One deepest contact per chart pair avoids overweighting long shared boundaries.
    std::map<std::pair<uint32_t, uint32_t>, std::array<double, 7>> contacts;
    for (size_t i = 0; i < boxes.size(); ++i) {
        tree.query(boxes[i], [&] { return std::max(minimum2, radius2); }, [&](int j) {
            if (j <= static_cast<int>(i) || c(i) == c(j)) return true;
            auto points = closest_pair({p(i, 0, 0), p(i, 0, 1)}, {p(i, 1, 0), p(i, 1, 1)},
                                       {p(j, 0, 0), p(j, 0, 1)}, {p(j, 1, 0), p(j, 1, 1)});
            Point delta = points.second - points.first; double distance2 = dot(delta, delta);
            minimum2 = std::min(minimum2, distance2);
            if (distance2 < radius2) {
                auto key = std::minmax(c(i), c(j));
                double distance = std::sqrt(distance2);
                auto found = contacts.find(key);
                if (found == contacts.end() || distance < found->second[6])
                    contacts[key] = {{double(c(i)), double(c(j)), points.first.x, points.first.y, points.second.x, points.second.y, distance}};
            }
            return true;
        });
    }
    py::array_t<double> output({static_cast<py::ssize_t>(contacts.size()), py::ssize_t(7)});
    auto values = output.mutable_unchecked<2>(); size_t row = 0;
    for (const auto &item : contacts) { for (int k = 0; k < 7; ++k) values(row, k) = item.second[k]; ++row; }
    py::dict result; result["contacts"] = output; result["minimum_gap"] = std::sqrt(minimum2);
    return result;
}

} // namespace uv_geometry
