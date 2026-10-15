find_package(Qt6 6.8 REQUIRED COMPONENTS Network)
add_executable(putty-runtime-tests runtime_test.cpp test_module.c)
target_link_libraries(putty-runtime-tests PRIVATE putty-runtime suite-putty Qt6::Network)
target_compile_features(putty-runtime-tests PRIVATE cxx_std_17)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
string(SHA256 _runtime_build_id "${CMAKE_BINARY_DIR}")
string(SUBSTRING "${_runtime_build_id}" 0 16 _runtime_build_id)
target_compile_definitions(putty-runtime-tests PRIVATE PUTTY_RUNTIME_PROJECT_TMP="${PROJECT_SOURCE_DIR}/.tmp")
add_test(NAME runtime-lifecycle COMMAND "${Python3_EXECUTABLE}" -B
    "${CMAKE_CURRENT_SOURCE_DIR}/run_runtime_test.py"
    --binary "$<TARGET_FILE:putty-runtime-tests>"
    --parent "${PROJECT_SOURCE_DIR}/.tmp/runtime-tests-${_runtime_build_id}")
set_tests_properties(runtime-lifecycle PROPERTIES LABELS runtime TIMEOUT 30 SKIP_RETURN_CODE 77)

if(WIN32)
  target_sources(putty-runtime-tests PRIVATE "${PROJECT_SOURCE_DIR}/vendor/putty/windows/no-jump-list.c")
  # 原生事件宿主禁止初始化存储和 PRNG；hive 验证完成前不启用持久化测试。
  add_executable(suite-windows-handle-tests windows/native_handle_wait.c)
  target_link_libraries(suite-windows-handle-tests PRIVATE suite-putty)
  add_executable(suite-windows-runtime-tests windows/native_runtime_test.cpp
    windows/event_module.c windows/no_storage.c)
  target_link_libraries(suite-windows-runtime-tests PRIVATE
    putty-runtime suite-putty Qt6::Network)
  target_compile_features(suite-windows-runtime-tests PRIVATE cxx_std_17)
  if(MSVC)
    target_link_options(suite-windows-runtime-tests PRIVATE
      "/MAP:$<TARGET_FILE:suite-windows-runtime-tests>.map"
      /INCLUDE:read_random_seed /INCLUDE:write_random_seed
      /INCLUDE:open_settings_r /INCLUDE:open_settings_w)
  endif()
  foreach(kind handles events)
    if(kind STREQUAL "handles")
      set(native_target suite-windows-handle-tests)
    else()
      set(native_target suite-windows-runtime-tests)
    endif()
    add_test(NAME "runtime-windows-${kind}" COMMAND "${Python3_EXECUTABLE}" -B
      "${PROJECT_SOURCE_DIR}/tools/suite/windows/run_native_test.py"
      --binary "$<TARGET_FILE:${native_target}>" --kind "${kind}"
      --parent "${PROJECT_SOURCE_DIR}/.tmp/windows-native-${_runtime_build_id}")
    set_tests_properties("runtime-windows-${kind}" PROPERTIES
      LABELS "runtime;windows-native" TIMEOUT 60)
  endforeach()

  # 私有应用 hive 自测独立于无存储宿主，不自动开放原版存储入口。
  set(_suite_hive_dir "${PROJECT_SOURCE_DIR}/tools/suite/windows/hive")
  add_library(suite-windows-hive STATIC "${_suite_hive_dir}/private_hive.c")
  target_include_directories(suite-windows-hive PUBLIC "${_suite_hive_dir}")
  target_compile_definitions(suite-windows-hive PUBLIC _WIN32_WINNT=0x0602)
  target_link_libraries(suite-windows-hive PUBLIC advapi32 bcrypt)
  if(MSVC)
    target_compile_options(suite-windows-hive PUBLIC /utf-8 /UNDEBUG)
  else()
    target_compile_options(suite-windows-hive PUBLIC -UNDEBUG)
  endif()
  add_executable(suite-windows-hive-tests "${_suite_hive_dir}/hive_test.c")
  target_link_libraries(suite-windows-hive-tests PRIVATE suite-windows-hive)
  target_compile_definitions(suite-windows-hive-tests PRIVATE
    SUITE_HIVE_PROJECT_TMP="${PROJECT_SOURCE_DIR}/.tmp")
  add_test(NAME runtime-windows-hive COMMAND "${Python3_EXECUTABLE}" -B
    "${_suite_hive_dir}/run_hive_test.py"
    --binary "$<TARGET_FILE:suite-windows-hive-tests>"
    --parent "${PROJECT_SOURCE_DIR}/.tmp/windows-hive-${_runtime_build_id}")
  set_tests_properties(runtime-windows-hive PROPERTIES
    LABELS "runtime;windows-hive" TIMEOUT 90)

  # 上游存储使用独立测试变体，随机种子访问失败时必须禁止个人路径回退。
  set(_suite_storage_dir "${PROJECT_SOURCE_DIR}/tools/suite/windows/storage")
  add_executable(suite-windows-storage-tests
    "${_suite_storage_dir}/storage_variant.c" "${_suite_storage_dir}/seed_guard.c"
    "${_suite_storage_dir}/storage_test.c" "${_suite_storage_dir}/frontend.c"
    "${PROJECT_SOURCE_DIR}/vendor/putty/windows/no-jump-list.c")
  target_link_libraries(suite-windows-storage-tests PRIVATE suite-windows-hive suite-putty)
  target_compile_definitions(suite-windows-storage-tests PRIVATE
    SUITE_STORAGE_PROJECT_TMP="${PROJECT_SOURCE_DIR}/.tmp")
  if(MSVC)
    target_link_options(suite-windows-storage-tests PRIVATE
      "/MAP:$<TARGET_FILE:suite-windows-storage-tests>.map")
  endif()
  add_test(NAME runtime-windows-storage COMMAND "${Python3_EXECUTABLE}" -B
    "${_suite_storage_dir}/run_storage_test.py"
    --binary "$<TARGET_FILE:suite-windows-storage-tests>"
    --parent "${PROJECT_SOURCE_DIR}/.tmp/windows-storage-${_runtime_build_id}")
  set_tests_properties(runtime-windows-storage PROPERTIES
    LABELS "runtime;windows-storage" TIMEOUT 120)
endif()

# 此 Unix 宿主不依赖 Qt；Windows 原生存储宿主待独立隔离夹具接入。
if(UNIX)
  # 在 Linux 编译真实 Windows 等待算法，覆盖迟到快照；不代替原生事件测试。
  add_executable(suite-handle-wait-tests handle_wait_test.c
    "${PROJECT_SOURCE_DIR}/vendor/putty/windows/handle-wait.c")
  target_include_directories(suite-handle-wait-tests BEFORE PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/windows-handle-wait"
    "${PROJECT_SOURCE_DIR}/vendor/putty/windows")
  target_link_libraries(suite-handle-wait-tests PRIVATE suite-putty)
  add_test(NAME runtime-handle-registration COMMAND suite-handle-wait-tests)
  set_tests_properties(runtime-handle-registration PROPERTIES
    LABELS "runtime;windows-algorithm" TIMEOUT 15)

  add_executable(suite-core-link-tests core_link_smoke.c
    "${PROJECT_SOURCE_DIR}/vendor/putty/stubs/no-console.c")
  target_link_libraries(suite-core-link-tests PRIVATE suite-putty)
  target_compile_definitions(suite-core-link-tests PRIVATE
    PUTTY_RUNTIME_PROJECT_TMP="${PROJECT_SOURCE_DIR}/.tmp")
  suite_add_backend_list(suite-core-link-tests SuiteReview SSH SERIAL OTHERBACKENDS)
  add_test(NAME runtime-core-link COMMAND "${Python3_EXECUTABLE}" -B
    "${CMAKE_CURRENT_SOURCE_DIR}/run_runtime_test.py"
    --binary "$<TARGET_FILE:suite-core-link-tests>"
    --parent "${PROJECT_SOURCE_DIR}/.tmp/runtime-core-link-${_runtime_build_id}"
    --kind core-link)
  set_tests_properties(runtime-core-link PROPERTIES LABELS "runtime;storage" TIMEOUT 30 SKIP_RETURN_CODE 77)

  add_executable(suite-agent-core-link-tests agent_core_link_smoke.c
    "${PROJECT_SOURCE_DIR}/vendor/putty/stubs/no-console.c")
  target_link_libraries(suite-agent-core-link-tests PRIVATE putty-runtime suite-pageant-core)
  suite_add_backend_list(suite-agent-core-link-tests Pageant)
  add_test(NAME runtime-agent-core-link COMMAND "${Python3_EXECUTABLE}" -B
    "${CMAKE_CURRENT_SOURCE_DIR}/run_runtime_test.py"
    --binary "$<TARGET_FILE:suite-agent-core-link-tests>"
    --parent "${PROJECT_SOURCE_DIR}/.tmp/runtime-agent-core-${_runtime_build_id}"
    --kind agent-core)
  set_tests_properties(runtime-agent-core-link PROPERTIES LABELS "runtime;agent" TIMEOUT 30 SKIP_RETURN_CODE 77)
endif()
