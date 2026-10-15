if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND NOT CMAKE_CROSSCOMPILING)
  add_executable(pageant-core-host core_host.cpp
    "${PROJECT_SOURCE_DIR}/vendor/putty/stubs/no-console.c")
  target_link_libraries(pageant-core-host PRIVATE pageant-agent suite-pageant-core)
  suite_add_backend_list(pageant-core-host Pageant)
  add_executable(pageant-prompt-failure prompt_failure.c
    "${PROJECT_SOURCE_DIR}/vendor/putty/stubs/no-console.c" $<TARGET_OBJECTS:pageant-host>)
  target_link_libraries(pageant-prompt-failure PRIVATE putty-runtime suite-pageant-core)
  suite_add_backend_list(pageant-prompt-failure Pageant)
  include("${PROJECT_SOURCE_DIR}/cmake/SuiteTestSupport.cmake")
  suite_prepare_test_keygen()
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  add_test(NAME pageant-core-interop COMMAND "${Python3_EXECUTABLE}" -B
    "${CMAKE_CURRENT_SOURCE_DIR}/agent_checks.py"
    --host "$<TARGET_FILE:pageant-core-host>" --puttygen "$<TARGET_FILE:suite-test-keygen>"
    --branches "$<TARGET_FILE:pageant-prompt-failure>")
  set_tests_properties(pageant-core-interop PROPERTIES LABELS "pageant;agent;interop"
    TIMEOUT 120 WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}")
  # 同一入口的测试变体仅改变原 CLI 的编译目录；生产目标保留原前缀。
  string(SHA256 pageant_test_hash "${CMAKE_CURRENT_BINARY_DIR}")
  string(SUBSTRING "${pageant_test_hash}" 0 8 pageant_test_hash)
  set(pageant_test_parent "${PROJECT_SOURCE_DIR}/.tmp/pa-${pageant_test_hash}")
  pageant_cli_objects(pageant-test-cli)
  target_compile_definitions(pageant-test-cli PRIVATE PAGEANT_DIR_PREFIX="${pageant_test_parent}/agent")
  add_executable(pageant-entry-tests ../src/app/main.cpp $<TARGET_OBJECTS:pageant-test-cli>)
  target_link_libraries(pageant-entry-tests PRIVATE pageant-ui suite-pageant-core)
  target_link_options(pageant-entry-tests PRIVATE -Wl,--wrap=uxsel_input_add -Wl,--wrap=uxsel_input_remove)
  suite_add_backend_list(pageant-entry-tests Pageant)
  add_test(NAME pageant-original-cli-entry COMMAND "${Python3_EXECUTABLE}" -B
    "${CMAKE_CURRENT_SOURCE_DIR}/entry_checks.py" --binary "$<TARGET_FILE:pageant-entry-tests>"
    --puttygen "$<TARGET_FILE:suite-test-keygen>" --prefix-parent "${pageant_test_parent}")
  set_tests_properties(pageant-original-cli-entry PROPERTIES LABELS "pageant;cli;interop" TIMEOUT 45)
  find_package(Qt6 6.8 REQUIRED COMPONENTS Test)
  add_executable(pageant-gui-tests gui_test.cpp
    "${PROJECT_SOURCE_DIR}/vendor/putty/stubs/no-console.c")
  set_target_properties(pageant-gui-tests PROPERTIES AUTOMOC ON)
  target_link_libraries(pageant-gui-tests PRIVATE pageant-ui Qt6::Test suite-pageant-core)
  target_compile_definitions(pageant-gui-tests PRIVATE PAGEANT_PROJECT_TMP="${PROJECT_SOURCE_DIR}/.tmp")
  suite_add_backend_list(pageant-gui-tests Pageant)
  add_dependencies(pageant-gui-tests pageant)
  foreach(scenario defaultLanguageAndSelection realAddDeleteAndClearActions
      serialPassphrasesAndReencrypt noTrayVisibleControlAndRecovery exitCancelsPromptAndSocket
      clearDuringPromptDismissesDialog oversizedPassphraseKeepsPrompt originalCliControlsGuiAgent)
    add_test(NAME pageant-gui-${scenario} COMMAND "${Python3_EXECUTABLE}" -B
      "${CMAKE_CURRENT_SOURCE_DIR}/run_gui_test.py"
      --binary "$<TARGET_FILE:pageant-gui-tests>" --puttygen "$<TARGET_FILE:suite-test-keygen>"
      --cli "$<TARGET_FILE:pageant>"
      --case "${scenario}")
    set_tests_properties(pageant-gui-${scenario} PROPERTIES LABELS "pageant;gui" TIMEOUT 40)
  endforeach()
endif()
find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_test(NAME pageant-windows-runner-contract COMMAND "${Python3_EXECUTABLE}" -B
  "${CMAKE_CURRENT_SOURCE_DIR}/windows/test_runner.py")
set_tests_properties(pageant-windows-runner-contract PROPERTIES LABELS "pageant;test-support" TIMEOUT 15)
if(WIN32)
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  set_property(TEST pageant-windows-runner-contract APPEND PROPERTY LABELS windows-pageant)
  # 只测试随机编译名，产品仍使用原版名称，不接受环境变量改名。
  string(RANDOM LENGTH 32 ALPHABET 0123456789abcdef pageant_native_namespace)
  pageant_agent_library(pageant-test-windows-agent)
  target_compile_definitions(pageant-test-windows-agent PUBLIC
    PA_WINDOWS_TEST_NAMESPACE="SuitePageantTest-${pageant_native_namespace}")
  add_executable(pageant-windows-ipc-tests windows/native_ipc.cpp windows/verify.c
    windows/no_storage.c "${PROJECT_SOURCE_DIR}/vendor/putty/stubs/no-console.c")
  target_link_libraries(pageant-windows-ipc-tests PRIVATE pageant-test-windows-agent suite-pageant-core)
  suite_add_backend_list(pageant-windows-ipc-tests Pageant)
  if(MSVC)
    target_link_options(pageant-windows-ipc-tests PRIVATE
      "/MAP:$<TARGET_FILE:pageant-windows-ipc-tests>.map"
      /INCLUDE:read_random_seed /INCLUDE:write_random_seed
      /INCLUDE:open_settings_r /INCLUDE:open_settings_w)
  endif()
  add_test(NAME pageant-windows-ipc COMMAND "${Python3_EXECUTABLE}" -B
    "${CMAKE_CURRENT_SOURCE_DIR}/windows/run_ipc_test.py"
    --binary "$<TARGET_FILE:pageant-windows-ipc-tests>"
    --parent "${PROJECT_SOURCE_DIR}/.tmp/pageant-windows-ipc")
  set_tests_properties(pageant-windows-ipc PROPERTIES LABELS "pageant;windows-pageant;agent"
    RUN_SERIAL TRUE TIMEOUT 90)
endif()
