# Native part of the pypolycall wheel: build the shared libpolycall from the
# vendored core (core/, see core/VENDORED.txt) and install it INSIDE the
# Python package (pypolycall/_native/), where pypolycall loads it from.
# Driven by scikit-build-core (pyproject.toml); not a standalone project.
cmake_minimum_required(VERSION 3.20)
project(pypolycall_native LANGUAGES C)

set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(POLYCALL_BUILD_SHARED ON CACHE BOOL "" FORCE)
set(POLYCALL_BUILD_STATIC OFF CACHE BOOL "" FORCE)
set(POLYCALL_BUILD_CLI OFF CACHE BOOL "" FORCE)
set(POLYCALL_INSTALL OFF CACHE BOOL "" FORCE)
add_subdirectory(core)

# Exactly ONE file: a wheel (zip) cannot hold symlinks, so the usual
# libpolycall.so -> .so.1 -> .so.1.1.0 chain would become three full copies.
# Install the real library under its SONAME, which is what the loader opens.
if(WIN32)
  install(FILES $<TARGET_FILE:polycall_shared> DESTINATION pypolycall/_native)
elseif(APPLE)
  install(FILES $<TARGET_FILE:polycall_shared> DESTINATION pypolycall/_native
          RENAME libpolycall.1.dylib)
else()
  install(FILES $<TARGET_FILE:polycall_shared> DESTINATION pypolycall/_native
          RENAME libpolycall.so.1)
endif()
