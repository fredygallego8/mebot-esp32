# The following lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.16)

# Add this line to disable the specific warning
add_compile_options(-Wno-missing-field-initializers)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# "Trim" the build. Include the minimal set of components, main, and anything it depends on.
idf_build_set_property(MINIMAL_BUILD ON)

# CMAKE_CURRENT_LIST_DIR = directorio de este CMakeLists.txt (siempre correcto en ESP-IDF)
set(MEBOT_DIR "${CMAKE_CURRENT_LIST_DIR}")

# Incrementar build number y generar main/mebot_build_info.h
execute_process(
    COMMAND ${CMAKE_COMMAND} -P ${MEBOT_DIR}/cmake/mebot_build_number.cmake
)

# Leer el build number generado para incluirlo en la versión
file(READ "${MEBOT_DIR}/build_number.txt" MEBOT_BUILD_NUM)
string(STRIP "${MEBOT_BUILD_NUM}" MEBOT_BUILD_NUM)

set(PROJECT_VER "2.7.7+b${MEBOT_BUILD_NUM}")
project(mebot)
