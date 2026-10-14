add_library(pg-gui STATIC
  app/session_controller.cpp app/session_controller.h
  app/language_manager.cpp app/language_manager.h
  ui/main_window.cpp ui/main_window.h
  ui/ppk_dialog.cpp ui/ppk_dialog.h
  ../resources/resources.qrc)
set_target_properties(pg-gui PROPERTIES AUTOMOC ON AUTORCC ON)
target_include_directories(pg-gui PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(pg-gui PUBLIC pg-core Qt6::Widgets)
if(MSVC)
  target_compile_options(pg-gui PRIVATE /W4 /utf-8)
else()
  target_compile_options(pg-gui PRIVATE -Wall -Wextra -Wpedantic)
endif()
add_executable(puttygen-gui WIN32 app/main.cpp)
target_link_libraries(puttygen-gui PRIVATE pg-gui)
if(MSVC)
  target_compile_options(puttygen-gui PRIVATE /utf-8)
endif()
if(BUILD_TESTING)
  add_executable(pg-gui-tests ../tests/gui/gui_test.cpp)
  set_target_properties(pg-gui-tests PROPERTIES AUTOMOC ON)
  target_link_libraries(pg-gui-tests PRIVATE pg-gui Qt6::Test)
  string(SHA256 pg_gui_test_id "${CMAKE_BINARY_DIR}")
  string(SUBSTRING "${pg_gui_test_id}" 0 12 pg_gui_test_id)
  set(pg_gui_test_root "${PUTTY_SOURCE_DIR}/.tmp/tests/gui-${pg_gui_test_id}")
  target_compile_definitions(pg-gui-tests PRIVATE PG_SOURCE_DIR="${PUTTY_SOURCE_DIR}"
    PG_GUI_TEST_ROOT="${pg_gui_test_root}")
  if(MSVC)
    target_compile_options(pg-gui-tests PRIVATE /utf-8)
  endif()
  file(MAKE_DIRECTORY "${pg_gui_test_root}/runtime" "${pg_gui_test_root}/config")
  file(CHMOD "${pg_gui_test_root}/runtime" PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE)
  add_test(NAME gui-interactions COMMAND pg-gui-tests)
  set_tests_properties(gui-interactions PROPERTIES LABELS gui
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen;TMPDIR=${pg_gui_test_root}/runtime;TEMP=${pg_gui_test_root}/runtime;TMP=${pg_gui_test_root}/runtime;XDG_CONFIG_HOME=${pg_gui_test_root}/config;XDG_RUNTIME_DIR=${pg_gui_test_root}/runtime")
endif()
