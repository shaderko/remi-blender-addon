#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <limits>
#include <memory>
#include <numeric>
#include <unordered_map>
#include <unordered_set>
#include <vector>
using V = std::array<double, 3>;
static double dot(const V &a, const V &b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
static V sub(const V &a, const V &b) {
  return {a[0] - b[0], a[1] - b[1], a[2] - b[2]};
}
static constexpr int C[8][3] = {{0, 0, 0}, {1, 0, 0}, {0, 1, 0}, {1, 1, 0},
                                {0, 0, 1}, {1, 0, 1}, {0, 1, 1}, {1, 1, 1}};
static constexpr int E[12][2] = {{0, 1}, {2, 3}, {4, 5}, {6, 7},
                                 {0, 2}, {1, 3}, {4, 6}, {5, 7},
                                 {0, 4}, {1, 5}, {2, 6}, {3, 7}};
static constexpr int FC[6][4] = {{0, 2, 6, 4}, {1, 3, 7, 5}, {0, 1, 5, 4},
                                 {2, 3, 7, 6}, {0, 1, 3, 2}, {4, 5, 7, 6}};
static constexpr int FE[6][4] = {{4, 10, 6, 8},  {5, 11, 7, 9}, {0, 9, 2, 8},
                                 {1, 11, 3, 10}, {0, 5, 1, 4},  {2, 7, 3, 6}};
struct KNode {
  int first, count, left = -1, right = -1, axis = 0;
  double split = 0;
  V low, high;
};
struct KD {
  const double *p;
  std::vector<KNode> nodes;
  std::vector<int> order;
  std::vector<V> packed;
  int root;
  KD(const double *pts, int n) : p(pts), order(n) {
    std::iota(order.begin(), order.end(), 0);
    nodes.reserve(n / 4);
    root = build(0, n);
    packed.resize(n);
    for (int i = 0; i < n; i++)
      packed[i] = {p[3 * order[i]], p[3 * order[i] + 1], p[3 * order[i] + 2]};
  }
  int build(int a, int b) {
    KNode t;
    t.first = a;
    t.count = b - a;
    t.low = {INFINITY, INFINITY, INFINITY};
    t.high = {-INFINITY, -INFINITY, -INFINITY};
    for (int i = a; i < b; i++)
      for (int k = 0; k < 3; k++) {
        t.low[k] = std::min(t.low[k], p[3 * order[i] + k]);
        t.high[k] = std::max(t.high[k], p[3 * order[i] + k]);
      }
    int me = nodes.size();
    nodes.push_back(t);
    if (b - a <= 16)
      return me;
    int ax = 0;
    for (int k = 1; k < 3; k++)
      if (t.high[k] - t.low[k] > t.high[ax] - t.low[ax])
        ax = k;
    int m = (a + b) / 2;
    std::nth_element(
        order.begin() + a, order.begin() + m, order.begin() + b,
        [&](int x, int y) { return p[3 * x + ax] < p[3 * y + ax]; });
    nodes[me].axis = ax;
    nodes[me].split = p[3 * order[m] + ax];
    int l = build(a, m), r = build(m, b);
    nodes[me].left = l;
    nodes[me].right = r;
    return me;
  }
  void walk(int n, const V &q, std::array<double, 8> &d,
            std::array<int, 8> &ids) const {
    const auto &t = nodes[n];
    double bound = 0;
    for (int a = 0; a < 3; a++) {
      double delta = q[a] < t.low[a]    ? t.low[a] - q[a]
                     : q[a] > t.high[a] ? q[a] - t.high[a]
                                        : 0;
      bound += delta * delta;
    }
    if (bound > d[7])
      return;
    if (t.left < 0) {
      for (int i = t.first; i < t.first + t.count; i++) {
        V delta = sub(q, packed[i]);
        double ds = dot(delta, delta);
        if (ds < d[7]) {
          int j = 7;
          while (j > 0 && ds < d[j - 1]) {
            d[j] = d[j - 1];
            ids[j] = ids[j - 1];
            --j;
          }
          d[j] = ds;
          ids[j] = order[i];
        }
      }
      return;
    }
    if (q[t.axis] < t.split) {
      walk(t.left, q, d, ids);
      walk(t.right, q, d, ids);
    } else {
      walk(t.right, q, d, ids);
      walk(t.left, q, d, ids);
    }
  }
  void walk_one(int n, const V &q, double &best, int &id) const {
    const auto &t = nodes[n];
    double bound = 0;
    for (int a = 0; a < 3; a++) {
      double d = q[a] < t.low[a]    ? t.low[a] - q[a]
                 : q[a] > t.high[a] ? q[a] - t.high[a]
                                    : 0;
      bound += d * d;
    }
    if (bound > best)
      return;
    if (t.left < 0) {
      for (int i = t.first; i < t.first + t.count; i++) {
        V delta = sub(q, packed[i]);
        double d = dot(delta, delta);
        bool preferred = id < 0;
        int candidate = order[i];
        if (d == best && id >= 0) {
          for (int a = 2; a >= 0; a--)
            if (p[3 * candidate + a] != p[3 * id + a]) {
              preferred = p[3 * candidate + a] < p[3 * id + a];
              break;
            }
        }
        if (d < best || (d == best && preferred)) {
          best = d;
          id = candidate;
        }
      }
      return;
    }
    if (q[t.axis] < t.split) {
      walk_one(t.left, q, best, id);
      walk_one(t.right, q, best, id);
    } else {
      walk_one(t.right, q, best, id);
      walk_one(t.left, q, best, id);
    }
  }
  int query_one(const V &q) const {
    double best = INFINITY;
    int id = -1;
    walk_one(root, q, best, id);
    return id;
  }
  std::array<int, 8> query(const V &q) const {
    std::array<double, 8> d;
    d.fill(INFINITY);
    std::array<int, 8> ids;
    ids.fill(0);
    walk(root, q, d, ids);
    return ids;
  }
};
struct G {
  V p;
  double q[6], M[9], w, cut;
  int lo[3], hi[3], rawlo[3], rawhi[3];
};
struct Leaf {
  int x, y, z, s;
  float f[8];
  int ev[12];
};
struct Edge {
  int x, y, z, a, s;
};
struct Polygon {
  std::vector<int> v;
  Edge e;
};

static void clean_vectors(std::vector<V> &v, std::vector<std::array<int, 3>> &f,
                          int minimum, int iterations) {
  int n = v.size();
  std::vector<int> parent(n), rank(n, 0);
  std::iota(parent.begin(), parent.end(), 0);
  auto find = [&](int a) {
    while (parent[a] != a) {
      parent[a] = parent[parent[a]];
      a = parent[a];
    }
    return a;
  };
  auto join = [&](int a, int b) {
    a = find(a);
    b = find(b);
    if (a == b)
      return;
    if (rank[a] < rank[b])
      std::swap(a, b);
    parent[b] = a;
    if (rank[a] == rank[b])
      rank[a]++;
  };
  for (auto t : f) {
    join(t[0], t[1]);
    join(t[1], t[2]);
  }
  std::vector<int> counts(n, 0);
  for (auto t : f)
    counts[find(t[0])]++;
  bool all = true;
  for (auto t : f)
    if (counts[find(t[0])] < minimum) {
      all = false;
      break;
    }
  if (!all) {
    std::vector<std::array<int, 3>> kept;
    kept.reserve(f.size());
    std::vector<int> remap(n, -1);
    for (auto t : f)
      if (counts[find(t[0])] >= minimum) {
        kept.push_back(t);
        for (int a : t)
          remap[a] = 0;
      }
    std::vector<V> used;
    used.reserve(n);
    for (int i = 0; i < n; i++)
      if (remap[i] >= 0) {
        remap[i] = used.size();
        used.push_back(v[i]);
      }
    for (auto &t : kept)
      for (int &a : t)
        a = remap[a];
    v.swap(used);
    f.swap(kept);
  }
  if (iterations <= 0 || f.empty())
    return;
  n = v.size();
  std::vector<size_t> offset(n + 1, 0);
  for (auto t : f)
    for (int a : t)
      offset[a + 1] += 2;
  for (int i = 0; i < n; i++)
    offset[i + 1] += offset[i];
  std::vector<int> adj(offset.back());
  auto cursor = offset;
  for (auto t : f)
    for (int a = 0; a < 3; a++) {
      adj[cursor[t[a]]++] = t[(a + 1) % 3];
      adj[cursor[t[a]]++] = t[(a + 2) % 3];
    }
  for (int i = 0; i < n; i++)
    std::sort(adj.begin() + offset[i], adj.begin() + offset[i + 1]);
  std::vector<V> next(n);
  for (int pass = 0; pass < iterations * 2; pass++) {
    double factor = pass % 2 ? -.53 : .5;
    for (int i = 0; i < n; i++) {
      V sum{0, 0, 0};
      int prev = -1, count = 0;
      for (size_t j = offset[i]; j < offset[i + 1]; j++) {
        int id = adj[j];
        if (id == prev)
          continue;
        prev = id;
        count++;
        for (int k = 0; k < 3; k++)
          sum[k] += v[id][k];
      }
      for (int k = 0; k < 3; k++)
        next[i][k] = v[i][k] + factor * (sum[k] / std::max(count, 1) - v[i][k]);
    }
    v.swap(next);
  }
}
// Separate disconnected incident-face fans without changing the surface
// positions.
static void split_vertex_fans(std::vector<V> &v,
                              std::vector<std::array<int, 3>> &f) {
  const int n = v.size();
  std::vector<size_t> offset(n + 1, 0);
  for (auto t : f)
    for (int a : t)
      offset[a + 1]++;
  for (int i = 0; i < n; i++)
    offset[i + 1] += offset[i];
  auto cursor = offset;
  std::vector<int> incident(offset.back());
  for (int i = 0; i < (int)f.size(); i++)
    for (int a : f[i])
      incident[cursor[a]++] = i;
  std::vector<int> parent, neighbour, first, remap;
  for (int a = 0; a < n; a++) {
    int count = offset[a + 1] - offset[a];
    if (count < 2)
      continue;
    parent.resize(count);
    std::iota(parent.begin(), parent.end(), 0);
    neighbour.clear();
    first.clear();
    auto find = [&](int x) {
      while (parent[x] != x) {
        parent[x] = parent[parent[x]];
        x = parent[x];
      }
      return x;
    };
    for (int i = 0; i < count; i++) {
      auto t = f[incident[offset[a] + i]];
      for (int b : t)
        if (b != a) {
          auto it = std::find(neighbour.begin(), neighbour.end(), b);
          if (it == neighbour.end()) {
            neighbour.push_back(b);
            first.push_back(i);
          } else {
            int x = find(i), y = find(first[it - neighbour.begin()]);
            parent[x] = y;
          }
        }
    }
    remap.assign(count, -1);
    bool initial = true;
    for (int i = 0; i < count; i++) {
      int root = find(i);
      if (remap[root] < 0) {
        if (initial) {
          remap[root] = a;
          initial = false;
        } else {
          remap[root] = v.size();
          v.push_back(v[a]);
        }
      }
      int id = remap[root];
      if (id != a)
        for (int &b : f[incident[offset[a] + i]])
          if (b == a)
            b = id;
    }
  }
}
struct Tile {
  float density[512], threshold[512];
  uint8_t request[512];
  bool has_requests = false;
  Tile() {
    std::fill(request, request + 512, 0);
    std::fill(density, density + 512, NAN);
    std::fill(threshold, threshold + 512, NAN);
  }
};
struct Context {
  int n, N, S, H, HS, binsN;
  V low, vox;
  std::vector<G> g;
  std::vector<std::vector<int>> bins;
  std::vector<float> upper, lower, coarse_T;
  std::vector<std::unique_ptr<Tile>> tiles;
  int tilesN;
  std::vector<uint8_t> refine;
  std::vector<int> coarse_owner, fine_owner;
  std::vector<Leaf> leaves;
  std::vector<V> vertices;
  std::vector<uint8_t> vertex_flags;
  std::vector<std::array<int, 3>> faces;
  float *logs = nullptr;
  const float *coarse_D = nullptr;
  std::vector<std::array<int, 4>> offsets;
  std::vector<V> positive_points;
  std::vector<float> positive_logs;
  std::unique_ptr<KD> positive_tree;
  uint64_t lazy_queries = 0, lazy_fallbacks = 0;
  double level, floor, power, ref, guardlo, guardhi;
  const double *sp;
  const double *op;
  KD kd;
  uint64_t queried = 0, expcount = 0, coarse_leaves = 0, fine_leaves = 0,
           ambiguous = 0;
  bool uniform = false;
  double times[4] = {0, 0, 0, 0};
  Context(const double *positions, const double *inverse, const double *metric,
          const double *weights, const int32_t *radius, int count,
          const double *low_, const double *vox_, int size,
          const double *active_p, const double *spacing, const double *opacity,
          int ac, double reference, double lev, double fl, double pow,
          double gl, double gh)
      : n(size), N(size + 4), S(N + 1), H(N / 2), HS(H + 1),
        binsN((size + 12) / 8), low{low_[0], low_[1], low_[2]},
        vox{vox_[0], vox_[1], vox_[2]}, level(lev), floor(fl), power(pow),
        ref(reference), guardlo(gl), guardhi(gh), sp(spacing), op(opacity),
        kd(active_p, ac) {
    tilesN = (S + 7) / 8;
    tiles.resize((size_t)tilesN * tilesN * tilesN);
    coarse_T.assign((size_t)(n / 2) * (n / 2) * (n / 2), NAN);
    upper.assign((size_t)H * H * H, 0);
    lower.assign(upper.size(), 0);
    refine.assign(upper.size(), 0);
    coarse_owner.assign(upper.size(), -1);
    bins.resize(binsN * binsN * binsN);
    g.reserve(count);
    for (int dx = -4; dx <= 4; dx++)
      for (int dy = -4; dy <= 4; dy++)
        for (int dz = -4; dz <= 4; dz++) {
          int d = dx * dx + dy * dy + dz * dz;
          if (d <= 16)
            offsets.push_back({d, dx, dy, dz});
        }
    std::sort(offsets.begin(), offsets.end(), [](const auto &a, const auto &b) {
      if (a[0] != b[0])
        return a[0] < b[0];
      if (a[3] != b[3])
        return a[3] < b[3];
      if (a[2] != b[2])
        return a[2] < b[2];
      return a[1] < b[1];
    });
    for (int i = 0; i < count; i++) {
      if (weights[i] <= 1e-4)
        continue;
      G z;
      for (int a = 0; a < 3; a++) {
        z.p[a] = (positions[3 * i + a] - low[a]) / vox[a] - .5;
        int c = (int)std::floor((positions[3 * i + a] - low[a]) / vox[a]);
        z.rawlo[a] = c - radius[3 * i + a];
        z.rawhi[a] = c + radius[3 * i + a];
        z.lo[a] = std::clamp(z.rawlo[a], 0, n - 1);
        z.hi[a] = std::clamp(z.rawhi[a], 0, n - 1);
      }
      z.q[0] = inverse[9 * i] * vox[0] * vox[0];
      z.q[1] = inverse[9 * i + 4] * vox[1] * vox[1];
      z.q[2] = inverse[9 * i + 8] * vox[2] * vox[2];
      z.q[3] = inverse[9 * i + 1] * vox[0] * vox[1];
      z.q[4] = inverse[9 * i + 2] * vox[0] * vox[2];
      z.q[5] = inverse[9 * i + 5] * vox[1] * vox[2];
      for (int j = 0; j < 9; j++)
        z.M[j] = metric[9 * i + j] * vox[j % 3];
      z.w = weights[i];
      z.cut = 2 * std::log(z.w / 1e-4);
      int gi = g.size();
      g.push_back(z);
      for (int x = (z.lo[0] + 2) / 8; x <= (z.hi[0] + 2) / 8; x++)
        for (int y = (z.lo[1] + 2) / 8; y <= (z.hi[1] + 2) / 8; y++)
          for (int k = (z.lo[2] + 2) / 8; k <= (z.hi[2] + 2) / 8; k++)
            bins[(x * binsN + y) * binsN + k].push_back(gi);
    }
  }
  size_t ni(int x, int y, int z) const { return ((size_t)x * S + y) * S + z; }
  int ci(int x, int y, int z) const { return (x * H + y) * H + z; }
  int mult(const G &z, int a, int i) const {
    if (i == 0)
      return std::min(z.rawhi[a], 0) - z.rawlo[a] + 1;
    if (i == n - 1)
      return z.rawhi[a] - std::max(z.rawlo[a], n - 1) + 1;
    return 1;
  }
  double quadratic(const G &z, double x, double y, double k) const {
    return z.q[0] * x * x + z.q[1] * y * y + z.q[2] * k * k +
           2 * (z.q[3] * x * y + z.q[4] * x * k + z.q[5] * y * k);
  }
  float &cache_value(int x, int y, int z, bool threshold) {
    size_t tile = ((size_t)(x / 8) * tilesN + y / 8) * tilesN + z / 8;
    if (!tiles[tile])
      tiles[tile] = std::make_unique<Tile>();
    int local = ((x & 7) * 8 + (y & 7)) * 8 + (z & 7);
    return threshold ? tiles[tile]->threshold[local]
                     : tiles[tile]->density[local];
  }
  float density(int x, int y, int z) {
    int a = x - 2, b = y - 2, c = z - 2;
    if (a < 0 || b < 0 || c < 0 || a >= n || b >= n || c >= n)
      return 0;
    if (!(a & 1) && !(b & 1) && !(c & 1))
      return coarse_D[((size_t)(a / 2) * (n / 2) + b / 2) * (n / 2) + c / 2];
    float &cached = cache_value(x, y, z, false);
    if (!std::isnan(cached))
      return cached;
    queried++;
    float total = 0;
    for (int i : bins[((x / 8) * binsN + y / 8) * binsN + z / 8]) {
      const auto &t = g[i];
      if (a < t.lo[0] || a > t.hi[0] || b < t.lo[1] || b > t.hi[1] ||
          c < t.lo[2] || c > t.hi[2])
        continue;
      double quad = quadratic(t, a - t.p[0], b - t.p[1], c - t.p[2]);
      if (quad > t.cut + 1e-12)
        continue;
      double value = t.w * std::exp(-.5 * quad);
      expcount++;
      if (value > 1e-4)
        total = (float)((double)total + value * (int64_t)mult(t, 0, a) *
                                            mult(t, 1, b) * mult(t, 2, c));
    }
    return cached = total;
  }
  void coarse(float *out, float *log_out, int query_logs) {
    // Original fine-grid samples at stride two; no coarse-scale sigma
    // inflation.
    int h = n / 2;
    coarse_D = out;
    std::fill(out, out + (size_t)h * h * h, 0.f);
    for (const auto &t : g)
      for (int x = ((t.lo[0] + 1) / 2) * 2; x <= t.hi[0]; x += 2)
        for (int y = ((t.lo[1] + 1) / 2) * 2; y <= t.hi[1]; y += 2)
          for (int z = ((t.lo[2] + 1) / 2) * 2; z <= t.hi[2]; z += 2) {
            double quad = quadratic(t, x - t.p[0], y - t.p[1], z - t.p[2]);
            if (quad > t.cut + 1e-12)
              continue;
            double value = t.w * std::exp(-.5 * quad);
            expcount++;
            if (value > 1e-4) {
              size_t id = ((size_t)(x / 2) * h + y / 2) * h + z / 2;
              out[id] =
                  (float)((double)out[id] + value * (int64_t)mult(t, 0, x) *
                                                mult(t, 1, y) * mult(t, 2, z));
            }
          }
    for (int x = 0; x < h; x++)
      for (int y = 0; y < h; y++)
        for (int z = 0; z < h; z++) {
          size_t id = ((size_t)x * h + y) * h + z;
          log_out[id] = out[id] > 0 ? 0 : NAN;
          if (out[id] <= 0 || !query_logs)
            continue;
          V q{low[0] + (2 * x + .5) * vox[0], low[1] + (2 * y + .5) * vox[1],
              low[2] + (2 * z + .5) * vox[2]};
          auto ids = kd.query(q);
          std::array<double, 8> s, o;
          for (int i = 0; i < 8; i++) {
            s[i] = sp[ids[i]];
            o[i] = op[ids[i]];
          }
          std::sort(s.begin(), s.end());
          std::sort(o.begin(), o.end());
          double local = (s[3] + s[4]) * .5, opacity = (o[3] + o[4]) * .5;
          double guard =
              std::clamp((opacity - guardlo) / (guardhi - guardlo), 0., 1.);
          log_out[id] = std::max(std::log(local / ref), 0.) * guard;
        }
  }
  double log_value(int x, int y, int z) {
    int h = n / 2;
    size_t id = ((size_t)x * h + y) * h + z;
    if (!std::isnan(logs[id]))
      return logs[id];
    lazy_queries++;
    for (auto off : offsets) {
      int xx = x + off[1], yy = y + off[2], zz = z + off[3];
      if (xx < 0 || yy < 0 || zz < 0 || xx >= h || yy >= h || zz >= h)
        continue;
      size_t next = ((size_t)xx * h + yy) * h + zz;
      if (coarse_D[next] > 0)
        return logs[id] = logs[next];
    }
    lazy_fallbacks++;
    if (!positive_tree) {
      for (int xx = 0; xx < h; xx++)
        for (int yy = 0; yy < h; yy++)
          for (int zz = 0; zz < h; zz++) {
            size_t next = ((size_t)xx * h + yy) * h + zz;
            if (coarse_D[next] > 0) {
              positive_points.push_back({(double)xx, (double)yy, (double)zz});
              positive_logs.push_back(logs[next]);
            }
          }
      if (positive_points.empty())
        return logs[id] = 0;
      positive_tree = std::make_unique<KD>(positive_points[0].data(),
                                           positive_points.size());
    }
    int nearest = positive_tree->query_one({(double)x, (double)y, (double)z});
    return logs[id] = positive_logs[nearest];
  }
  double threshold(int x, int y, int z) {
    if (!(x & 1) && !(y & 1) && !(z & 1)) {
      int h = n / 2;
      int xx = std::clamp((x - 2) / 2, 0, h - 1),
          yy = std::clamp((y - 2) / 2, 0, h - 1),
          zz = std::clamp((z - 2) / 2, 0, h - 1);
      size_t id = ((size_t)xx * h + yy) * h + zz;
      float &cached = coarse_T[id];
      if (!std::isnan(cached))
        return cached;
      double sum = log_value(xx, yy, zz);
      return cached =
                 (float)(sum <= 0 ? level
                                  : (power * sum >= std::log(level / floor)
                                         ? floor
                                         : level * std::exp(-power * sum)));
    }
    float &cached = cache_value(x, y, z, true);
    if (!std::isnan(cached))
      return cached;
    double p[3];
    int b[3];
    double t[3];
    int h = n / 2;
    int v[3] = {x, y, z};
    for (int a = 0; a < 3; a++) {
      p[a] = std::clamp((v[a] - 2) * .5, 0., (double)h - 1);
      b[a] = (int)p[a];
      t[a] = p[a] - b[a];
    }
    double sum = 0;
    for (int i = 0; i < 8; i++) {
      int xx = std::min(b[0] + C[i][0], h - 1),
          yy = std::min(b[1] + C[i][1], h - 1),
          zz = std::min(b[2] + C[i][2], h - 1);
      double w = 1;
      for (int a = 0; a < 3; a++)
        w *= C[i][a] ? t[a] : 1 - t[a];
      if (w)
        sum += w * log_value(xx, yy, zz);
    }
    return cached = (float)(sum <= 0 ? level
                                     : (power * sum >= std::log(level / floor)
                                            ? floor
                                            : level * std::exp(-power * sum)));
  }
  float phi(int x, int y, int z) {
    return (float)(density(x, y, z) - threshold(x, y, z));
  }
  void bounds() {
    for (const auto &t : g) {
      int a[3], b[3];
      for (int k = 0; k < 3; k++) {
        a[k] = std::max(0, (t.lo[k] + 1) / 2);
        b[k] = std::min(H - 1, (t.hi[k] + 2) / 2);
      }
      for (int x = a[0]; x <= b[0]; x++)
        for (int y = a[1]; y <= b[1]; y++)
          for (int z = a[2]; z <= b[2]; z++) {
            double dc[3] = {2. * x - 1 - t.p[0], 2. * y - 1 - t.p[1],
                            2. * z - 1 - t.p[2]};
            double qmin = 0, qmax = 0;
            for (int r = 0; r < 3; r++) {
              double v = 0, w = 0;
              for (int k = 0; k < 3; k++) {
                v += t.M[3 * r + k] * dc[k];
                w += std::abs(t.M[3 * r + k]);
              }
              qmin += std::pow(std::max(std::abs(v) - w, 0.), 2);
              qmax += std::pow(std::abs(v) + w, 2);
            }
            if (qmin > t.cut + 1e-12)
              continue;
            double u = qmin == 0 ? t.w : t.w * std::exp(-.5 * qmin);
            if (u > 1e-4)
              upper[ci(x, y, z)] +=
                  u * mult(t, 0, std::clamp(2 * x - 1, 0, n - 1)) *
                  mult(t, 1, std::clamp(2 * y - 1, 0, n - 1)) *
                  mult(t, 2, std::clamp(2 * z - 1, 0, n - 1));
            bool covers = true;
            int c[3] = {2 * x - 2, 2 * y - 2, 2 * z - 2};
            for (int k = 0; k < 3; k++)
              covers &= c[k] >= t.lo[k] && c[k] + 2 <= t.hi[k];
            if (covers && qmax <= t.cut + 1e-12) {
              double l = t.w * std::exp(-.5 * qmax);
              if (l > 1e-4)
                lower[ci(x, y, z)] += l;
            }
          }
    }
  }
  void values(int x, int y, int z, int size, float *f) {
    for (int i = 0; i < 8; i++)
      f[i] = phi(x + C[i][0] * size, y + C[i][1] * size, z + C[i][2] * size);
  }
  static bool mixed(const float *f) {
    int a = 0;
    for (int i = 0; i < 8; i++)
      a += f[i] >= 0;
    return a > 0 && a < 8;
  }
  static int components(const float *f, int *parent) {
    for (int e = 0; e < 12; e++)
      parent[e] = e;
    auto find = [&](int a) {
      while (parent[a] != a)
        a = parent[a];
      return a;
    };
    auto join = [&](int a, int b) {
      a = find(a);
      b = find(b);
      parent[a] = b;
    };
    for (int face = 0; face < 6; face++) {
      int cross[4], count = 0;
      for (int i = 0; i < 4; i++) {
        int e = FE[face][i];
        if ((f[E[e][0]] >= 0) != (f[E[e][1]] >= 0))
          cross[count++] = i;
      }
      if (count == 2)
        join(FE[face][cross[0]], FE[face][cross[1]]);
      else if (count == 4) {
        double q = (double)f[FC[face][0]] * f[FC[face][2]] -
                   (double)f[FC[face][1]] * f[FC[face][3]];
        if (q >= 0) {
          join(FE[face][0], FE[face][1]);
          join(FE[face][2], FE[face][3]);
        } else {
          join(FE[face][0], FE[face][3]);
          join(FE[face][1], FE[face][2]);
        }
      }
    }
    int n = 0;
    for (int e = 0; e < 12; e++)
      if ((f[E[e][0]] >= 0) != (f[E[e][1]] >= 0)) {
        parent[e] = find(e);
        if (parent[e] == e)
          n++;
      }
    return n;
  }

  bool fine_patch_disk(int x, int y, int z, const float *coarse) {
    float f[3][3][3];
    for (int a = 0; a < 3; a++)
      for (int b = 0; b < 3; b++)
        for (int c = 0; c < 3; c++)
          f[a][b][c] = phi(x + a, y + b, z + c);
    int parent[54], boundary[6][54];
    bool used[54] = {0}, bu[6][54] = {{0}};
    for (int i = 0; i < 54; i++) {
      parent[i] = i;
      for (int b = 0; b < 6; b++)
        boundary[b][i] = i;
    }
    auto edgeid = [](int a, const int *p) {
      if (a == 0)
        return (p[0] * 3 + p[1]) * 3 + p[2];
      if (a == 1)
        return 18 + (p[0] * 2 + p[1]) * 3 + p[2];
      return 36 + (p[0] * 3 + p[1]) * 2 + p[2];
    };
    auto find = [](int *p, int a) {
      while (p[a] != a) {
        p[a] = p[p[a]];
        a = p[a];
      }
      return a;
    };
    auto join = [&](int *p, int a, int b) {
      a = find(p, a);
      b = find(p, b);
      p[a] = b;
    };
    int edges = 0, faces_count = 0;
    for (int axis = 0; axis < 3; axis++)
      for (int plane = 0; plane < 3; plane++)
        for (int i = 0; i < 2; i++)
          for (int j = 0; j < 2; j++) {
            int u = (axis + 1) % 3, v = (axis + 2) % 3, p0[3] = {0, 0, 0};
            p0[axis] = plane;
            p0[u] = i;
            p0[v] = j;
            int pp[4][3];
            for (int c = 0; c < 4; c++)
              std::copy(p0, p0 + 3, pp[c]);
            pp[1][u]++;
            pp[2][u]++;
            pp[2][v]++;
            pp[3][v]++;
            float values[4];
            for (int c = 0; c < 4; c++)
              values[c] = f[pp[c][0]][pp[c][1]][pp[c][2]];
            int ee[4] = {edgeid(u, pp[0]), edgeid(v, pp[1]), edgeid(u, pp[3]),
                         edgeid(v, pp[0])};
            int cross[4], count = 0;
            for (int c = 0; c < 4; c++)
              if ((values[c] >= 0) != (values[(c + 1) % 4] >= 0)) {
                cross[count++] = c;
                used[ee[c]] = true;
              }
            int bnd = plane == 0 ? axis * 2 : plane == 2 ? axis * 2 + 1 : -1;
            auto arc = [&](int a, int b) {
              edges++;
              join(parent, ee[a], ee[b]);
              if (bnd >= 0) {
                join(boundary[bnd], ee[a], ee[b]);
                bu[bnd][ee[a]] = bu[bnd][ee[b]] = true;
              }
            };
            if (count == 2)
              arc(cross[0], cross[1]);
            else if (count == 4) {
              double q =
                  (double)values[0] * values[2] - (double)values[1] * values[3];
              if (q >= 0) {
                arc(0, 1);
                arc(2, 3);
              } else {
                arc(0, 3);
                arc(1, 2);
              }
            }
          }
    for (int cell = 0; cell < 8; cell++) {
      float values[8];
      for (int c = 0; c < 8; c++)
        values[c] =
            f[C[cell][0] + C[c][0]][C[cell][1] + C[c][1]][C[cell][2] + C[c][2]];
      if (mixed(values)) {
        int p[12];
        faces_count += components(values, p);
      }
    }
    int vertices_count = 0, groups = 0;
    for (int i = 0; i < 54; i++)
      if (used[i]) {
        vertices_count++;
        if (find(parent, i) == i)
          groups++;
      }
    if (groups != 1 || vertices_count - edges + faces_count != 1)
      return false;
    for (int bnd = 0; bnd < 6; bnd++) {
      int expected = 0;
      for (int i = 0; i < 4; i++) {
        int e = FE[bnd][i];
        expected += (coarse[E[e][0]] >= 0) != (coarse[E[e][1]] >= 0);
      }
      int count = 0, groups = 0;
      for (int i = 0; i < 54; i++)
        if (bu[bnd][i]) {
          count++;
          if (find(boundary[bnd], i) == i)
            groups++;
        }
      if (expected == 0 && count)
        return false;
      if (expected == 2 && groups != 1)
        return false;
    }
    return true;
  }
  bool coarse_ok(int x, int y, int z, const float *f, double tolerance) {
    int parent[12];
    if (components(f, parent) > 1)
      return false;
    for (int face = 0; face < 6; face++) {
      int count = 0;
      for (int edge : FE[face])
        count += (f[E[edge][0]] >= 0) != (f[E[edge][1]] >= 0);
      if (count > 2)
        return false;
    }
    if (!fine_patch_disk(x, y, z, f))
      return false;
    if (tolerance >= 1.0)
      return true;
    // Preserve fine-lattice edge crossings and face signs at transitions.
    for (int e = 0; e < 12; e++) {
      int a = E[e][0], b = E[e][1];
      int xx = x + C[a][0] + C[b][0], yy = y + C[a][1] + C[b][1],
          zz = z + C[a][2] + C[b][2];
      double m = phi(xx, yy, zz);
      bool s0 = f[a] >= 0, s1 = m >= 0, s2 = f[b] >= 0;
      if (s0 == s2) {
        if (s0 != s1)
          return false;
      } else {
        double coarse = 2 * f[a] / (f[a] - f[b]);
        double fine = s0 != s1 ? f[a] / (f[a] - m) : 1 + m / (m - f[b]);
        if (std::abs(coarse - fine) > tolerance)
          return false;
      }
    }
    for (int face = 0; face < 6; face++) {
      int q[3] = {0, 0, 0};
      double expected = 0;
      for (int i = 0; i < 4; i++) {
        int c = FC[face][i];
        for (int a = 0; a < 3; a++)
          q[a] += C[c][a];
        expected += f[c];
      }
      if ((phi(x + q[0] / 2, y + q[1] / 2, z + q[2] / 2) >= 0) !=
          (expected >= 0))
        return false;
    }
    double expected = std::accumulate(f, f + 8, 0.);
    if ((phi(x + 1, y + 1, z + 1) >= 0) != (expected >= 0))
      return false;
    return true;
  }
  void select(double sparse_level, double tolerance, bool force_uniform) {
    double clock0 = (double)std::clock() / CLOCKS_PER_SEC;
    bounds();
    times[0] = (double)std::clock() / CLOCKS_PER_SEC - clock0;
    uniform = force_uniform;
    for (int x = 0; x < H; x++)
      for (int y = 0; y < H; y++)
        for (int z = 0; z < H; z++) {
          int preliminary = ci(x, y, z);
          if (upper[preliminary] < floor || lower[preliminary] >= level)
            continue;
          float f[8];
          values(2 * x, 2 * y, 2 * z, 2, f);
          double lo = INFINITY, hi = 0;
          int positives = 0;
          for (int i = 0; i < 8; i++) {
            double t = threshold(2 * x + C[i][0] * 2, 2 * y + C[i][1] * 2,
                                 2 * z + C[i][2] * 2);
            lo = std::min(lo, t);
            hi = std::max(hi, t);
            positives += f[i] >= 0;
          }
          int id = ci(x, y, z);
          bool candidate =
              mixed(f) || (positives == 0 ? upper[id] >= lo : lower[id] < hi);
          if (!candidate)
            continue;
          double surface_level = 0;
          for (int e = 0; e < 12; e++) {
            int a = E[e][0], b = E[e][1];
            if ((f[a] >= 0) == (f[b] >= 0))
              continue;
            double t = f[a] / (f[a] - f[b]);
            double ta = threshold(2 * x + 2 * C[a][0], 2 * y + 2 * C[a][1],
                                  2 * z + 2 * C[a][2]),
                   tb = threshold(2 * x + 2 * C[b][0], 2 * y + 2 * C[b][1],
                                  2 * z + 2 * C[b][2]);
            surface_level = std::max(surface_level, (1 - t) * ta + t * tb);
          }
          if (force_uniform || !mixed(f) ||
              surface_level > sparse_level * (1 + 1e-6))
            refine[id] = 1;
          else
            refine[id] = 2;
        }
    // Evaluate the candidate band once, then coarsen only a connected disk
    // whose intersection with each shared face is one arc. This preserves the
    // fine-grid topology at coarse/fine joins; no post-extraction hole filling
    // is used.
    requested_density();
    for (int x = 0; x < H; x++)
      for (int y = 0; y < H; y++)
        for (int z = 0; z < H; z++) {
          int id = ci(x, y, z);
          if (refine[id] != 2)
            continue;
          float f[8];
          values(2 * x, 2 * y, 2 * z, 2, f);
          refine[id] = coarse_ok(2 * x, 2 * y, 2 * z, f, tolerance) ? 0 : 1;
        }
  }

  static bool solve(double A[3][3], double b[3], V &x) {
    for (int k = 0; k < 3; k++) {
      int p = k;
      for (int j = k + 1; j < 3; j++)
        if (std::abs(A[j][k]) > std::abs(A[p][k]))
          p = j;
      if (std::abs(A[p][k]) < 1e-14)
        return false;
      for (int j = k; j < 3; j++)
        std::swap(A[p][j], A[k][j]);
      std::swap(b[p], b[k]);
      double d = A[k][k];
      for (int j = k; j < 3; j++)
        A[k][j] /= d;
      b[k] /= d;
      for (int i = 0; i < 3; i++)
        if (i != k) {
          d = A[i][k];
          for (int j = k; j < 3; j++)
            A[i][j] -= d * A[k][j];
          b[i] -= d * b[k];
        }
    }
    x = {b[0], b[1], b[2]};
    return true;
  }
  int add_leaf(int x, int y, int z, int size, const float *known = nullptr) {
    Leaf l;
    l.x = x;
    l.y = y;
    l.z = z;
    l.s = size;
    if (known)
      std::copy(known, known + 8, l.f);
    else
      values(x, y, z, size, l.f);
    std::fill(l.ev, l.ev + 12, -1);
    if (!mixed(l.f))
      return -1;
    int parent[12];
    int parts = components(l.f, parent);
    bool flag = parts > 1 || size == 2;
    for (int face = 0; face < 6; face++) {
      int crossings = 0;
      for (int e : FE[face])
        crossings += (l.f[E[e][0]] >= 0) != (l.f[E[e][1]] >= 0);
      flag |= crossings == 4;
    }
    if (parts > 1)
      ambiguous++;
    std::vector<int> roots;
    for (int e = 0; e < 12; e++)
      if ((l.f[E[e][0]] >= 0) != (l.f[E[e][1]] >= 0) &&
          std::find(roots.begin(), roots.end(), parent[e]) == roots.end())
        roots.push_back(parent[e]);
    for (int root : roots) {
      std::array<V, 12> hits, normals;
      int nhit = 0;
      V mass{0, 0, 0};
      for (int e = 0; e < 12; e++)
        if (parent[e] == root && ((l.f[E[e][0]] >= 0) != (l.f[E[e][1]] >= 0))) {
          int a = E[e][0], b = E[e][1];
          double t = l.f[a] / (l.f[a] - l.f[b]);
          V p;
          for (int k = 0; k < 3; k++)
            p[k] = C[a][k] + t * (C[b][k] - C[a][k]);
          V grad;
          for (int k = 0; k < 3; k++) {
            int bit = 1 << k;
            double ga = l.f[a | bit] - l.f[a & ~bit],
                   gb = l.f[b | bit] - l.f[b & ~bit];
            grad[k] = ((1 - t) * ga + t * gb) / (vox[k] * size);
          }
          double length = std::sqrt(dot(grad, grad));
          if (length > 1e-30)
            for (double &v : grad)
              v /= length;
          hits[nhit] = p;
          normals[nhit] = grad;
          nhit++;
          for (int k = 0; k < 3; k++)
            mass[k] += p[k];
        }
      for (double &v : mass)
        v /= nhit;
      double A[3][3] = {{0}}, rhs[3] = {0, 0, 0};
      for (size_t i = 0; i < nhit; i++) {
        V delta;
        for (int k = 0; k < 3; k++)
          delta[k] = (hits[i][k] - mass[k]) * vox[k] * size;
        double t = dot(normals[i], delta);
        for (int a = 0; a < 3; a++) {
          rhs[a] += normals[i][a] * t;
          for (int b = 0; b < 3; b++)
            A[a][b] += normals[i][a] * normals[i][b];
        }
      }
      double reg = .01 * nhit;
      for (int k = 0; k < 3; k++)
        A[k][k] += reg;
      V delta{0, 0, 0};
      solve(A, rhs, delta);
      V point;
      for (int k = 0; k < 3; k++)
        point[k] =
            std::clamp(mass[k] + delta[k] / (vox[k] * size), 0., 1.) * size +
            (k == 0   ? x
             : k == 1 ? y
                      : z);
      int id = vertices.size();
      vertices.push_back(point);
      vertex_flags.push_back(flag);
      for (int e = 0; e < 12; e++)
        if (parent[e] == root && ((l.f[E[e][0]] >= 0) != (l.f[E[e][1]] >= 0)))
          l.ev[e] = id;
    }
    int id = leaves.size();
    leaves.push_back(l);
    if (size == 2)
      coarse_leaves++;
    else
      fine_leaves++;
    return id;
  }
  int owner(int x, int y, int z) const {
    if (x < 0 || y < 0 || z < 0 || x >= N || y >= N || z >= N)
      return -1;
    int id = ci(x / 2, y / 2, z / 2);
    if (!refine[id])
      return coarse_owner[id];
    int base = coarse_owner[id];
    return base < 0 ? -1
                    : fine_owner[base + (x & 1) + 2 * (y & 1) + 4 * (z & 1)];
  }

  void mark_request(int x, int y, int z) {
    int a = x - 2, b = y - 2, c = z - 2;
    if (a < 0 || b < 0 || c < 0 || a >= n || b >= n || c >= n ||
        (!(a & 1) && !(b & 1) && !(c & 1)))
      return;
    float &cached = cache_value(x, y, z, false);
    if (!std::isnan(cached))
      return;
    cached = 0;
    size_t tile = ((size_t)(x / 8) * tilesN + y / 8) * tilesN + z / 8;
    int local = ((x & 7) * 8 + (y & 7)) * 8 + (z & 7);
    tiles[tile]->request[local] = 1;
    tiles[tile]->has_requests = true;
    queried++;
  }
  void requested_density() {
    for (int x = 0; x < H; x++)
      for (int y = 0; y < H; y++)
        for (int z = 0; z < H; z++) {
          int id = ci(x, y, z);
          if (!refine[id] || upper[id] < floor || lower[id] >= level)
            continue;
          for (int a = 0; a < 3; a++)
            for (int b = 0; b < 3; b++)
              for (int c = 0; c < 3; c++)
                mark_request(2 * x + a, 2 * y + b, 2 * z + c);
        }
    for (int tx = 0; tx < tilesN; tx++)
      for (int ty = 0; ty < tilesN; ty++)
        for (int tz = 0; tz < tilesN; tz++) {
          size_t id = ((size_t)tx * tilesN + ty) * tilesN + tz;
          if (!tiles[id] || !tiles[id]->has_requests)
            continue;
          auto &tile = *tiles[id];
          for (int index : bins[id]) {
            const auto &t = g[index];
            int ax = std::max(tx * 8, t.lo[0] + 2),
                bx = std::min(tx * 8 + 7, t.hi[0] + 2),
                ay = std::max(ty * 8, t.lo[1] + 2),
                by = std::min(ty * 8 + 7, t.hi[1] + 2),
                az = std::max(tz * 8, t.lo[2] + 2),
                bz = std::min(tz * 8 + 7, t.hi[2] + 2);
            for (int x = ax; x <= bx; x++)
              for (int y = ay; y <= by; y++)
                for (int z = az; z <= bz; z++) {
                  int local = ((x & 7) * 8 + (y & 7)) * 8 + (z & 7);
                  if (!tile.request[local])
                    continue;
                  int a = x - 2, b = y - 2, c = z - 2;
                  double q = quadratic(t, a - t.p[0], b - t.p[1], c - t.p[2]);
                  if (q > t.cut + 1e-12)
                    continue;
                  double value = t.w * std::exp(-.5 * q);
                  expcount++;
                  if (value > 1e-4)
                    tile.density[local] =
                        (float)((double)tile.density[local] +
                                value * (int64_t)mult(t, 0, a) * mult(t, 1, b) *
                                    mult(t, 2, c));
                }
          }
          std::fill(tile.request, tile.request + 512, 0);
          tile.has_requests = false;
        }
  }
  void make_leaves() {
    fine_owner.reserve(std::count(refine.begin(), refine.end(), 1) * 8);
    for (int x = 0; x < H; x++)
      for (int y = 0; y < H; y++)
        for (int z = 0; z < H; z++) {
          int id = ci(x, y, z);
          if (upper[id] < floor || lower[id] >= level)
            continue;
          if (refine[id]) {
            coarse_owner[id] = fine_owner.size();
            fine_owner.resize(fine_owner.size() + 8, -1);
            float local[3][3][3];
            for (int a = 0; a < 3; a++)
              for (int b = 0; b < 3; b++)
                for (int c = 0; c < 3; c++)
                  local[a][b][c] = phi(2 * x + a, 2 * y + b, 2 * z + c);
            for (int a = 0; a < 8; a++) {
              int xx = 2 * x + C[a][0], yy = 2 * y + C[a][1],
                  zz = 2 * z + C[a][2];
              float f[8];
              for (int b = 0; b < 8; b++)
                f[b] = local[C[a][0] + C[b][0]][C[a][1] + C[b][1]]
                            [C[a][2] + C[b][2]];
              fine_owner[coarse_owner[id] + a] = add_leaf(xx, yy, zz, 1, f);
            }
          } else
            coarse_owner[id] = add_leaf(2 * x, 2 * y, 2 * z, 2);
        }
  }
  std::array<int, 4> around(const Edge &e) const {
    int u = (e.a + 1) % 3, v = (e.a + 2) % 3;
    int p[3] = {e.x, e.y, e.z};
    std::array<int, 4> result;
    int offsets[4][2] = {{-1, -1}, {0, -1}, {0, 0}, {-1, 0}};
    for (int k = 0; k < 4; k++) {
      int q[3] = {p[0], p[1], p[2]};
      q[u] += offsets[k][0];
      q[v] += offsets[k][1];
      result[k] = owner(q[0], q[1], q[2]);
    }
    return result;
  }
  int edge_vertex(int leaf, const Edge &e) const {
    if (leaf < 0)
      return -1;
    const auto &l = leaves[leaf];
    if (l.s == 2) {
      for (int v : l.ev)
        if (v >= 0)
          return v;
      return -1;
    }
    int p[3] = {e.x - l.x, e.y - l.y, e.z - l.z};
    int edge = -1;
    if (e.a == 0)
      edge = p[1] + 2 * p[2];
    if (e.a == 1)
      edge = 4 + p[0] + 2 * p[2];
    if (e.a == 2)
      edge = 8 + p[0] + 2 * p[1];
    return edge >= 0 && edge < 12 ? l.ev[edge] : -1;
  }
  uint64_t edge_key(const Edge &e) const {
    return ((uint64_t)ni(e.x, e.y, e.z) * 3 + e.a) * 2 + (e.s == 2);
  }
  V edge_hit(const Edge &e) {
    int end[3] = {e.x, e.y, e.z};
    end[e.a] += e.s;
    double a = phi(e.x, e.y, e.z), b = phi(end[0], end[1], end[2]);
    V p{(double)e.x, (double)e.y, (double)e.z};
    p[e.a] += e.s * a / (a - b);
    return p;
  }
  void polygons() {
    std::vector<Polygon> polys;
    polys.reserve(leaves.size() * 3);
    auto emit = [&](const Edge &e) {
      int end[3] = {e.x, e.y, e.z};
      end[e.a] += e.s;
      float a = phi(e.x, e.y, e.z), b = phi(end[0], end[1], end[2]);
      if ((a >= 0) == (b >= 0))
        return;
      auto owners = around(e);
      Polygon p;
      p.e = e;
      for (int own : owners) {
        int v = edge_vertex(own, e);
        if (v < 0)
          return;
        if (p.v.empty() || p.v.back() != v)
          p.v.push_back(v);
      }
      if (p.v.size() > 1 && p.v.front() == p.v.back())
        p.v.pop_back();
      if (p.v.size() < 3)
        return;
      if (a < 0)
        std::reverse(p.v.begin(), p.v.end());
      polys.push_back(std::move(p));
    };
    for (int li = 0; li < (int)leaves.size(); li++) {
      const auto &l = leaves[li];
      for (int k = 0; k < 12; k++) {
        if (l.ev[k] < 0)
          continue;
        int a = E[k][0], b = E[k][1], axis = 0;
        for (int j = 0; j < 3; j++)
          if (C[a][j] != C[b][j])
            axis = j;
        Edge e{l.x + C[a][0] * l.s, l.y + C[a][1] * l.s, l.z + C[a][2] * l.s,
               axis, l.s};
        bool has_fine = false;
        int winner = INT32_MAX;
        if (l.s == 2) {
          for (int part = 0; part < 2; part++) {
            Edge sub = e;
            sub.s = 1;
            if (axis == 0)
              sub.x += part;
            else if (axis == 1)
              sub.y += part;
            else
              sub.z += part;
            for (int own : around(sub))
              if (own >= 0 && leaves[own].s == 1)
                has_fine = true;
          }
          if (has_fine)
            continue;
          for (int own : around(e))
            if (own >= 0)
              winner = std::min(winner, own);
        } else {
          for (int own : around(e))
            if (own >= 0 && leaves[own].s == 1)
              winner = std::min(winner, own);
        }
        if (winner == li)
          emit(e);
      }
    }
    // Keep distinct arcs that would otherwise share one dual edge. Subdivide
    // only multiply used dual edges; this is a topology safeguard, not mesh
    // decimation.
    using Occ = std::pair<int, int>;
    std::unordered_map<uint64_t, std::vector<Occ>> occ;
    for (int i = 0; i < (int)polys.size(); i++)
      for (int k = 0; k < (int)polys[i].v.size(); k++) {
        uint32_t a = polys[i].v[k], b = polys[i].v[(k + 1) % polys[i].v.size()];
        if (!vertex_flags[a] && !vertex_flags[b])
          continue;
        uint64_t key = ((uint64_t)std::min(a, b) << 32) | std::max(a, b);
        occ[key].push_back({i, k});
      }
    std::vector<std::vector<int>> insert(polys.size());
    for (size_t i = 0; i < polys.size(); i++)
      insert[i].assign(polys[i].v.size(), -1);
    for (auto &kv : occ)
      if (kv.second.size() > 2) {
        auto list = kv.second;
        while (list.size() >= 2) {
          auto a = list.back();
          list.pop_back();
          V p = edge_hit(polys[a.first].e);
          size_t best = 0;
          double distance = INFINITY;
          for (size_t j = 0; j < list.size(); j++) {
            V q = edge_hit(polys[list[j].first].e);
            double d = dot(sub(p, q), sub(p, q));
            if (d < distance) {
              distance = d;
              best = j;
            }
          }
          auto b = list[best];
          list.erase(list.begin() + best);
          V q = edge_hit(polys[b.first].e), middle;
          for (int k = 0; k < 3; k++)
            middle[k] = (p[k] + q[k]) * .5;
          int id = vertices.size();
          vertices.push_back(middle);
          insert[a.first][a.second] = insert[b.first][b.second] = id;
        }
      }
    std::vector<std::vector<int>> rings(polys.size());
    std::unordered_set<uint64_t> used;
    auto key = [](int a, int b) {
      return ((uint64_t)std::min(a, b) << 32) | (uint32_t)std::max(a, b);
    };
    for (size_t i = 0; i < polys.size(); i++) {
      auto &p = polys[i];
      auto &v = rings[i];
      for (size_t j = 0; j < p.v.size(); j++) {
        v.push_back(p.v[j]);
        if (insert[i][j] >= 0)
          v.push_back(insert[i][j]);
      }
      for (size_t j = 0; j < v.size(); j++)
        used.insert(key(v[j], v[(j + 1) % v.size()]));
    }
    for (size_t i = 0; i < polys.size(); i++) {
      auto &v = rings[i];
      if (v.size() == 3) {
        faces.push_back({v[0], v[1], v[2]});
        continue;
      }
      if (v.size() == 4) {
        bool ac = !used.count(key(v[0], v[2])),
             bd = !used.count(key(v[1], v[3]));
        double a = dot(sub(vertices[v[0]], vertices[v[2]]),
                       sub(vertices[v[0]], vertices[v[2]])),
               b = dot(sub(vertices[v[1]], vertices[v[3]]),
                       sub(vertices[v[1]], vertices[v[3]]));
        if (ac && (a <= b || !bd)) {
          used.insert(key(v[0], v[2]));
          faces.push_back({v[0], v[1], v[2]});
          faces.push_back({v[0], v[2], v[3]});
          continue;
        }
        if (bd) {
          used.insert(key(v[1], v[3]));
          faces.push_back({v[0], v[1], v[3]});
          faces.push_back({v[1], v[2], v[3]});
          continue;
        }
      }
      V point = edge_hit(polys[i].e);
      int centre = vertices.size();
      vertices.push_back(point);
      for (size_t j = 0; j < v.size(); j++)
        faces.push_back({centre, v[j], v[(j + 1) % v.size()]});
    }
  }
};
#ifdef _WIN32
#define REMI_API __declspec(dllexport)
#else
#define REMI_API __attribute__((visibility("default")))
#endif
#ifndef REMI_SOURCE_SHA256
#error Build with CMake so the library records its source hash
#endif
extern "C" {
REMI_API int remi_abi_version() { return 1; }
REMI_API const char *remi_source_sha256() { return REMI_SOURCE_SHA256; }

REMI_API void *remi_create(const double *p, const double *q, const double *m,
                           const double *w, const int32_t *r, int count,
                           const double *lo, const double *vox, int size,
                           const double *ap, const double *sp, const double *op,
                           int ac, double ref, double level, double floor,
                           double power, double gl, double gh) {
  try {
    return new Context(p, q, m, w, r, count, lo, vox, size, ap, sp, op, ac, ref,
                       level, floor, power, gl, gh);
  } catch (...) {
    return nullptr;
  }
}
REMI_API int remi_coarse(void *c, float *d, float *l, int query_logs) {
  try {
    ((Context *)c)->coarse(d, l, query_logs);
    return 0;
  } catch (...) {
    return 1;
  }
}
REMI_API int remi_extract(void *c, float *logs, double sparse, double tolerance,
                          int uniform) {
  try {
    auto &t = *(Context *)c;
    t.logs = logs;
    double at = (double)std::clock() / CLOCKS_PER_SEC;
    t.select(sparse, tolerance, uniform);
    t.times[1] = (double)std::clock() / CLOCKS_PER_SEC - at;
    at = (double)std::clock() / CLOCKS_PER_SEC;
    t.make_leaves();
    t.times[2] = (double)std::clock() / CLOCKS_PER_SEC - at;
    at = (double)std::clock() / CLOCKS_PER_SEC;
    t.polygons();
    t.times[3] = (double)std::clock() / CLOCKS_PER_SEC - at;
    return 0;
  } catch (...) {
    return 1;
  }
}
REMI_API int remi_cleanup(void *c, int minimum, int iterations) {
  try {
    auto &t = *(Context *)c;
    split_vertex_fans(t.vertices, t.faces);
    clean_vectors(t.vertices, t.faces, minimum, iterations);
    return 0;
  } catch (...) {
    return 1;
  }
}
REMI_API int remi_clean_arrays(const double *v, const int64_t *f, int64_t nv,
                               int64_t nf, int minimum, int iterations,
                               double *outv, int64_t *outf, int64_t *sizes) {
  try {
    std::vector<V> vv(nv);
    std::vector<std::array<int, 3>> ff(nf);
    for (int64_t i = 0; i < nv; i++)
      for (int k = 0; k < 3; k++)
        vv[i][k] = v[3 * i + k];
    for (int64_t i = 0; i < nf; i++)
      for (int k = 0; k < 3; k++)
        ff[i][k] = f[3 * i + k];
    clean_vectors(vv, ff, minimum, iterations);
    sizes[0] = vv.size();
    sizes[1] = ff.size();
    for (size_t i = 0; i < vv.size(); i++)
      for (int k = 0; k < 3; k++)
        outv[3 * i + k] = vv[i][k];
    for (size_t i = 0; i < ff.size(); i++)
      for (int k = 0; k < 3; k++)
        outf[3 * i + k] = ff[i][k];
    return 0;
  } catch (...) {
    return 1;
  }
}
REMI_API void remi_profile(void *c, double *out) {
  auto &t = *(Context *)c;
  for (int i = 0; i < 4; i++)
    out[i] = t.times[i];
  out[4] = t.lazy_queries;
  out[5] = t.lazy_fallbacks;
}
REMI_API void remi_sizes(void *c, int64_t *s) {
  auto &t = *(Context *)c;
  s[0] = t.vertices.size();
  s[1] = t.faces.size();
  s[2] = t.queried;
  s[3] = t.expcount;
  s[4] = t.coarse_leaves;
  s[5] = t.fine_leaves;
  s[6] = t.ambiguous;
  s[7] = std::count(t.refine.begin(), t.refine.end(), 1);
}
REMI_API void remi_copy(void *c, double *v, int64_t *f) {
  auto &t = *(Context *)c;
  for (size_t i = 0; i < t.vertices.size(); i++)
    for (int k = 0; k < 3; k++)
      v[3 * i + k] = t.low[k] + (t.vertices[i][k] - 2) * t.vox[k];
  for (size_t i = 0; i < t.faces.size(); i++)
    for (int k = 0; k < 3; k++)
      f[3 * i + k] = t.faces[i][k];
}
REMI_API void remi_free(void *c) { delete (Context *)c; }
}
