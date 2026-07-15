#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>

#include "xatlas.h"

namespace py = pybind11;

namespace {

struct AtlasDeleter {
    void operator()(xatlas::Atlas *atlas) const { xatlas::Destroy(atlas); }
};

using AtlasPtr = std::unique_ptr<xatlas::Atlas, AtlasDeleter>;

void pack_at_exact_resolution(xatlas::Atlas *atlas, xatlas::PackOptions options)
{
    xatlas::PackCharts(atlas, options);
    if (options.resolution == 0)
        return;
    if (atlas->atlasCount == 1 &&
        atlas->width == options.resolution && atlas->height == options.resolution)
        return;

    const float width = static_cast<float>(std::max(uint32_t(1), atlas->width));
    const float height = static_cast<float>(std::max(uint32_t(1), atlas->height));
    const float target = static_cast<float>(options.resolution);
    const float fit_scale = std::min(target / width, target / height);
    options.texelsPerUnit = std::max(1.0e-6f, atlas->texelsPerUnit * fit_scale * 0.98f);

    // A fixed texel scale makes xatlas honor resolution exactly. Random chart
    // placement may occasionally need a little more headroom than the
    // estimated rectangular atlas, so back off until everything fits one tile.
    bool found_single_atlas = false;
    for (int attempt = 0; attempt < 8; ++attempt) {
        xatlas::PackCharts(atlas, options);
        if (atlas->atlasCount == 1) {
            found_single_atlas = true;
            break;
        }
        options.texelsPerUnit *= 0.90f;
    }
    if (!found_single_atlas)
        return;
    if (options.bruteForce)
        return;

    // Maximize scale inside the fixed square. The first estimate minimizes a
    // free-aspect rectangle, so it can leave room in the square's short axis.
    // Bracket the one-atlas limit, then refine it with deterministic searches.
    float low = options.texelsPerUnit;
    float high = low;
    bool found_upper_bound = false;
    for (int attempt = 0; attempt < 3; ++attempt) {
        high = low * 1.25f;
        options.texelsPerUnit = high;
        xatlas::PackCharts(atlas, options);
        if (atlas->atlasCount == 1) {
            low = high;
        } else {
            found_upper_bound = true;
            break;
        }
    }
    if (found_upper_bound) {
        const int refinement_count = atlas->chartCount > 256 ? 1 : 2;
        for (int attempt = 0; attempt < refinement_count; ++attempt) {
            const float middle = (low + high) * 0.5f;
            options.texelsPerUnit = middle;
            xatlas::PackCharts(atlas, options);
            if (atlas->atlasCount == 1)
                low = middle;
            else
                high = middle;
        }
        options.texelsPerUnit = low;
        xatlas::PackCharts(atlas, options);
    }
}

py::dict pack_uvs(
    const py::array_t<float, py::array::c_style | py::array::forcecast> &uvs,
    const py::array_t<uint32_t, py::array::c_style | py::array::forcecast> &triangles,
    const py::array_t<uint32_t, py::array::c_style | py::array::forcecast> &chart_ids,
    uint32_t resolution,
    uint32_t padding,
    bool brute_force,
    bool rotate,
    bool rotate_to_axis,
    bool bilinear,
    bool block_align)
{
    if (uvs.ndim() != 2 || uvs.shape(1) != 2)
        throw std::invalid_argument("uvs must have shape (loop_count, 2)");
    if (triangles.ndim() != 2 || triangles.shape(1) != 3)
        throw std::invalid_argument("triangles must have shape (triangle_count, 3)");
    if (chart_ids.ndim() != 1 || chart_ids.shape(0) != triangles.shape(0))
        throw std::invalid_argument("chart_ids must have one value per triangle");
    if (resolution == 0)
        throw std::invalid_argument("resolution must be greater than zero");

    const auto uv_count = static_cast<uint32_t>(uvs.shape(0));
    const auto triangle_count = static_cast<uint32_t>(triangles.shape(0));
    if (uv_count == 0 || triangle_count == 0)
        throw std::invalid_argument("cannot pack an empty UV mesh");

    const float *uv_data = uvs.data();
    for (size_t index = 0; index < static_cast<size_t>(uv_count) * 2; ++index) {
        if (!std::isfinite(uv_data[index]))
            throw std::invalid_argument("uvs contain a non-finite value");
    }
    const uint32_t *index_data = triangles.data();
    for (size_t index = 0; index < static_cast<size_t>(triangle_count) * 3; ++index) {
        if (index_data[index] >= uv_count)
            throw std::invalid_argument("triangle contains an out-of-range loop index");
    }

    AtlasPtr atlas(xatlas::Create());
    if (!atlas)
        throw std::runtime_error("xatlas could not allocate an atlas");

    xatlas::UvMeshDecl declaration;
    declaration.vertexUvData = uvs.data();
    declaration.vertexCount = uv_count;
    declaration.vertexStride = sizeof(float) * 2;
    declaration.indexData = triangles.data();
    declaration.indexCount = triangle_count * 3;
    declaration.indexFormat = xatlas::IndexFormat::UInt32;
    declaration.faceMaterialData = chart_ids.data();

    const xatlas::AddMeshError add_error = xatlas::AddUvMesh(atlas.get(), declaration);
    if (add_error != xatlas::AddMeshError::Success)
        throw std::runtime_error(
            std::string("xatlas AddUvMesh failed: ") + xatlas::StringForEnum(add_error));

    xatlas::ChartOptions chart_options;
    xatlas::ComputeCharts(atlas.get(), chart_options);

    xatlas::PackOptions options;
    options.resolution = resolution;
    options.padding = padding;
    options.bruteForce = brute_force;
    options.rotateCharts = rotate;
    options.rotateChartsToAxis = rotate_to_axis;
    options.bilinear = bilinear;
    options.blockAlign = block_align;
    options.createImage = false;
    pack_at_exact_resolution(atlas.get(), options);

    if (atlas->meshCount != 1)
        throw std::runtime_error("xatlas returned an unexpected mesh count");
    if (atlas->width == 0 || atlas->height == 0)
        throw std::runtime_error("xatlas returned an empty atlas");

    py::array_t<float> output({static_cast<py::ssize_t>(uv_count), py::ssize_t(2)});
    auto output_view = output.mutable_unchecked<2>();
    std::vector<bool> written(uv_count, false);
    std::vector<int32_t> atlas_indices(uv_count, -1);
    const xatlas::Mesh &mesh = atlas->meshes[0];
    for (uint32_t vertex_index = 0; vertex_index < mesh.vertexCount; ++vertex_index) {
        const xatlas::Vertex &vertex = mesh.vertexArray[vertex_index];
        if (vertex.xref >= uv_count)
            throw std::runtime_error("xatlas returned an invalid source vertex reference");
        output_view(vertex.xref, 0) = vertex.uv[0] / static_cast<float>(atlas->width);
        output_view(vertex.xref, 1) = vertex.uv[1] / static_cast<float>(atlas->height);
        atlas_indices[vertex.xref] = vertex.atlasIndex;
        written[vertex.xref] = true;
    }
    for (uint32_t index = 0; index < uv_count; ++index) {
        if (!written[index])
            throw std::runtime_error("xatlas did not return every input UV loop");
    }

    py::array_t<int32_t> output_atlas_indices(uv_count);
    auto atlas_index_view = output_atlas_indices.mutable_unchecked<1>();
    for (uint32_t index = 0; index < uv_count; ++index)
        atlas_index_view(index) = atlas_indices[index];

    py::dict result;
    result["uvs"] = std::move(output);
    result["atlas_indices"] = std::move(output_atlas_indices);
    result["width"] = atlas->width;
    result["height"] = atlas->height;
    result["atlas_count"] = atlas->atlasCount;
    result["chart_count"] = atlas->chartCount;
    result["texels_per_unit"] = atlas->texelsPerUnit;
    result["utilization"] = atlas->atlasCount > 0 ? atlas->utilization[0] : 0.0f;
    return result;
}

py::dict unwrap_mesh(
    const py::array_t<float, py::array::c_style | py::array::forcecast> &positions,
    const py::array_t<uint32_t, py::array::c_style | py::array::forcecast> &triangles,
    const py::array_t<uint32_t, py::array::c_style | py::array::forcecast> &material_ids,
    uint32_t resolution,
    uint32_t padding,
    float max_cost,
    uint32_t chart_iterations,
    bool brute_force,
    bool rotate_to_axis)
{
    if (positions.ndim() != 2 || positions.shape(1) != 3)
        throw std::invalid_argument("positions must have shape (vertex_count, 3)");
    if (triangles.ndim() != 2 || triangles.shape(1) != 3)
        throw std::invalid_argument("triangles must have shape (triangle_count, 3)");
    if (material_ids.ndim() != 1 || material_ids.shape(0) != triangles.shape(0))
        throw std::invalid_argument("material_ids must have one value per triangle");
    if (positions.shape(0) == 0 || triangles.shape(0) == 0)
        throw std::invalid_argument("cannot unwrap an empty mesh");
    if (resolution == 0)
        throw std::invalid_argument("resolution must be greater than zero");

    const auto vertex_count = static_cast<uint32_t>(positions.shape(0));
    const auto triangle_count = static_cast<uint32_t>(triangles.shape(0));
    const uint32_t *index_data = triangles.data();
    for (size_t index = 0; index < static_cast<size_t>(triangle_count) * 3; ++index) {
        if (index_data[index] >= vertex_count)
            throw std::invalid_argument("triangle contains an out-of-range vertex index");
    }

    AtlasPtr atlas(xatlas::Create());
    if (!atlas)
        throw std::runtime_error("xatlas could not allocate an atlas");

    xatlas::MeshDecl declaration;
    declaration.vertexPositionData = positions.data();
    declaration.vertexCount = vertex_count;
    declaration.vertexPositionStride = sizeof(float) * 3;
    declaration.indexData = triangles.data();
    declaration.indexCount = triangle_count * 3;
    declaration.indexFormat = xatlas::IndexFormat::UInt32;
    declaration.faceMaterialData = material_ids.data();
    const xatlas::AddMeshError add_error = xatlas::AddMesh(atlas.get(), declaration);
    if (add_error != xatlas::AddMeshError::Success)
        throw std::runtime_error(
            std::string("xatlas AddMesh failed: ") + xatlas::StringForEnum(add_error));

    xatlas::ChartOptions chart_options;
    chart_options.maxCost = max_cost;
    chart_options.maxIterations = std::max(uint32_t(1), chart_iterations);
    chart_options.fixWinding = true;
    xatlas::ComputeCharts(atlas.get(), chart_options);

    xatlas::PackOptions pack_options;
    pack_options.resolution = resolution;
    pack_options.padding = padding;
    pack_options.bilinear = true;
    pack_options.bruteForce = brute_force;
    pack_options.rotateCharts = true;
    pack_options.rotateChartsToAxis = rotate_to_axis;
    pack_options.createImage = false;
    pack_at_exact_resolution(atlas.get(), pack_options);

    if (atlas->meshCount != 1 || atlas->width == 0 || atlas->height == 0)
        throw std::runtime_error("xatlas returned an invalid generated atlas");
    const xatlas::Mesh &mesh = atlas->meshes[0];
    if (mesh.indexCount != triangle_count * 3)
        throw std::runtime_error("xatlas changed the triangle index count");

    py::array_t<float> corner_uvs({
        static_cast<py::ssize_t>(triangle_count), py::ssize_t(3), py::ssize_t(2)});
    py::array_t<int32_t> triangle_charts(triangle_count);
    py::array_t<int32_t> triangle_atlases(triangle_count);
    auto uv_view = corner_uvs.mutable_unchecked<3>();
    auto chart_view = triangle_charts.mutable_unchecked<1>();
    auto atlas_view = triangle_atlases.mutable_unchecked<1>();
    for (uint32_t triangle_index = 0; triangle_index < triangle_count; ++triangle_index) {
        int32_t chart_index = -1;
        int32_t atlas_index = -1;
        for (uint32_t corner = 0; corner < 3; ++corner) {
            const uint32_t output_index = mesh.indexArray[triangle_index * 3 + corner];
            if (output_index >= mesh.vertexCount)
                throw std::runtime_error("xatlas returned an invalid output index");
            const xatlas::Vertex &vertex = mesh.vertexArray[output_index];
            uv_view(triangle_index, corner, 0) =
                vertex.uv[0] / static_cast<float>(atlas->width);
            uv_view(triangle_index, corner, 1) =
                vertex.uv[1] / static_cast<float>(atlas->height);
            if (corner == 0) {
                chart_index = vertex.chartIndex;
                atlas_index = vertex.atlasIndex;
            } else if (vertex.chartIndex != chart_index || vertex.atlasIndex != atlas_index) {
                throw std::runtime_error("xatlas returned a triangle spanning charts");
            }
        }
        chart_view(triangle_index) = chart_index;
        atlas_view(triangle_index) = atlas_index;
    }

    py::dict result;
    result["corner_uvs"] = std::move(corner_uvs);
    result["triangle_chart_ids"] = std::move(triangle_charts);
    result["triangle_atlas_indices"] = std::move(triangle_atlases);
    result["width"] = atlas->width;
    result["height"] = atlas->height;
    result["atlas_count"] = atlas->atlasCount;
    result["chart_count"] = atlas->chartCount;
    result["texels_per_unit"] = atlas->texelsPerUnit;
    result["utilization"] = atlas->atlasCount > 0 ? atlas->utilization[0] : 0.0f;
    return result;
}

} // namespace

PYBIND11_MODULE(_remi_uv_packer, module)
{
    module.doc() = "Remi's xatlas-backed existing-chart UV packer";
    xatlas::SetPrint(nullptr, false);
    module.def(
        "pack_uvs",
        &pack_uvs,
        py::arg("uvs"),
        py::arg("triangles"),
        py::arg("chart_ids"),
        py::arg("resolution"),
        py::arg("padding"),
        py::arg("brute_force") = false,
        py::arg("rotate") = true,
        py::arg("rotate_to_axis") = true,
        py::arg("bilinear") = true,
        py::arg("block_align") = false);
    module.def(
        "unwrap_mesh",
        &unwrap_mesh,
        py::arg("positions"),
        py::arg("triangles"),
        py::arg("material_ids"),
        py::arg("resolution"),
        py::arg("padding"),
        py::arg("max_cost") = 2.0f,
        py::arg("chart_iterations") = 2,
        py::arg("brute_force") = false,
        py::arg("rotate_to_axis") = true);
}
