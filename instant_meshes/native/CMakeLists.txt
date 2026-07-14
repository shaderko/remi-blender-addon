cmake_minimum_required(VERSION 3.15...3.27)
project(remi_instant_meshes LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(APPLE AND NOT CMAKE_OSX_DEPLOYMENT_TARGET)
  set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "Minimum macOS version" FORCE)
endif()

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

set(IM_VENDOR ${CMAKE_CURRENT_SOURCE_DIR}/vendor/instant_meshes)
set(IM_TBB ${IM_VENDOR}/ext/tbb)

# Instant Meshes uses the classic TBB task scheduler. Build only that library's
# sources directly instead of retaining the legacy standalone TBB build,
# allocator, proxy, tests, and platform packaging machinery.
file(GLOB TBB_CORE_SOURCES CONFIGURE_DEPENDS
  ${IM_TBB}/src/tbb/*.cpp
  ${IM_TBB}/src/old/*.cpp
)
list(FILTER TBB_CORE_SOURCES EXCLUDE REGEX "/test[^/]*\\.cpp$")
list(APPEND TBB_CORE_SOURCES ${IM_TBB}/src/rml/client/rml_tbb.cpp)

# TBB's version source expects this generated include. Keep it deterministic
# instead of embedding the developer machine, compiler command, and timestamp.
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/version_string.ver [=[
#define __TBB_VERSION_STRINGS(N) #N ": BUILD_HOST\tRemi" ENDL
#define __TBB_DATETIME "Reproducible build"
]=])

add_library(remi_tbb STATIC ${TBB_CORE_SOURCES})
target_include_directories(remi_tbb PRIVATE
  ${CMAKE_CURRENT_BINARY_DIR}
  ${IM_TBB}/include
  ${IM_TBB}/src
  ${IM_TBB}/src/rml/include
)
target_compile_definitions(remi_tbb PRIVATE USE_PTHREAD __TBB_BUILD=1)
target_compile_options(remi_tbb PRIVATE -frtti -fexceptions)
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  set_source_files_properties(${IM_TBB}/src/tbb/itt_notify.cpp
    PROPERTIES COMPILE_OPTIONS -Wno-varargs)
  if(APPLE)
    target_compile_options(remi_tbb PRIVATE -Wno-deprecated-declarations)
  endif()
endif()

set(IM_CORE_SOURCES
  ${IM_VENDOR}/src/normal.cpp
  ${IM_VENDOR}/src/adjacency.cpp
  ${IM_VENDOR}/src/meshstats.cpp
  ${IM_VENDOR}/src/hierarchy.cpp
  ${IM_VENDOR}/src/extract.cpp
  ${IM_VENDOR}/src/field.cpp
  ${IM_VENDOR}/src/bvh.cpp
  ${IM_VENDOR}/src/subdivide.cpp
  ${IM_VENDOR}/src/reorder.cpp
  ${IM_VENDOR}/src/serializer.cpp
  ${IM_VENDOR}/src/smoothcurve.cpp
  ${IM_VENDOR}/src/cleanup.cpp
  ${IM_VENDOR}/src/dedge.cpp
  ${IM_VENDOR}/ext/rply/rply.c
)

pybind11_add_module(_remi_instant_meshes
  bindings.cpp
  session.cpp
  ${IM_CORE_SOURCES}
)

target_include_directories(_remi_instant_meshes PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${IM_VENDOR}/src
  ${IM_VENDOR}/ext/eigen
  ${IM_VENDOR}/ext/tbb/include
  ${IM_VENDOR}/ext/dset
  ${IM_VENDOR}/ext/pss
  ${IM_VENDOR}/ext/pcg32
  ${IM_VENDOR}/ext/rply
)

target_compile_definitions(_remi_instant_meshes PRIVATE EIGEN_MPL2_ONLY)
target_link_libraries(_remi_instant_meshes PRIVATE remi_tbb)
if(APPLE AND CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  target_compile_options(_remi_instant_meshes PRIVATE
    -Wno-deprecated-declarations)
endif()
set_target_properties(_remi_instant_meshes PROPERTIES CXX_VISIBILITY_PRESET default)
install(TARGETS _remi_instant_meshes DESTINATION instant_meshes/_native)
