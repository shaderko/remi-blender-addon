#include "session.h"

#include "adjacency.h"
#include "bvh.h"
#include "dedge.h"
#include "extract.h"
#include "field.h"
#include "normal.h"
#include "subdivide.h"

#include <cmath>
#include <algorithm>
#include <map>
#include <limits>
#include <numeric>
#include <unordered_map>
#include <unordered_set>

int nprocs = -1;

namespace {

// Sparse, confidence-weighted suggestions should not turn into a hard global
// direction when the hierarchy collapses an entire circular feature to a node.
// Keep upstream propagation unchanged for legacy/manual-only sessions.
void propagate_soft_constraints(MultiResolutionHierarchy &res, int rosy, int posy) {
    auto orient = rosy == 2 ? compat_orientation_extrinsic_2 :
                  rosy == 4 ? compat_orientation_extrinsic_4 : compat_orientation_extrinsic_6;
    auto position = posy == 4 ? compat_position_extrinsic_4 : compat_position_extrinsic_3;
    for (int level = 0; level + 1 < res.levels(); ++level) {
        for (uint32_t i = 0; i < res.size(level + 1); ++i) {
            Vector2u upper = res.toUpper(level).col(i);
            uint32_t a = upper[0], b = upper[1];
            Float qa = res.CQw(level)[a], qb = b == INVALID ? 0.f : res.CQw(level)[b];
            Float oa = res.COw(level)[a], ob = b == INVALID ? 0.f : res.COw(level)[b];
            // An explicit/boundary constraint takes priority over suggestions.
            if (qa == 1.f || qb == 1.f) {
                if (qa < 1.f) qa = 0;
                if (qb < 1.f) qb = 0;
            }
            if (oa == 1.f || ob == 1.f) {
                if (oa < 1.f) oa = 0;
                if (ob < 1.f) ob = 0;
            }
            Vector3f q = Vector3f::Zero(), o = Vector3f::Zero();
            if (qa > 0 && qb > 0) {
                auto pair = orient(res.CQ(level).col(a), res.N(level).col(a),
                                   res.CQ(level).col(b), res.N(level).col(b));
                q = pair.first * qa + pair.second * qb;
            } else if (qa > 0) q = res.CQ(level).col(a);
            else if (qb > 0) q = res.CQ(level).col(b);
            Float qw = std::max(qa, qb), ow = std::max(oa, ob);
            const Vector3f n = res.N(level + 1).col(i), v = res.V(level + 1).col(i);
            q -= n.dot(q) * n;
            if (q.squaredNorm() > RCPOVERFLOW) q.normalize();
            else qw = 0;
            if (oa > 0 && ob > 0) {
                auto pair = position(res.V(level).col(a), res.N(level).col(a), res.CQ(level).col(a), res.CO(level).col(a),
                                     res.V(level).col(b), res.N(level).col(b), res.CQ(level).col(b), res.CO(level).col(b),
                                     res.scale(), 1.f / res.scale());
                o = (pair.first * oa + pair.second * ob) / (oa + ob);
            } else if (oa > 0) o = res.CO(level).col(a);
            else if (ob > 0) o = res.CO(level).col(b);
            o -= n.dot(o - v) * n;
            res.CQ(level + 1).col(i) = q;
            res.CO(level + 1).col(i) = o;
            res.CQw(level + 1)[i] = qw < 1.f ? qw * 0.5f : qw;
            res.COw(level + 1)[i] = ow < 1.f ? ow * 0.5f : ow;
        }
    }
}

uint64_t edge_key(uint32_t first, uint32_t second) {
    if (first > second)
        std::swap(first, second);
    return (uint64_t(first) << 32) | uint64_t(second);
}

RemiTopologyStats compute_topology(uint32_t vertex_count, const MatrixXu &faces) {
    RemiTopologyStats stats;
    stats.vertex_count = vertex_count;
    stats.face_count = (uint32_t) faces.cols();

    std::vector<uint32_t> parent(vertex_count);
    std::iota(parent.begin(), parent.end(), 0u);
    std::vector<uint8_t> used(vertex_count, 0);
    std::unordered_map<uint64_t, uint32_t> edge_counts;
    edge_counts.reserve((size_t) faces.cols() * (size_t) faces.rows());

    auto find_root = [&](uint32_t value) {
        uint32_t root = value;
        while (parent[root] != root)
            root = parent[root];
        while (parent[value] != value) {
            uint32_t next = parent[value];
            parent[value] = root;
            value = next;
        }
        return root;
    };

    auto unite = [&](uint32_t first, uint32_t second) {
        uint32_t root_first = find_root(first);
        uint32_t root_second = find_root(second);
        if (root_first != root_second)
            parent[root_second] = root_first;
    };

    for (uint32_t face_index = 0; face_index < (uint32_t) faces.cols(); ++face_index) {
        uint32_t face_size = (uint32_t) faces.rows();
        if (face_size == 4 && faces(2, face_index) == faces(3, face_index))
            face_size = 3;

        std::unordered_set<uint32_t> unique;
        unique.reserve(face_size);
        bool invalid = false;
        for (uint32_t corner = 0; corner < face_size; ++corner) {
            uint32_t vertex = faces(corner, face_index);
            if (vertex >= vertex_count) {
                invalid = true;
                break;
            }
            unique.insert(vertex);
            used[vertex] = 1;
        }
        if (invalid || unique.size() != face_size) {
            ++stats.degenerate_faces;
            continue;
        }

        for (uint32_t corner = 0; corner < face_size; ++corner) {
            uint32_t first = faces(corner, face_index);
            uint32_t second = faces((corner + 1) % face_size, face_index);
            ++edge_counts[edge_key(first, second)];
            unite(first, second);
        }
    }

    std::unordered_set<uint32_t> roots;
    for (uint32_t vertex = 0; vertex < vertex_count; ++vertex) {
        if (!used[vertex])
            continue;
        ++stats.used_vertex_count;
        roots.insert(find_root(vertex));
    }
    stats.components = (uint32_t) roots.size();
    for (const auto &edge : edge_counts) {
        if (edge.second == 1)
            ++stats.boundary_edges;
        else if (edge.second > 2)
            ++stats.nonmanifold_edges;
    }
    return stats;
}

} // namespace

Float InteractiveSession::target_scale_for(int face_count) const {
    int effective_faces = std::max(4, face_count);
    // Instant Meshes performs a regular 4x subdivision when pure-quad output
    // is requested. Interpret target_faces as the desired final Blender count.
    if (mPureQuad && mPosy == 4)
        effective_faces = std::max(4, effective_faces / 4);
    Float face_area = (Float) (mStats.mSurfaceArea / effective_faces);
    return mPosy == 4
        ? std::sqrt(face_area)
        : 2 * std::sqrt(face_area * std::sqrt(1.f / 3.f));
}

InteractiveSession::InteractiveSession(
    MatrixXf vertices,
    MatrixXu faces,
    int target_faces,
    bool pure_quad,
    Float crease_angle,
    bool extrinsic,
    bool align_boundaries,
    bool deterministic,
    int rosy,
    int posy,
    int smooth_iterations)
    : mPureQuad(pure_quad),
      mExtrinsic(extrinsic),
      mAlignBoundaries(align_boundaries),
      mDeterministic(deterministic),
      mRosy(rosy),
      mPosy(posy),
      mSmoothIterations(smooth_iterations) {
    if (vertices.rows() != 3 || vertices.cols() < 3)
        throw std::runtime_error("Vertices must contain at least three 3D points");
    if (faces.rows() != 3 || faces.cols() < 1)
        throw std::runtime_error("Interactive Instant Meshes requires triangular faces");
    if (rosy != 2 && rosy != 4 && rosy != 6)
        throw std::runtime_error("RoSy must be 2, 4, or 6");
    if (posy != 3 && posy != 4)
        throw std::runtime_error("PoSy must be 3 or 4");

    for (uint32_t face = 0; face < (uint32_t) faces.cols(); ++face) {
        for (uint32_t corner = 0; corner < 3; ++corner) {
            if (faces(corner, face) >= (uint32_t) vertices.cols())
                throw std::runtime_error("A face references a vertex outside the input array");
        }
    }
    mSourceTopology = compute_topology((uint32_t) vertices.cols(), faces);

    mStats = compute_mesh_stats(faces, vertices, deterministic);
    mTargetFaces = target_faces;
    Float target_scale = target_scale_for(target_faces);

    VectorXb boundary, nonmanifold;
    if (mStats.mMaximumEdgeLength * 2 > target_scale ||
        mStats.mMaximumEdgeLength > mStats.mAverageEdgeLength * 2) {
        VectorXu v2e, e2e;
        build_dedge(faces, vertices, v2e, e2e, boundary, nonmanifold);
        subdivide(
            faces,
            vertices,
            v2e,
            e2e,
            boundary,
            nonmanifold,
            std::min(target_scale / 2, (Float) mStats.mAverageEdgeLength * 2),
            deterministic);
        mStats = compute_mesh_stats(faces, vertices, deterministic);
    }

    mRes.setF(std::move(faces));
    mRes.setV(std::move(vertices));

    VectorXu v2e, e2e;
    build_dedge(mRes.F(), mRes.V(), v2e, e2e, boundary, nonmanifold);
    AdjacencyMatrix adjacency = generate_adjacency_matrix_uniform(
        mRes.F(), v2e, e2e, nonmanifold);

    MatrixXf normals;
    if (crease_angle >= 0)
        generate_crease_normals(
            mRes.F(), mRes.V(), v2e, e2e, boundary, nonmanifold,
            crease_angle, normals, mCreases);
    else
        generate_smooth_normals(
            mRes.F(), mRes.V(), v2e, e2e, nonmanifold, normals);

    VectorXf areas;
    compute_dual_vertex_areas(
        mRes.F(), mRes.V(), v2e, e2e, nonmanifold, areas);

    mRes.setAdj(std::move(adjacency));
    mRes.setE2E(std::move(e2e));
    mRes.setN(std::move(normals));
    mRes.setA(std::move(areas));
    mRes.setScale(target_scale);
    mRes.build(deterministic);
    mRes.resetSolution();

    mBVH.reset(new BVH(&mRes.F(), &mRes.V(), &mRes.N(), mStats.mAABB));
    mBVH->build();

    mOptimizer.reset(new Optimizer(mRes, true));
    mOptimizer->setRoSy(mRosy);
    mOptimizer->setPoSy(mPosy);
    mOptimizer->setExtrinsic(mExtrinsic);

    std::lock_guard<ordered_lock> lock(mRes.mutex());
    apply_constraints_locked();
}

InteractiveSession::~InteractiveSession() {
    if (mOptimizer)
        mOptimizer->shutdown();
}

void InteractiveSession::stop_locked() {
    if (mOptimizer && mOptimizer->active())
        mOptimizer->stop();
    mSolvingOrientation = false;
    mSolvingPosition = false;
}

void InteractiveSession::update_solve_state_locked() {
    if (!mOptimizer || mOptimizer->active())
        return;
    if (mSolvingPosition) {
        mPositionSolutionValid = true;
        mSolvingPosition = false;
    }
    mSolvingOrientation = false;
}

void InteractiveSession::stop() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    stop_locked();
}

bool InteractiveSession::active() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    bool result = mOptimizer && mOptimizer->active();
    if (!result)
        update_solve_state_locked();
    return result;
}

Float InteractiveSession::progress() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    return mOptimizer ? mOptimizer->progress() : 1.f;
}

bool InteractiveSession::position_solved() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    update_solve_state_locked();
    return mPositionSolutionValid;
}

void InteractiveSession::set_strokes(std::vector<RemiStroke> strokes) {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    stop_locked();
    mPositionSolutionValid = false;
    for (auto &stroke : strokes) {
        if (stroke.type != 0 && stroke.type != 1)
            throw std::runtime_error("Unknown field stroke type");
        if (!std::isfinite(stroke.weight) || stroke.weight <= 0 || stroke.weight > 1)
            throw std::runtime_error("Stroke weights must be in (0, 1]");
        if (stroke.automatic)
            stroke.weight = std::min(stroke.weight, (Float) 0.95);
        if (stroke.points.size() < 2)
            throw std::runtime_error("Each field stroke requires at least two surface points");
        for (const auto &point : stroke.points) {
            if (!point.p.allFinite() || !point.n.allFinite() || point.n.squaredNorm() < RCPOVERFLOW)
                throw std::runtime_error("Stroke points and normals must be finite and normals nonzero");
            if (point.f >= mRes.F().cols())
                throw std::runtime_error("Stroke references an invalid surface face");
        }
        // A periodic guide is not a shortest-path annotation. The upstream
        // open-stroke smoother shrinks circles and rejects matching end faces.
        // Closed/fitted paths arrive surface-sampled and must retain their shape.
        Float epsilon = std::max((Float) 1e-12, average_edge_length() * (Float) 1e-4);
        if ((stroke.points.front().p - stroke.points.back().p).norm() < epsilon) {
            stroke.closed = true;
            stroke.points.pop_back();
        }
        if (stroke.closed && stroke.points.size() < 3)
            throw std::runtime_error("A closed guide requires at least three distinct surface points");
        if (!stroke.closed && !stroke.automatic &&
            !smooth_curve(mBVH.get(), mRes.E2E(), stroke.points, false))
            throw std::runtime_error("A field stroke could not be projected across the surface");
    }
    mStrokes = std::move(strokes);
    apply_constraints_locked();
}

void InteractiveSession::clear_strokes() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    stop_locked();
    mPositionSolutionValid = false;
    mStrokes.clear();
    apply_constraints_locked();
}

void InteractiveSession::apply_constraints_locked() {
    const MatrixXu &faces = mRes.F();
    const MatrixXf &vertices = mRes.V();
    const MatrixXf &normals = mRes.N();
    const VectorXu &e2e = mRes.E2E();
    mRes.clearConstraints();
    mRes.CQ().setZero();
    mRes.CO().setZero();

    // Automatic guides are soft suggestions. Accumulate compatible directions
    // instead of allowing sample order to choose the field at shared vertices.
    VectorXf anchor_distance = VectorXf::Constant(vertices.cols(), std::numeric_limits<Float>::infinity());
    MatrixXf automatic_sum = MatrixXf::Zero(2, vertices.cols());
    auto apply_stroke = [&](const RemiStroke &stroke) {
        const auto &curve = stroke.points;
        for (uint32_t i = 0; i < curve.size(); ++i) {
            Vector3f tangent;
            if (stroke.closed)
                tangent = curve[(i + 1) % curve.size()].p - curve[(i + curve.size() - 1) % curve.size()].p;
            else if (i == 0)
                tangent = curve[1].p - curve[0].p;
            else if (i + 1 == curve.size())
                tangent = curve[i].p - curve[i - 1].p;
            else
                tangent = curve[i + 1].p - curve[i - 1].p;
            if (tangent.squaredNorm() <= RCPOVERFLOW)
                continue;
            tangent.normalize();

            for (int corner = 0; corner < 3; ++corner) {
                uint32_t vertex = faces(corner, curve[i].f);
                Vector3f local = tangent - tangent.dot(normals.col(vertex)) * normals.col(vertex);
                if (local.squaredNorm() <= RCPOVERFLOW)
                    continue;
                local.normalize();
                if (stroke.automatic) {
                    // Average in RoSy space: q and its 90-degree rotation are
                    // equivalent for quads. This is independent of stroke order.
                    Vector3f n = normals.col(vertex);
                    int axis = 0;
                    if (std::abs(n[1]) < std::abs(n[axis])) axis = 1;
                    if (std::abs(n[2]) < std::abs(n[axis])) axis = 2;
                    Vector3f u = Vector3f::Zero();
                    u[axis] = 1;
                    u = (u - n.dot(u) * n).normalized();
                    Vector3f v = n.cross(u);
                    Float angle = mRosy * std::atan2(local.dot(v), local.dot(u));
                    automatic_sum(0, vertex) += stroke.weight * std::cos(angle);
                    automatic_sum(1, vertex) += stroke.weight * std::sin(angle);
                    if (automatic_sum.col(vertex).squaredNorm() < RCPOVERFLOW) {
                        mRes.CQw()[vertex] = 0;
                        continue;
                    }
                    angle = std::atan2(automatic_sum(1, vertex), automatic_sum(0, vertex)) / mRosy;
                    local = u * std::cos(angle) + v * std::sin(angle);
                }
                mRes.CQ().col(vertex) = local;
                mRes.CQw()[vertex] = stroke.automatic
                    ? std::max(mRes.CQw()[vertex], stroke.weight) : 1.f;
                if (!stroke.automatic && stroke.type == 0 && mRes.COw()[vertex] < 1.f)
                    mRes.COw()[vertex] = 0.f; // manual comb supersedes an automatic edge
                Float distance = (vertices.col(vertex) - curve[i].p).squaredNorm();
                if (stroke.type == 1 && (!stroke.automatic || distance < anchor_distance[vertex])) {
                    mRes.CO().col(vertex) = curve[i].p;
                    mRes.COw()[vertex] = stroke.automatic ? stroke.weight : 1.f;
                    anchor_distance[vertex] = distance;
                }
            }
        }
    };

    for (const auto &stroke : mStrokes)
        if (stroke.automatic)
            apply_stroke(stroke);

    if (mAlignBoundaries) {
        for (uint32_t edge_index = 0; edge_index < 3 * faces.cols(); ++edge_index) {
            if (e2e[edge_index] != INVALID)
                continue;
            uint32_t i0 = faces(edge_index % 3, edge_index / 3);
            uint32_t i1 = faces((edge_index + 1) % 3, edge_index / 3);
            Vector3f p0 = vertices.col(i0), p1 = vertices.col(i1);
            Vector3f edge = p1 - p0;
            if (edge.squaredNorm() <= RCPOVERFLOW)
                continue;
            edge.normalize();
            mRes.CO().col(i0) = p0;
            mRes.CO().col(i1) = p1;
            mRes.CQ().col(i0) = mRes.CQ().col(i1) = edge;
            mRes.CQw()[i0] = mRes.CQw()[i1] = 1.f;
            mRes.COw()[i0] = mRes.COw()[i1] = 1.f;
        }
    }

    for (const auto &stroke : mStrokes)
        if (!stroke.automatic)
            apply_stroke(stroke);
    bool automatic = std::any_of(mStrokes.begin(), mStrokes.end(), [](const RemiStroke &s) { return s.automatic; });
    if (automatic)
        propagate_soft_constraints(mRes, mRosy, mPosy);
    else
        mRes.propagateConstraints(mRosy, mPosy);
}

void InteractiveSession::start_orientation() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    stop_locked();
    mPositionSolutionValid = false;
    mSolvingOrientation = true;
    mOptimizer->optimizeOrientations(-1);
    mOptimizer->notify();
}

void InteractiveSession::start_position() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    stop_locked();
    mPositionSolutionValid = false;
    mSolvingPosition = true;
    mOptimizer->optimizePositions(-1);
    mOptimizer->notify();
}

std::vector<uint32_t> InteractiveSession::sample_indices(
    uint32_t size, uint32_t max_points) const {
    uint32_t count = max_points == 0 ? size : std::min(size, max_points);
    std::vector<uint32_t> result;
    result.reserve(count);
    if (count == 0)
        return result;
    for (uint32_t i = 0; i < count; ++i)
        result.push_back((uint64_t) i * size / count);
    return result;
}

std::tuple<MatrixXf, MatrixXu, MatrixXf> InteractiveSession::surface_snapshot() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    return std::make_tuple(mRes.V(), mRes.F(), mRes.N());
}

void InteractiveSession::save_solution() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    update_solve_state_locked();
    if (mOptimizer->active() || !mPositionSolutionValid)
        throw std::runtime_error("Only a completed field solution can be saved");
    mSavedQ.clear();
    mSavedO.clear();
    for (int level = 0; level < mRes.levels(); ++level) {
        mSavedQ.push_back(mRes.Q(level));
        mSavedO.push_back(mRes.O(level));
    }
    mSavedTopology = mOutputTopology;
    mSavedWarnings = mOutputWarnings;
}

void InteractiveSession::restore_solution() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    if (mOptimizer->active() || mSavedQ.size() != (size_t) mRes.levels())
        throw std::runtime_error("No compatible saved field solution is available");
    for (int level = 0; level < mRes.levels(); ++level) {
        mRes.Q(level) = std::move(mSavedQ[level]);
        mRes.O(level) = std::move(mSavedO[level]);
    }
    mSavedQ.clear();
    mSavedO.clear();
    mOutputTopology = mSavedTopology;
    mOutputWarnings = std::move(mSavedWarnings);
    mPositionSolutionValid = true;
    mSolvingOrientation = mSolvingPosition = false;
    mRes.setFrozenQ(false);
    mRes.setFrozenO(false);
}

void InteractiveSession::discard_saved_solution() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    mSavedQ.clear();
    mSavedO.clear();
    mSavedWarnings.clear();
}

std::tuple<MatrixXf, MatrixXf, VectorXu> InteractiveSession::stroke_snapshot(uint32_t index) {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    if (index >= mStrokes.size())
        throw std::runtime_error("Invalid guide index");
    const auto &points = mStrokes[index].points;
    MatrixXf positions(3, points.size()), normals(3, points.size());
    VectorXu faces(points.size());
    for (uint32_t i = 0; i < points.size(); ++i) {
        positions.col(i) = points[i].p;
        normals.col(i) = points[i].n;
        faces[i] = points[i].f;
    }
    return std::make_tuple(positions, normals, faces);
}

std::tuple<MatrixXf, VectorXf, MatrixXf, VectorXf> InteractiveSession::constraint_snapshot(int level) {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    if (level < 0 || level >= mRes.levels())
        throw std::runtime_error("Invalid hierarchy level");
    return std::make_tuple(mRes.CQ(level), mRes.CQw(level), mRes.CO(level), mRes.COw(level));
}

std::tuple<MatrixXf, MatrixXf, MatrixXf>
InteractiveSession::orientation_snapshot(uint32_t max_points) {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    auto indices = sample_indices(mRes.size(), max_points);
    MatrixXf positions(3, indices.size()), normals(3, indices.size()), field(3, indices.size());
    for (uint32_t i = 0; i < indices.size(); ++i) {
        positions.col(i) = mRes.V().col(indices[i]);
        normals.col(i) = mRes.N().col(indices[i]);
        field.col(i) = mRes.Q().col(indices[i]);
    }
    return std::make_tuple(std::move(positions), std::move(normals), std::move(field));
}

std::tuple<MatrixXf, MatrixXf>
InteractiveSession::position_snapshot(uint32_t max_points) {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    auto indices = sample_indices(mRes.size(), max_points);
    MatrixXf positions(3, indices.size()), normals(3, indices.size());
    for (uint32_t i = 0; i < indices.size(); ++i) {
        positions.col(i) = mRes.O().col(indices[i]);
        normals.col(i) = mRes.N().col(indices[i]);
    }
    return std::make_tuple(std::move(positions), std::move(normals));
}

std::pair<Vector3f, Vector3f>
InteractiveSession::singularity_position_and_normal(uint32_t face) const {
    Vector3f position = Vector3f::Zero(), normal = Vector3f::Zero();
    for (int corner = 0; corner < 3; ++corner) {
        uint32_t vertex = mRes.F()(corner, face);
        position += mRes.V().col(vertex);
        normal += mRes.N().col(vertex);
    }
    return std::make_pair(position / 3.f, normal.normalized());
}

std::tuple<MatrixXf, VectorXi> InteractiveSession::orientation_singularities() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    std::map<uint32_t, uint32_t> values;
    compute_orientation_singularities(mRes, values, mExtrinsic, mRosy);
    MatrixXf positions(3, values.size());
    VectorXi indices(values.size());
    uint32_t slot = 0;
    for (const auto &value : values) {
        positions.col(slot) = singularity_position_and_normal(value.first).first;
        indices[slot] = (int32_t) value.second;
        ++slot;
    }
    return std::make_tuple(std::move(positions), std::move(indices));
}

std::tuple<MatrixXf, MatrixXi> InteractiveSession::position_singularities() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    std::map<uint32_t, uint32_t> orientation_values;
    std::map<uint32_t, Vector2i> position_values;
    compute_orientation_singularities(mRes, orientation_values, mExtrinsic, mRosy);
    compute_position_singularities(
        mRes, orientation_values, position_values, mExtrinsic, mRosy, mPosy);
    MatrixXf positions(3, position_values.size());
    MatrixXi indices(2, position_values.size());
    uint32_t slot = 0;
    for (const auto &value : position_values) {
        positions.col(slot) = singularity_position_and_normal(value.first).first;
        indices.col(slot) = value.second;
        ++slot;
    }
    return std::make_tuple(std::move(positions), std::move(indices));
}

void InteractiveSession::set_target_faces(int target_faces) {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    if (mOptimizer && mOptimizer->active())
        throw std::runtime_error("Wait for the field solve to finish before changing the face count");
    mTargetFaces = target_faces;
    // The lattice spacing is the only knob that changes; the multi-resolution
    // hierarchy itself is scale independent and stays built. Re-seed the
    // fields on the new lattice so extraction really reaches the requested
    // density, then let the caller re-run the field solve.
    mRes.setScale(target_scale_for(target_faces));
    mRes.resetSolution();
    apply_constraints_locked();
    mPositionSolutionValid = false;
    mOutputWarnings.clear();
}

std::vector<std::string> InteractiveSession::output_warnings() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    return mOutputWarnings;
}

bool InteractiveSession::needs_input_subdivision(int target_faces) const {
    // Same test the constructor uses to decide whether the input mesh is too
    // coarse for the requested density. A live session cannot refine its own
    // input, so the caller rebuilds when this returns true.
    Float target_scale = target_scale_for(target_faces);
    return mStats.mMaximumEdgeLength * 2 > target_scale ||
           mStats.mMaximumEdgeLength > mStats.mAverageEdgeLength * 2;
}

std::tuple<MatrixXf, MatrixXu, MatrixXf> InteractiveSession::extract_mesh(bool strict) {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    update_solve_state_locked();
    if (mOptimizer && mOptimizer->active())
        throw std::runtime_error("Wait for the field solve to finish before extracting");
    if (!mPositionSolutionValid)
        throw std::runtime_error(
            "The position field has not been solved. Solve orientation and position before extracting");
    mOutputWarnings.clear();
    std::vector<std::vector<TaggedLink>> adjacency;
    MatrixXf vertices, normals, face_normals;
    std::set<uint32_t> crease_out;
    extract_graph(
        mRes, mExtrinsic, mRosy, mPosy, adjacency, vertices, normals,
        mCreases, crease_out, mDeterministic);
    MatrixXu faces;
    extract_faces(
        adjacency, vertices, normals, face_normals, faces, mPosy,
        mRes.scale(), crease_out, true, mPureQuad, mBVH.get(),
        mSmoothIterations);
    mOutputTopology = compute_topology((uint32_t) vertices.cols(), faces);
    if (mOutputTopology.face_count == 0 || mOutputTopology.used_vertex_count == 0)
        throw std::runtime_error("Instant Meshes extraction returned an empty mesh");
    // Quality defects below are advisory: the caller decides whether to keep
    // the result. Strict mode (used by unattended batch runs) still refuses.
    if (mOutputTopology.degenerate_faces > 0) {
        std::string message = "Instant Meshes extraction returned degenerate faces";
        if (strict)
            throw std::runtime_error(message);
        mOutputWarnings.push_back(
            message + " (" + std::to_string(mOutputTopology.degenerate_faces) + ")");
    }
    if (mSourceTopology.nonmanifold_edges == 0 && mOutputTopology.nonmanifold_edges > 0) {
        std::string message =
            "Instant Meshes extraction introduced non-manifold edges";
        if (strict)
            throw std::runtime_error(message + "; result was rejected");
        mOutputWarnings.push_back(
            message + " (" + std::to_string(mOutputTopology.nonmanifold_edges) + ")");
    }
    if (mSourceTopology.boundary_edges == 0 && mOutputTopology.boundary_edges > 0) {
        std::string message =
            "Instant Meshes extraction opened holes in a watertight source";
        if (strict)
            throw std::runtime_error(message + "; result was rejected");
        mOutputWarnings.push_back(
            message + " (" + std::to_string(mOutputTopology.boundary_edges) + ")");
    }
    uint32_t allowed_components = std::max(
        8u, std::max(1u, mSourceTopology.components) * 4u);
    if (mOutputTopology.components > allowed_components) {
        std::string message =
            "Instant Meshes extraction fragmented into too many disconnected components";
        if (strict)
            throw std::runtime_error(message + "; result was rejected");
        mOutputWarnings.push_back(
            message + " (" + std::to_string(mOutputTopology.components) + ")");
    }
    return std::make_tuple(
        std::move(vertices), std::move(faces), std::move(normals));
}

RemiTopologyStats InteractiveSession::source_topology() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    return mSourceTopology;
}

RemiTopologyStats InteractiveSession::output_topology() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    return mOutputTopology;
}
