cmake_minimum_required(VERSION 3.20)
project(AetherDriveMega LANGUAGES CXX)

if(NOT MEGA_SDK_SOURCE_DIR)
    message(FATAL_ERROR "MEGA_SDK_SOURCE_DIR must point to the official MEGA SDK source.")
endif()

add_subdirectory("${MEGA_SDK_SOURCE_DIR}" mega-sdk)

add_executable(aetherdrive-mega mega_bridge.cpp)
target_link_libraries(aetherdrive-mega PRIVATE MEGA::SDKlib)
target_compile_features(aetherdrive-mega PRIVATE cxx_std_17)
