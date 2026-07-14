#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "session.h"

namespace py = pybind11;

static MatrixXf float_matrix(py::array_t<float, py::array::c_style | py::array::forcecast> array) {
    auto data = array.unchecked<2>();
    if (data.shape(1) != 3)
        throw std::runtime_error("Expected an Nx3 float array");
    MatrixXf result(3, data.shape(0));
    for (py::ssize_t row = 0; row < data.shape(0); ++row)
        for (int column = 0; column < 3; ++column)
            result(column, row) = data(row, column);
    return result;
}

static MatrixXu uint_matrix(py::array_t<int, py::array::c_style | py::array::forcecast> array) {
    auto data = array.unchecked<2>();
    if (data.shape(1) != 3)
        throw std::runtime_error("Expected an Nx3 triangle array");
    MatrixXu result(3, data.shape(0));
    for (py::ssize_t row = 0; row < data.shape(0); ++row)
        for (int column = 0; column < 3; ++column) {
            int value = data(row, column);
            if (value < 0)
                throw std::runtime_error("Face indices cannot be negative");
            result(column, row) = (uint32_t) value;
        }
    return result;
}

template <typename Matrix>
static py::array matrix_array(const Matrix &matrix) {
    using Scalar = typename Matrix::Scalar;
    py::array_t<Scalar> result({
        static_cast<py::ssize_t>(matrix.cols()),
        static_cast<py::ssize_t>(matrix.rows())});
    auto target = result.template mutable_unchecked<2>();
    for (py::ssize_t row = 0; row < matrix.cols(); ++row)
        for (py::ssize_t column = 0; column < matrix.rows(); ++column)
            target(row, column) = matrix(column, row);
    return result;
}

static py::tuple surface_snapshot(InteractiveSession &session) {
    auto value = session.surface_snapshot();
    return py::make_tuple(
        matrix_array(std::get<0>(value)),
        matrix_array(std::get<1>(value)),
        matrix_array(std::get<2>(value)));
}

static py::tuple orientation_snapshot(InteractiveSession &session, uint32_t max_points) {
    auto value = session.orientation_snapshot(max_points);
    return py::make_tuple(
        matrix_array(std::get<0>(value)),
        matrix_array(std::get<1>(value)),
        matrix_array(std::get<2>(value)));
}

static py::tuple position_snapshot(InteractiveSession &session, uint32_t max_points) {
    auto value = session.position_snapshot(max_points);
    return py::make_tuple(matrix_array(std::get<0>(value)), matrix_array(std::get<1>(value)));
}

static py::tuple orientation_singularities(InteractiveSession &session) {
    auto value = session.orientation_singularities();
    return py::make_tuple(matrix_array(std::get<0>(value)), matrix_array(std::get<1>(value)));
}

static py::tuple position_singularities(InteractiveSession &session) {
    auto value = session.position_singularities();
    return py::make_tuple(matrix_array(std::get<0>(value)), matrix_array(std::get<1>(value)));
}

static py::tuple extract_mesh(InteractiveSession &session) {
    py::gil_scoped_release release;
    auto value = session.extract_mesh();
    py::gil_scoped_acquire acquire;
    return py::make_tuple(
        matrix_array(std::get<0>(value)),
        matrix_array(std::get<1>(value)),
        matrix_array(std::get<2>(value)));
}

static py::dict topology_dict(const RemiTopologyStats &stats) {
    py::dict result;
    result["vertices"] = stats.vertex_count;
    result["used_vertices"] = stats.used_vertex_count;
    result["faces"] = stats.face_count;
    result["components"] = stats.components;
    result["boundary_edges"] = stats.boundary_edges;
    result["nonmanifold_edges"] = stats.nonmanifold_edges;
    result["degenerate_faces"] = stats.degenerate_faces;
    return result;
}

static void set_strokes(
    InteractiveSession &session,
    py::array_t<int, py::array::c_style | py::array::forcecast> types,
    py::array_t<int, py::array::c_style | py::array::forcecast> offsets,
    py::array_t<float, py::array::c_style | py::array::forcecast> positions,
    py::array_t<float, py::array::c_style | py::array::forcecast> normals,
    py::array_t<int, py::array::c_style | py::array::forcecast> faces) {
    auto type_data = types.unchecked<1>();
    auto offset_data = offsets.unchecked<1>();
    auto position_data = positions.unchecked<2>();
    auto normal_data = normals.unchecked<2>();
    auto face_data = faces.unchecked<1>();
    if (offset_data.shape(0) != type_data.shape(0) + 1)
        throw std::runtime_error("Stroke offsets must contain stroke_count + 1 entries");
    if (position_data.shape(1) != 3 || normal_data.shape(1) != 3 ||
        position_data.shape(0) != normal_data.shape(0) ||
        position_data.shape(0) != face_data.shape(0))
        throw std::runtime_error("Stroke point arrays have inconsistent shapes");

    std::vector<RemiStroke> result(type_data.shape(0));
    for (py::ssize_t stroke_index = 0; stroke_index < type_data.shape(0); ++stroke_index) {
        int begin = offset_data(stroke_index), end = offset_data(stroke_index + 1);
        if (begin < 0 || end < begin || end > position_data.shape(0))
            throw std::runtime_error("Invalid stroke offsets");
        result[stroke_index].type = type_data(stroke_index);
        result[stroke_index].points.reserve(end - begin);
        for (int point_index = begin; point_index < end; ++point_index) {
            CurvePoint point;
            for (int axis = 0; axis < 3; ++axis) {
                point.p[axis] = position_data(point_index, axis);
                point.n[axis] = normal_data(point_index, axis);
            }
            if (face_data(point_index) < 0)
                throw std::runtime_error("Stroke face indices cannot be negative");
            point.f = (uint32_t) face_data(point_index);
            result[stroke_index].points.push_back(point);
        }
    }
    py::gil_scoped_release release;
    session.set_strokes(std::move(result));
}

PYBIND11_MODULE(_remi_instant_meshes, module) {
    module.doc() = "Headless interactive Instant Meshes session for Remi";
    py::class_<InteractiveSession>(module, "Session")
        .def(py::init([](
            py::array_t<float, py::array::c_style | py::array::forcecast> vertices,
            py::array_t<int, py::array::c_style | py::array::forcecast> faces,
            int target_faces,
            bool pure_quad,
            float crease_angle,
            bool extrinsic,
            bool align_boundaries,
            bool deterministic,
            int rosy,
            int posy,
            int smooth_iterations) {
                MatrixXf native_vertices = float_matrix(vertices);
                MatrixXu native_faces = uint_matrix(faces);
                py::gil_scoped_release release;
                return std::unique_ptr<InteractiveSession>(new InteractiveSession(
                    std::move(native_vertices), std::move(native_faces), target_faces,
                    pure_quad, crease_angle, extrinsic, align_boundaries,
                    deterministic, rosy, posy, smooth_iterations));
            }),
            py::arg("vertices"), py::arg("faces"),
            py::arg("target_faces") = 50000,
            py::arg("pure_quad") = true,
            py::arg("crease_angle") = -1.f,
            py::arg("extrinsic") = true,
            py::arg("align_boundaries") = false,
            py::arg("deterministic") = false,
            py::arg("rosy") = 4,
            py::arg("posy") = 4,
            py::arg("smooth_iterations") = 2)
        .def("set_strokes", &set_strokes)
        .def("clear_strokes", &InteractiveSession::clear_strokes, py::call_guard<py::gil_scoped_release>())
        .def("start_orientation", &InteractiveSession::start_orientation)
        .def("start_position", &InteractiveSession::start_position)
        .def("stop", &InteractiveSession::stop)
        .def_property_readonly("active", &InteractiveSession::active)
        .def_property_readonly("progress", &InteractiveSession::progress)
        .def_property_readonly("position_solved", &InteractiveSession::position_solved)
        .def_property_readonly("scale", &InteractiveSession::scale)
        .def_property_readonly("average_edge_length", &InteractiveSession::average_edge_length)
        .def_property_readonly("stroke_count", &InteractiveSession::stroke_count)
        .def("surface_snapshot", &surface_snapshot)
        .def("orientation_snapshot", &orientation_snapshot, py::arg("max_points") = 5000)
        .def("position_snapshot", &position_snapshot, py::arg("max_points") = 5000)
        .def("orientation_singularities", &orientation_singularities)
        .def("position_singularities", &position_singularities)
        .def_property_readonly("source_topology", [](InteractiveSession &session) {
            return topology_dict(session.source_topology());
        })
        .def_property_readonly("output_topology", [](InteractiveSession &session) {
            return topology_dict(session.output_topology());
        })
        .def("extract", &extract_mesh);
}
