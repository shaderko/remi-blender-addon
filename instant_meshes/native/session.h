#pragma once

#include "hierarchy.h"
#include "meshstats.h"
#include "smoothcurve.h"

#include <memory>
#include <set>
#include <tuple>

class BVH;
class Optimizer;

struct RemiStroke {
    int type = 0; // 0 = orientation comb, 1 = output edge guide
    std::vector<CurvePoint> points;
};

struct RemiTopologyStats {
    uint32_t vertex_count = 0;
    uint32_t used_vertex_count = 0;
    uint32_t face_count = 0;
    uint32_t components = 0;
    uint32_t boundary_edges = 0;
    uint32_t nonmanifold_edges = 0;
    uint32_t degenerate_faces = 0;
};

class InteractiveSession {
public:
    InteractiveSession(
        MatrixXf vertices,
        MatrixXu faces,
        int target_faces,
        bool pure_quad,
        Float crease_angle,
        bool extrinsic,
        bool align_boundaries,
        bool deterministic,
        int rosy = 4,
        int posy = 4,
        int smooth_iterations = 2);
    ~InteractiveSession();

    InteractiveSession(const InteractiveSession &) = delete;
    InteractiveSession &operator=(const InteractiveSession &) = delete;

    void set_strokes(std::vector<RemiStroke> strokes);
    void clear_strokes();

    void start_orientation();
    void start_position();
    void stop();
    bool active();
    Float progress();
    bool position_solved();

    std::tuple<MatrixXf, MatrixXu, MatrixXf> surface_snapshot();
    std::tuple<MatrixXf, MatrixXf, MatrixXf> orientation_snapshot(uint32_t max_points);
    std::tuple<MatrixXf, MatrixXf> position_snapshot(uint32_t max_points);
    std::tuple<MatrixXf, VectorXi> orientation_singularities();
    std::tuple<MatrixXf, MatrixXi> position_singularities();
    std::tuple<MatrixXf, MatrixXu, MatrixXf> extract_mesh();
    RemiTopologyStats source_topology();
    RemiTopologyStats output_topology();

    Float scale() const { return mRes.scale(); }
    Float average_edge_length() const { return (Float) mStats.mAverageEdgeLength; }
    uint32_t stroke_count() const { return (uint32_t) mStrokes.size(); }

private:
    void apply_constraints_locked();
    void stop_locked();
    void update_solve_state_locked();
    std::pair<Vector3f, Vector3f> singularity_position_and_normal(uint32_t face) const;
    std::vector<uint32_t> sample_indices(uint32_t size, uint32_t max_points) const;

    MultiResolutionHierarchy mRes;
    MeshStats mStats;
    std::unique_ptr<BVH> mBVH;
    std::unique_ptr<Optimizer> mOptimizer;
    std::vector<RemiStroke> mStrokes;
    std::set<uint32_t> mCreases;
    RemiTopologyStats mSourceTopology;
    RemiTopologyStats mOutputTopology;
    bool mSolvingOrientation = false;
    bool mSolvingPosition = false;
    bool mPositionSolutionValid = false;
    bool mPureQuad;
    bool mExtrinsic;
    bool mAlignBoundaries;
    bool mDeterministic;
    int mRosy;
    int mPosy;
    int mSmoothIterations;
};
