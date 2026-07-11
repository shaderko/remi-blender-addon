cmake_minimum_required(VERSION 3.16)
project(remi_alpha_wrap LANGUAGES CXX)

find_package(CGAL REQUIRED)

add_executable(remi_alpha_wrap main.cpp)
target_compile_features(remi_alpha_wrap PRIVATE cxx_std_17)
target_link_libraries(remi_alpha_wrap PRIVATE CGAL::CGAL)

if(MSVC)
  target_compile_options(remi_alpha_wrap PRIVATE /O2)
else()
  target_compile_options(remi_alpha_wrap PRIVATE -O3)
endif()
