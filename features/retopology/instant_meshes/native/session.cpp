#include "session.h"

#include "adjacency.h"
#include "bvh.h"
#include "dedge.h"
#include "extract.h"
#include "field.h"
#include "normal.h"
#include "subdivide.h"

#include <cmath>
#include <map>
#include <numeric>
#include <unordered_map>
#include <unordered_set>

int nprocs = -1;

namespace {

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
    int effective_faces = std::max(4, target_faces);
    // Instant Meshes performs a regular 4x subdivision when pure-quad output
    // is requested. Interpret target_faces as the desired final Blender count.
    if (pure_quad && posy == 4)
        effective_faces = std::max(4, effective_faces / 4);
    Float face_area = (Float) (mStats.mSurfaceArea / effective_faces);
    Float target_scale = posy == 4
        ? std::sqrt(face_area)
        : 2 * std::sqrt(face_area * std::sqrt(1.f / 3.f));

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
        if (stroke.points.size() < 2)
            throw std::runtime_error("Each field stroke requires at least two surface points");
        for (const auto &point : stroke.points) {
            if (point.f >= mRes.F().cols())
                throw std::runtime_error("Stroke references an invalid surface face");
        }
        if (!smooth_curve(mBVH.get(), mRes.E2E(), stroke.points, false))
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

    for (const auto &stroke : mStrokes) {
        const auto &curve = stroke.points;
        for (uint32_t i = 0; i < curve.size(); ++i) {
            Vector3f tangent;
            if (i == 0)
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
                Vector3f local_tangent = tangent;
                local_tangent -= local_tangent.dot(normals.col(vertex)) * normals.col(vertex);
                if (local_tangent.squaredNorm() <= RCPOVERFLOW)
                    continue;
                local_tangent.normalize();
                mRes.CQ().col(vertex) = local_tangent;
                mRes.CQw()[vertex] = 1.f;
                if (stroke.type == 1) {
                    mRes.CO().col(vertex) = curve[i].p;
                    mRes.COw()[vertex] = 1.f;
                }
            }
        }
    }
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

std::tuple<MatrixXf, MatrixXu, MatrixXf> InteractiveSession::extract_mesh() {
    std::lock_guard<ordered_lock> lock(mRes.mutex());
    update_solve_state_locked();
    if (mOptimizer && mOptimizer->active())
        throw std::runtime_error("Wait for the field solve to finish before extracting");
    if (!mPositionSolutionValid)
        throw std::runtime_error(
            "The position field has not been solved. Solve orientation and position before extracting");
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
    if (mOutputTopology.degenerate_faces > 0)
        throw std::runtime_error("Instant Meshes extraction returned degenerate faces");
    if (mSourceTopology.nonmanifold_edges == 0 && mOutputTopology.nonmanifold_edges > 0)
        throw std::runtime_error(
            "Instant Meshes extraction introduced non-manifold edges; result was rejected");
    if (mSourceTopology.boundary_edges == 0 && mOutputTopology.boundary_edges > 0)
        throw std::runtime_error(
            "Instant Meshes extraction opened holes in a watertight source; result was rejected");
    uint32_t allowed_components = std::max(
        8u, std::max(1u, mSourceTopology.components) * 4u);
    if (mOutputTopology.components > allowed_components)
        throw std::runtime_error(
            "Instant Meshes extraction fragmented into too many disconnected components; result was rejected");
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
