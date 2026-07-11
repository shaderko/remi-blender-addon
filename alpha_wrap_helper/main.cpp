#include <CGAL/Exact_predicates_inexact_constructions_kernel.h>
#include <CGAL/IO/polygon_soup_io.h>
#include <CGAL/Polygon_mesh_processing/bbox.h>
#include <CGAL/Surface_mesh.h>
#include <CGAL/alpha_wrap_3.h>
#include <CGAL/boost/graph/IO/polygon_mesh_io.h>

#include <array>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using Kernel = CGAL::Exact_predicates_inexact_constructions_kernel;
using Point = Kernel::Point_3;
using Mesh = CGAL::Surface_mesh<Point>;

struct Options {
  std::string input;
  std::string output;
  double alpha = 0.0;
  double offset = 0.0;
};

static Options parse_options(int argc, char** argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string argument = argv[i];
    if (argument == "--input" && i + 1 < argc) {
      options.input = argv[++i];
    } else if (argument == "--output" && i + 1 < argc) {
      options.output = argv[++i];
    } else if (argument == "--alpha" && i + 1 < argc) {
      options.alpha = std::stod(argv[++i]);
    } else if (argument == "--offset" && i + 1 < argc) {
      options.offset = std::stod(argv[++i]);
    } else if (argument == "--help") {
      std::cout << "Usage: remi_alpha_wrap --input mesh.ply --output wrap.ply "
                   "--alpha value --offset value\n";
      std::exit(EXIT_SUCCESS);
    } else {
      throw std::runtime_error("Unknown or incomplete argument: " + argument);
    }
  }
  if (options.input.empty() || options.output.empty() || options.alpha <= 0.0 ||
      options.offset <= 0.0) {
    throw std::runtime_error("Input, output, alpha, and offset are required; alpha/offset must be positive");
  }
  return options;
}

int main(int argc, char** argv) {
  try {
    const Options options = parse_options(argc, argv);

    std::vector<Point> points;
    std::vector<std::vector<std::size_t>> polygons;
    if (!CGAL::IO::read_polygon_soup(options.input, points, polygons) || polygons.empty()) {
      throw std::runtime_error("Could not read a non-empty polygon soup from: " + options.input);
    }

    std::vector<std::array<std::size_t, 3>> triangles;
    triangles.reserve(polygons.size() * 2);
    for (const auto& polygon : polygons) {
      if (polygon.size() < 3) {
        continue;
      }
      for (std::size_t i = 1; i + 1 < polygon.size(); ++i) {
        triangles.push_back({polygon[0], polygon[i], polygon[i + 1]});
      }
    }
    if (triangles.empty()) {
      throw std::runtime_error("Input contains no triangulatable faces");
    }

    Mesh wrap;
    CGAL::alpha_wrap_3(points, triangles, options.alpha, options.offset, wrap);
    if (wrap.is_empty()) {
      throw std::runtime_error("Alpha wrapping produced an empty mesh");
    }

    if (!CGAL::IO::write_polygon_mesh(
            options.output, wrap, CGAL::parameters::stream_precision(17))) {
      throw std::runtime_error("Could not write output mesh: " + options.output);
    }

    std::cout << "{\"success\":true,\"vertices\":" << num_vertices(wrap)
              << ",\"faces\":" << num_faces(wrap) << "}\n";
    return EXIT_SUCCESS;
  } catch (const std::exception& error) {
    std::cerr << "Remi Alpha Wrap: " << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
