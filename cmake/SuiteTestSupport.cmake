# 三个 CLI 共用的测试工具不进入安装或发布清单。
include_guard(GLOBAL)
include(ExternalProject)
option(PUTTY_SUITE_CONTAINER_TESTS "启用需要已有 Docker 镜像的专用 SSH 账户测试" OFF)

function(suite_prepare_test_keygen)
  if(TARGET suite-test-keygen)
    return()
  endif()
  set(reference_dir "${CMAKE_BINARY_DIR}/suite-test-keygen")
  set(reference_output "${reference_dir}")
  set(reference_args
    "-DCMAKE_C_COMPILER:FILEPATH=${CMAKE_C_COMPILER}"
    "-DCMAKE_BUILD_TYPE:STRING=${CMAKE_BUILD_TYPE}"
    -DPUTTY_GTK_VERSION:STRING=NONE -DPUTTY_GSSAPI:STRING=DYNAMIC)
  foreach(variable CMAKE_TOOLCHAIN_FILE CMAKE_SYSROOT CMAKE_C_COMPILER_TARGET
      CMAKE_C_COMPILER_EXTERNAL_TOOLCHAIN CMAKE_C_COMPILER_ARG1 CMAKE_MAKE_PROGRAM
      CMAKE_C_FLAGS CMAKE_C_FLAGS_DEBUG CMAKE_C_FLAGS_RELEASE
      CMAKE_C_FLAGS_RELWITHDEBINFO CMAKE_C_FLAGS_MINSIZEREL
      CMAKE_EXE_LINKER_FLAGS CMAKE_EXE_LINKER_FLAGS_DEBUG CMAKE_EXE_LINKER_FLAGS_RELEASE
      CMAKE_EXE_LINKER_FLAGS_RELWITHDEBINFO CMAKE_EXE_LINKER_FLAGS_MINSIZEREL)
    if(DEFINED ${variable})
      set(argument "${${variable}}")
      if(variable MATCHES "^CMAKE_C_FLAGS")
        string(REGEX REPLACE "(^| )[-/]DNDEBUG(=[^ ]*)?( |$)" " " argument "${argument}")
      endif()
      string(REPLACE ";" "|" argument "${argument}")
      list(APPEND reference_args "-D${variable}:STRING=${argument}")
    endif()
  endforeach()
  if(CMAKE_CONFIGURATION_TYPES)
    string(REPLACE ";" "|" configurations "${CMAKE_CONFIGURATION_TYPES}")
    list(APPEND reference_args "-DCMAKE_CONFIGURATION_TYPES:STRING=${configurations}")
    set(configurations ${CMAKE_CONFIGURATION_TYPES})
    string(APPEND reference_output "/$<CONFIG>")
  else()
    set(configurations Debug Release RelWithDebInfo MinSizeRel)
  endif()
  foreach(configuration IN LISTS configurations)
    string(TOUPPER "${configuration}" key)
    set(output "${reference_dir}")
    if(CMAKE_CONFIGURATION_TYPES)
      string(APPEND output "/${configuration}")
    endif()
    list(APPEND reference_args "-DCMAKE_RUNTIME_OUTPUT_DIRECTORY_${key}:PATH=${output}")
  endforeach()
  ExternalProject_Add(suite-test-keygen-build
    SOURCE_DIR "${PUTTY_SOURCE_DIR}/vendor/putty"
    BINARY_DIR "${reference_dir}"
    PREFIX "${CMAKE_BINARY_DIR}/suite-test-keygen-project"
    DOWNLOAD_COMMAND "" UPDATE_COMMAND ""
    LIST_SEPARATOR "|" CMAKE_ARGS ${reference_args}
    BUILD_COMMAND "${CMAKE_COMMAND}" --build <BINARY_DIR> --config $<CONFIG> --target puttygen
    BUILD_BYPRODUCTS "${reference_output}/puttygen${CMAKE_EXECUTABLE_SUFFIX}"
    BUILD_ALWAYS TRUE INSTALL_COMMAND "")
  add_executable(suite-test-keygen IMPORTED GLOBAL)
  if(CMAKE_CONFIGURATION_TYPES)
    set_target_properties(suite-test-keygen PROPERTIES
      IMPORTED_CONFIGURATIONS "${CMAKE_CONFIGURATION_TYPES}")
    foreach(configuration IN LISTS CMAKE_CONFIGURATION_TYPES)
      string(TOUPPER "${configuration}" key)
      set_property(TARGET suite-test-keygen PROPERTY "IMPORTED_LOCATION_${key}"
        "${reference_dir}/${configuration}/puttygen${CMAKE_EXECUTABLE_SUFFIX}")
    endforeach()
  else()
    set_target_properties(suite-test-keygen PROPERTIES
      IMPORTED_LOCATION "${reference_dir}/puttygen${CMAKE_EXECUTABLE_SUFFIX}")
  endif()
  add_dependencies(suite-test-keygen suite-test-keygen-build)
endfunction()

function(suite_add_cli_checks component)
  if(NOT BUILD_TESTING)
    return()
  endif()
  # 环境变量不能隔离 Windows HKCU；跨编译也不能冒充原生执行。
  if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux" OR CMAKE_CROSSCOMPILING)
    message(STATUS "${component} 自动运行测试需要已验证的 Linux 隔离；当前平台未注册")
    return()
  endif()
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  suite_prepare_test_keygen()
  set(scenarios local sftp)
  if(PUTTY_SUITE_CONTAINER_TESTS AND NOT component STREQUAL "psftp")
    list(APPEND scenarios container)
  endif()
  foreach(scenario IN LISTS scenarios)
    set(test_name "cli-${component}-${scenario}")
    add_test(NAME "${test_name}" COMMAND "${Python3_EXECUTABLE}" -B
      "${PUTTY_SOURCE_DIR}/tools/suite/cli_checks.py"
      --component "${component}" --executable "$<TARGET_FILE:suite-${component}>"
      --puttygen "$<TARGET_FILE:suite-test-keygen>" --scenario "${scenario}")
    set_tests_properties("${test_name}" PROPERTIES LABELS "cli;${component};${scenario}"
      WORKING_DIRECTORY "${PUTTY_SOURCE_DIR}" TIMEOUT 120 SKIP_RETURN_CODE 77)
  endforeach()
endfunction()
