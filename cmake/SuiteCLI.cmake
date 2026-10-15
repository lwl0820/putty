# CLI 在上游独立源码根配置；不向套件注入上游全局目标、缓存或安装规则。
include_guard(GLOBAL)
include(ExternalProject)
include(GNUInstallDirs)

function(suite_prepare_cli)
  if(TARGET suite-cli-build)
    return()
  endif()
  set(suite_cli_dir "${CMAKE_BINARY_DIR}/suite-cli")
  if(CMAKE_CONFIGURATION_TYPES)
    set(suite_cli_output "${suite_cli_dir}/$<CONFIG>")
  else()
    set(suite_cli_output "${suite_cli_dir}")
  endif()
  set(suite_cli_targets)
  set(suite_cli_byproducts)
  foreach(component PLINK PSCP PSFTP)
    if(PUTTY_BUILD_${component})
      string(TOLOWER "${component}" executable)
      list(APPEND suite_cli_targets "${executable}")
      list(APPEND suite_cli_byproducts "${suite_cli_output}/${executable}${CMAKE_EXECUTABLE_SUFFIX}")
    endif()
  endforeach()

  set(suite_cli_args
    "-DCMAKE_C_COMPILER:FILEPATH=${CMAKE_C_COMPILER}"
    "-DCMAKE_BUILD_TYPE:STRING=${CMAKE_BUILD_TYPE}"
    -DPUTTY_GTK_VERSION:STRING=NONE -DPUTTY_IPV6:BOOL=ON)
  # 继承调用方编译/链接标志，同时在全部发布配置中保留上游安全断言。
  foreach(variable CMAKE_C_FLAGS CMAKE_C_FLAGS_DEBUG CMAKE_C_FLAGS_RELEASE
      CMAKE_C_FLAGS_RELWITHDEBINFO CMAKE_C_FLAGS_MINSIZEREL)
    string(REGEX REPLACE "(^| )[-/]DNDEBUG(=[^ ]*)?( |$)" " " flags "${${variable}}")
    string(REPLACE ";" "|" flags "${flags}")
    list(APPEND suite_cli_args "-D${variable}:STRING=${flags}")
  endforeach()
  if(WIN32)
    list(APPEND suite_cli_args -DPUTTY_GSSAPI:BOOL=ON)
  else()
    list(APPEND suite_cli_args -DPUTTY_GSSAPI:STRING=DYNAMIC)
  endif()
  # 工具链、sysroot、架构及运行库必须与调用方一致。
  foreach(variable CMAKE_TOOLCHAIN_FILE CMAKE_SYSROOT CMAKE_C_COMPILER_TARGET
      CMAKE_C_COMPILER_EXTERNAL_TOOLCHAIN CMAKE_MSVC_RUNTIME_LIBRARY
      CMAKE_C_COMPILER_ARG1 CMAKE_EXE_LINKER_FLAGS
      CMAKE_EXE_LINKER_FLAGS_DEBUG CMAKE_EXE_LINKER_FLAGS_RELEASE
      CMAKE_EXE_LINKER_FLAGS_RELWITHDEBINFO CMAKE_EXE_LINKER_FLAGS_MINSIZEREL
      CMAKE_OSX_ARCHITECTURES CMAKE_OSX_DEPLOYMENT_TARGET CMAKE_OSX_SYSROOT
      CMAKE_MAKE_PROGRAM)
    if(DEFINED ${variable} AND NOT "${${variable}}" STREQUAL "")
      string(REPLACE ";" "|" argument "${${variable}}")
      list(APPEND suite_cli_args "-D${variable}:STRING=${argument}")
    endif()
  endforeach()
  # 多配置必须保留配置目录，禁止 Release 安装误收先前的 Debug 程序。
  if(CMAKE_CONFIGURATION_TYPES)
    string(REPLACE ";" "|" configurations "${CMAKE_CONFIGURATION_TYPES}")
    list(APPEND suite_cli_args "-DCMAKE_CONFIGURATION_TYPES:STRING=${configurations}")
    set(suite_configurations ${CMAKE_CONFIGURATION_TYPES})
  else()
    set(suite_configurations Debug Release RelWithDebInfo MinSizeRel)
  endif()
  foreach(configuration IN LISTS suite_configurations)
    string(TOUPPER "${configuration}" configuration_key)
    set(output "${suite_cli_dir}")
    if(CMAKE_CONFIGURATION_TYPES)
      string(APPEND output "/${configuration}")
    endif()
    list(APPEND suite_cli_args
      "-DCMAKE_RUNTIME_OUTPUT_DIRECTORY_${configuration_key}:PATH=${output}")
  endforeach()
  ExternalProject_Add(suite-cli-build
    SOURCE_DIR "${PUTTY_SOURCE_DIR}/vendor/putty"
    BINARY_DIR "${suite_cli_dir}"
    PREFIX "${CMAKE_BINARY_DIR}/suite-cli-project"
    DOWNLOAD_COMMAND "" UPDATE_COMMAND ""
    LIST_SEPARATOR "|"
    CMAKE_ARGS ${suite_cli_args}
    BUILD_COMMAND "${CMAKE_COMMAND}" --build <BINARY_DIR> --config $<CONFIG>
      --target ${suite_cli_targets}
    BUILD_BYPRODUCTS ${suite_cli_byproducts}
    BUILD_ALWAYS TRUE
    INSTALL_COMMAND "")
endfunction()

function(suite_add_cli executable)
  suite_prepare_cli()
  set(suite_cli_file "${CMAKE_BINARY_DIR}/suite-cli/${executable}${CMAKE_EXECUTABLE_SUFFIX}")
  add_executable(suite-${executable} IMPORTED GLOBAL)
  if(CMAKE_CONFIGURATION_TYPES)
    set_target_properties(suite-${executable} PROPERTIES
      IMPORTED_CONFIGURATIONS "${CMAKE_CONFIGURATION_TYPES}")
    foreach(configuration IN LISTS CMAKE_CONFIGURATION_TYPES)
      string(TOUPPER "${configuration}" configuration_key)
      set_property(TARGET suite-${executable} PROPERTY "IMPORTED_LOCATION_${configuration_key}"
        "${CMAKE_BINARY_DIR}/suite-cli/${configuration}/${executable}${CMAKE_EXECUTABLE_SUFFIX}")
    endforeach()
    set(suite_cli_file "${CMAKE_BINARY_DIR}/suite-cli/$<CONFIG>/${executable}${CMAKE_EXECUTABLE_SUFFIX}")
  else()
    set_target_properties(suite-${executable} PROPERTIES IMPORTED_LOCATION "${suite_cli_file}")
  endif()
  add_dependencies(suite-${executable} suite-cli-build)
  # 便于按产品名构建；仅安装明确选择的三个产品，不运行上游全量 install。
  add_custom_target(${executable} DEPENDS suite-cli-build)
  install(PROGRAMS "${suite_cli_file}" DESTINATION "${CMAKE_INSTALL_BINDIR}"
    COMPONENT ${executable})
endfunction()
