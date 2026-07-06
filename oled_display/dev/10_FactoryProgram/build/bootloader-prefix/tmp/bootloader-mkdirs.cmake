# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file Copyright.txt or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION 3.5)

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "D:/p/smstr 8/toyota/esp/.espressif/v5.2.7/esp-idf/components/bootloader/subproject")
  file(MAKE_DIRECTORY "D:/p/smstr 8/toyota/esp/.espressif/v5.2.7/esp-idf/components/bootloader/subproject")
endif()
file(MAKE_DIRECTORY
  "D:/dev/10_FactoryProgram/build/bootloader"
  "D:/dev/10_FactoryProgram/build/bootloader-prefix"
  "D:/dev/10_FactoryProgram/build/bootloader-prefix/tmp"
  "D:/dev/10_FactoryProgram/build/bootloader-prefix/src/bootloader-stamp"
  "D:/dev/10_FactoryProgram/build/bootloader-prefix/src"
  "D:/dev/10_FactoryProgram/build/bootloader-prefix/src/bootloader-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "D:/dev/10_FactoryProgram/build/bootloader-prefix/src/bootloader-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "D:/dev/10_FactoryProgram/build/bootloader-prefix/src/bootloader-stamp${cfgdir}") # cfgdir has leading slash
endif()
