cmake_minimum_required(VERSION 3.15...3.27)
project(remi_uv_packer LANGUAGES CXX)

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

set(XATLAS_VENDOR ${CMAKE_CURRENT_SOURCE_DIR}/vendor/xatlas)
pybind11_add_module(_remi_uv_packer
  bindings.cpp
  ${XATLAS_VENDOR}/xatlas.cpp
)
target_include_directories(_remi_uv_packer PRIVATE ${XATLAS_VENDOR})
set_target_properties(_remi_uv_packer PROPERTIES CXX_VISIBILITY_PRESET default)
install(TARGETS _remi_uv_packer DESTINATION uv_mapping/_native)
