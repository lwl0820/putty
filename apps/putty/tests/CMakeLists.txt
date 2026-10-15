find_package(Python3 REQUIRED COMPONENTS Interpreter)
include("${PUTTY_SOURCE_DIR}/cmake/SuiteTestSupport.cmake")
add_executable(putty-terminal-tests terminal_test.cpp
  ../src/ui/terminal_window.cpp ../src/ui/terminal_window.h)
set_target_properties(putty-terminal-tests PROPERTIES AUTOMOC ON)
target_link_libraries(putty-terminal-tests PRIVATE putty-terminal)
suite_add_backend_list(putty-terminal-tests PuTTY SSH SERIAL OTHERBACKENDS)
target_compile_definitions(putty-terminal-tests PRIVATE
  PUTTY_TERMINAL_PROJECT_TMP="${PUTTY_SOURCE_DIR}/.tmp")

# 此目标只链接生产渲染源码和 Qt；不带入 Terminal／Conf／PRNG／存储。
add_executable(putty-terminal-cpu-tests cpu_render_test.cpp ../src/render/cpu_renderer.cpp)
target_include_directories(putty-terminal-cpu-tests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../src"
  "${PUTTY_SOURCE_DIR}/libs/putty-runtime/include")
target_link_libraries(putty-terminal-cpu-tests PRIVATE Qt6::Widgets)
target_compile_features(putty-terminal-cpu-tests PRIVATE cxx_std_17)
if(MSVC)
  target_compile_options(putty-terminal-cpu-tests PRIVATE /utf-8 /UNDEBUG)
else()
  target_compile_options(putty-terminal-cpu-tests PRIVATE -UNDEBUG)
endif()
string(SHA256 _terminal_cpu_build_id "${CMAKE_BINARY_DIR}")
string(SUBSTRING "${_terminal_cpu_build_id}" 0 16 _terminal_cpu_build_id)
set(_terminal_cpu_root "${PUTTY_SOURCE_DIR}/.tmp/terminal-cpu-${_terminal_cpu_build_id}")
file(MAKE_DIRECTORY "${_terminal_cpu_root}/runtime" "${_terminal_cpu_root}/config")
file(CHMOD "${_terminal_cpu_root}/runtime" PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE)
add_test(NAME terminal-cpu-render COMMAND putty-terminal-cpu-tests)
set_tests_properties(terminal-cpu-render PROPERTIES LABELS "terminal;render;no-storage" TIMEOUT 20
  ENVIRONMENT "QT_QPA_PLATFORM=offscreen;TMPDIR=${_terminal_cpu_root}/runtime;TEMP=${_terminal_cpu_root}/runtime;TMP=${_terminal_cpu_root}/runtime;XDG_CONFIG_HOME=${_terminal_cpu_root}/config;XDG_RUNTIME_DIR=${_terminal_cpu_root}/runtime")

if(WIN32)
  add_executable(putty-terminal-platform-tests windows_platform_test.c ../src/core/pt_platform.c)
  target_include_directories(putty-terminal-platform-tests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../src")
  target_link_libraries(putty-terminal-platform-tests PRIVATE suite-putty)
  add_test(NAME terminal-windows-platform COMMAND putty-terminal-platform-tests)
  set_tests_properties(terminal-windows-platform PROPERTIES
    LABELS "terminal;windows-terminal;no-storage" TIMEOUT 10)
  set_property(TEST terminal-cpu-render APPEND PROPERTY LABELS windows-terminal)
endif()

# 编译上游函数原文，仅以窄类型／系统调用夹具注入失败，不复制生产算法。
function(terminal_extract_function source begin end output)
  file(READ "${source}" _source)
  string(FIND "${_source}" "${begin}" _begin REVERSE)
  string(FIND "${_source}" "${end}" _end REVERSE)
  if(_begin LESS 0 OR _end LESS_EQUAL _begin)
    message(FATAL_ERROR "无法定位 MIC 故障回归对应的上游函数")
  endif()
  math(EXPR _length "${_end} - ${_begin}")
  string(SUBSTRING "${_source}" ${_begin} ${_length} _function)
  file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/${output}" "${_function}")
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${source}")
endfunction()
terminal_extract_function("${PUTTY_SOURCE_DIR}/vendor/putty/ssh/userauth2-client.c"
  "static PktOut *ssh2_userauth_gss_packet(" "\n#endif\n\nstatic bool ssh2_userauth_get_specials"
  userauth_mic_function.inc)
add_executable(putty-gss-mic-tests gss_mic_test.c)
target_include_directories(putty-gss-mic-tests PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(putty-gss-mic-tests PRIVATE putty-terminal suite-putty)
suite_add_backend_list(putty-gss-mic-tests PuTTY SSH SERIAL OTHERBACKENDS)
add_test(NAME terminal-gss-mic-failures COMMAND putty-gss-mic-tests)
set_tests_properties(terminal-gss-mic-failures PROPERTIES LABELS "terminal;gss;no-storage" TIMEOUT 10)

terminal_extract_function("${PUTTY_SOURCE_DIR}/vendor/putty/windows/gss.c"
  "static Ssh_gss_stat ssh_sspi_get_mic(" "static Ssh_gss_stat ssh_sspi_verify_mic("
  sspi_mic_function.inc)
add_executable(putty-sspi-mic-shim-tests sspi_mic_shim_test.c)
target_include_directories(putty-sspi-mic-shim-tests PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
if(MSVC)
  target_compile_options(putty-sspi-mic-shim-tests PRIVATE /utf-8 /UNDEBUG)
else()
  target_compile_options(putty-sspi-mic-shim-tests PRIVATE -UNDEBUG)
endif()
add_test(NAME terminal-sspi-mic-failures-shim COMMAND putty-sspi-mic-shim-tests)
set_tests_properties(terminal-sspi-mic-failures-shim PROPERTIES
  LABELS "terminal;gss;platform-shim;no-storage" TIMEOUT 10)
if(WIN32)
  set_property(TEST terminal-gss-mic-failures terminal-sspi-mic-failures-shim
    APPEND PROPERTY LABELS windows-terminal)
endif()
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND NOT CMAKE_CROSSCOMPILING)
  suite_prepare_test_keygen()
  add_dependencies(putty-terminal-tests suite-test-keygen)
  string(SHA256 _terminal_build_id "${CMAKE_BINARY_DIR}")
  string(SUBSTRING "${_terminal_build_id}" 0 16 _terminal_build_id)
  add_test(NAME terminal-render-bounds COMMAND "${Python3_EXECUTABLE}" -B
    "${CMAKE_CURRENT_SOURCE_DIR}/run_render_test.py"
    --binary "$<TARGET_FILE:putty-terminal-tests>"
    --parent "${PUTTY_SOURCE_DIR}/.tmp/terminal-render-${_terminal_build_id}")
  set_tests_properties(terminal-render-bounds PROPERTIES LABELS "terminal;render" TIMEOUT 30)
  add_test(NAME terminal-runner-report-paths COMMAND "${Python3_EXECUTABLE}" -B -m unittest
    discover -s "${CMAKE_CURRENT_SOURCE_DIR}" -p test_runner_paths.py -v)
  set_tests_properties(terminal-runner-report-paths PROPERTIES LABELS "terminal;isolation" TIMEOUT 10)
  if(PUTTY_SUITE_CONTAINER_TESTS)
    add_test(NAME terminal-ssh-kdf-render COMMAND "${Python3_EXECUTABLE}" -B
      "${CMAKE_CURRENT_SOURCE_DIR}/run_terminal_test.py"
      --binary "$<TARGET_FILE:putty-terminal-tests>" --puttygen "$<TARGET_FILE:suite-test-keygen>"
      --parent "${PUTTY_SOURCE_DIR}/.tmp/terminal-tests-${_terminal_build_id}"
      --report "${PUTTY_SOURCE_DIR}/.tmp/terminal-reports/${_terminal_build_id}.json")
    set_tests_properties(terminal-ssh-kdf-render PROPERTIES
      LABELS "terminal;ssh;container" TIMEOUT 90 SKIP_RETURN_CODE 77)
    add_test(NAME terminal-gss-render-close COMMAND "${Python3_EXECUTABLE}" -B
      "${CMAKE_CURRENT_SOURCE_DIR}/run_terminal_gss_test.py"
      --binary "$<TARGET_FILE:putty-terminal-tests>"
      --parent "${PUTTY_SOURCE_DIR}/.tmp/terminal-gss-tests-${_terminal_build_id}"
      --report "${PUTTY_SOURCE_DIR}/.tmp/terminal-reports/${_terminal_build_id}-gss.json")
    set_tests_properties(terminal-gss-render-close PROPERTIES
      LABELS "terminal;ssh;gss;container" TIMEOUT 100 SKIP_RETURN_CODE 77)
  endif()
endif()
