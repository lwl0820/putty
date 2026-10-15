find_package(Qt6 6.8 REQUIRED COMPONENTS Core Widgets)
add_library(pageant-host OBJECT src/core/pa_host.c)
target_link_libraries(pageant-host PRIVATE suite-config)
add_library(pageant-agent STATIC src/core/pa_agent.c
  src/app/agent_codec.cpp)
target_sources(pageant-agent INTERFACE $<TARGET_OBJECTS:pageant-host>)
target_include_directories(pageant-agent PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(pageant-agent PUBLIC putty-runtime PRIVATE suite-pageant-core)
target_compile_features(pageant-agent PUBLIC cxx_std_17)

# 只重命名前端入口及重复宿主符号，参数解析、fork 和原 CLI 业务保持原源。
function(pageant_cli_objects target)
  add_library(${target} OBJECT
    "${PROJECT_SOURCE_DIR}/vendor/putty/unix/pageant.c"
    "${PROJECT_SOURCE_DIR}/vendor/putty/unix/cliloop.c"
    "${PROJECT_SOURCE_DIR}/vendor/putty/unix/console.c"
    "${PROJECT_SOURCE_DIR}/vendor/putty/console.c"
    "${PROJECT_SOURCE_DIR}/vendor/putty/stubs/no-gss.c"
    "${PROJECT_SOURCE_DIR}/vendor/putty/proxy/nosshproxy.c"
    "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/src/core/pa_cli_prompt.c")
  target_link_libraries(${target} PRIVATE suite-config)
  target_compile_definitions(${target} PRIVATE main=pageant_cli_main
    keylist_update=pa_cli_keylist_update x_get_default=pa_cli_x_get_default
    random_save_seed=pa_cli_random_save_seed random_destroy_seed=pa_cli_random_destroy_seed
    platform_default_s=pa_cli_platform_default_s platform_default_b=pa_cli_platform_default_b
    platform_default_i=pa_cli_platform_default_i
    platform_default_fontspec=pa_cli_platform_default_fontspec
    platform_default_filename=pa_cli_platform_default_filename
    platform_get_x_display=pa_cli_platform_get_x_display
    uxsel_input_add=pa_cli_uxsel_input_add uxsel_input_remove=pa_cli_uxsel_input_remove
    modalfatalbox=pa_cli_modalfatalbox nonfatal=pa_cli_nonfatal
    timer_change_notify=pa_cli_timer_change_notify old_keyfile_warning=pa_cli_old_keyfile_warning)
endfunction()
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  pageant_cli_objects(pageant-cli)
  add_library(pageant-ui STATIC src/app/agent_controller.cpp src/app/agent_controller.h
    src/app/language.cpp src/app/language.h src/ui/agent_window.cpp src/ui/agent_window.h)
  set_target_properties(pageant-ui PROPERTIES AUTOMOC ON)
  target_link_libraries(pageant-ui PUBLIC pageant-agent Qt6::Widgets)
  add_executable(pageant src/app/main.cpp $<TARGET_OBJECTS:pageant-cli>)
  target_link_libraries(pageant PRIVATE pageant-ui suite-pageant-core)
  target_link_options(pageant PRIVATE -Wl,--wrap=uxsel_input_add -Wl,--wrap=uxsel_input_remove)
  suite_add_backend_list(pageant Pageant)
  include(GNUInstallDirs)
  install(TARGETS pageant RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}" COMPONENT pageant)
  install(FILES resources/pageant.desktop DESTINATION "${CMAKE_INSTALL_DATADIR}/applications" COMPONENT pageant)
endif()
if(BUILD_TESTING)
  add_subdirectory(tests)
endif()
