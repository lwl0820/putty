# 发布目标只安装本应用；上游 CLI 和测试夹具不会进入安装树。
include_guard(GLOBAL)
if(NOT TARGET puttygen-gui)
    return()
endif()
include(GNUInstallDirs)
option(PUTTYGEN_RELEASE_PACKAGING "使用锁定的 Qt 生成完整发布包" OFF)
set(PUTTYGEN_QT_SOURCE_MATERIAL "${Qt6_DIR}/../../../share/puttygen-gui/qt-sources"
    CACHE PATH "Qt 完整许可证与已校验源码目录")

if(PUTTYGEN_RELEASE_PACKAGING)
    if(NOT Qt6_VERSION VERSION_EQUAL "6.8.3")
        message(FATAL_ERROR "发布包须使用 tools/qt/qt-sources.json 锁定的 Qt 6.8.3")
    endif()
    if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8 OR
       NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(AMD64|amd64|x86_64)$")
        message(FATAL_ERROR "首版发布构建仅支持 x64")
    endif()
    if(NOT EXISTS "${PUTTYGEN_QT_SOURCE_MATERIAL}/qt-sources.json" OR
       NOT EXISTS "${PUTTYGEN_QT_SOURCE_MATERIAL}/licenses/qtbase/LGPL-3.0-only.txt")
        message(FATAL_ERROR "缺少 Qt 来源或完整 LGPL 文件；先运行 tools/qt/build_qt.py")
    endif()
    if(WIN32)
        if(NOT MSVC OR MSVC_VERSION LESS 1930 OR MSVC_VERSION GREATER_EQUAL 1950)
            message(FATAL_ERROR "Windows 发布构建须使用 MSVC 2022")
        endif()
        set(CMAKE_INSTALL_BINDIR ".")
        set(CMAKE_INSTALL_LIBDIR ".")
    elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        file(READ "/etc/os-release" _puttygen_os_release)
        if(NOT _puttygen_os_release MATCHES "(^|\n)ID=ubuntu(\n|$)" OR
           NOT _puttygen_os_release MATCHES "VERSION_ID=\"(22\.04|24\.04)\"")
            message(FATAL_ERROR "DEB 须分别在 Ubuntu 22.04 或 24.04 中构建")
        endif()
        set(_puttygen_ubuntu "${CMAKE_MATCH_1}")
        if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
            message(FATAL_ERROR "Ubuntu 发布构建须使用对应发行版的 GCC")
        endif()
        if(_puttygen_ubuntu STREQUAL "22.04" AND
           (CMAKE_CXX_COMPILER_VERSION VERSION_LESS "11" OR
            NOT CMAKE_CXX_COMPILER_VERSION VERSION_LESS "12"))
            message(FATAL_ERROR "Ubuntu 22.04 发布构建须使用 GCC 11")
        elseif(_puttygen_ubuntu STREQUAL "24.04" AND
               (CMAKE_CXX_COMPILER_VERSION VERSION_LESS "13" OR
                NOT CMAKE_CXX_COMPILER_VERSION VERSION_LESS "14"))
            message(FATAL_ERROR "Ubuntu 24.04 发布构建须使用 GCC 13")
        endif()
        set(CMAKE_INSTALL_PREFIX "/opt/puttygen-gui" CACHE PATH "发布包独立安装目录" FORCE)
        set(CMAKE_INSTALL_LIBDIR "lib")
    else()
        message(FATAL_ERROR "首版发布包仅支持 Windows 和 Ubuntu")
    endif()
endif()

if(UNIX AND NOT APPLE)
    set_target_properties(puttygen-gui PROPERTIES
        INSTALL_RPATH "$ORIGIN/../${CMAKE_INSTALL_LIBDIR}"
        INSTALL_RPATH_USE_LINK_PATH FALSE)
endif()
install(TARGETS puttygen-gui RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
set(_puttygen_notice_dir "${CMAKE_INSTALL_DATADIR}/licenses/puttygen-gui")
install(FILES "${PUTTY_SOURCE_DIR}/LICENSE" "${PUTTY_SOURCE_DIR}/THIRD_PARTY_NOTICES.md"
    DESTINATION "${_puttygen_notice_dir}")
install(FILES "${PUTTY_SOURCE_DIR}/vendor/putty/LICENCE"
    DESTINATION "${_puttygen_notice_dir}" RENAME "PuTTY-LICENCE")
install(FILES "${PUTTYGEN_SOURCE_DIR}/README.md" "${PUTTY_SOURCE_DIR}/docs/构建与发布.md"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/doc/puttygen-gui")

if(PUTTYGEN_RELEASE_PACKAGING)
    get_filename_component(_puttygen_qt_translations "${QT6_INSTALL_TRANSLATIONS}"
        ABSOLUTE BASE_DIR "${QT6_INSTALL_PREFIX}")
    set(_puttygen_qtbase_zh "${_puttygen_qt_translations}/qtbase_zh_CN.qm")
    if(NOT EXISTS "${_puttygen_qtbase_zh}")
        message(FATAL_ERROR "发布包缺少 Qt 标准控件中文翻译：${_puttygen_qtbase_zh}")
    endif()
    # 自动部署会合并成 qt_zh_CN.qm；应用按原目录名称加载 Qt 标准控件翻译。
    # 英文直接使用 Qt 源文本，只安装本应用支持的中文目录。
    install(FILES "${_puttygen_qtbase_zh}" DESTINATION "translations")
    install(DIRECTORY "${PUTTYGEN_QT_SOURCE_MATERIAL}/"
        DESTINATION "${_puttygen_notice_dir}/Qt")
    if(WIN32)
        # ZIP 使用应用本地 CRT，不依赖用户先运行 VC 再发行安装器。
        set(CMAKE_INSTALL_SYSTEM_RUNTIME_DESTINATION "${CMAKE_INSTALL_BINDIR}")
        set(CMAKE_INSTALL_UCRT_LIBRARIES FALSE)
        include(InstallRequiredSystemLibraries)
        if(NOT CMAKE_INSTALL_SYSTEM_RUNTIME_LIBS)
            message(FATAL_ERROR "找不到可再发行 MSVC 运行库 DLL")
        endif()
        qt_generate_deploy_app_script(TARGET puttygen-gui
            OUTPUT_SCRIPT _puttygen_deploy NO_COMPILER_RUNTIME NO_TRANSLATIONS
            DEPLOY_TOOL_OPTIONS --release)
    else()
        # Wayland 的 shell、绘图及装饰插件由对应模块提供，必须进入部署依赖图。
        find_package(Qt6 6.8 REQUIRED COMPONENTS WaylandClient)
        target_link_libraries(puttygen-gui PRIVATE Qt6::WaylandClient)
        qt_import_plugins(puttygen-gui INCLUDE_BY_TYPE platforms
            Qt6::QXcbIntegrationPlugin Qt6::QWaylandIntegrationPlugin
            Qt6::QWaylandEglPlatformIntegrationPlugin Qt6::QOffscreenIntegrationPlugin
            platforminputcontexts Qt6::QIbusPlatformInputContextPlugin
            Qt6::QComposePlatformInputContextPlugin
            platformthemes Qt6::QXdgDesktopPortalThemePlugin)
        qt_generate_deploy_app_script(TARGET puttygen-gui
            OUTPUT_SCRIPT _puttygen_deploy NO_TRANSLATIONS)
        configure_file("${PUTTYGEN_SOURCE_DIR}/packaging/puttygen-gui.desktop.in"
            "${PUTTYGEN_BINARY_DIR}/puttygen-gui.desktop" @ONLY)
        install(FILES "${PUTTYGEN_BINARY_DIR}/puttygen-gui.desktop" DESTINATION "/usr/share/applications")
        install(FILES "${PUTTYGEN_SOURCE_DIR}/packaging/puttygen-gui.svg"
            DESTINATION "/usr/share/icons/hicolor/scalable/apps")
        install(PROGRAMS "${PUTTYGEN_SOURCE_DIR}/packaging/puttygen-gui.sh"
            DESTINATION "/usr/bin" RENAME "puttygen-gui")
    endif()
    # 普通 add_executable 不自动执行 Qt finalizer；缺少该步骤不会生成插件部署清单。
    qt_finalize_target(puttygen-gui)
    # Qt 在首次 find_package 时记录默认目录，安装时显式覆盖本包布局。
    install(CODE "set(QT_DEPLOY_BIN_DIR \"${CMAKE_INSTALL_BINDIR}\")\nset(QT_DEPLOY_LIB_DIR \"${CMAKE_INSTALL_LIBDIR}\")\nset(QT_DEPLOY_PLUGINS_DIR \"plugins\")\nset(QT_DEPLOY_TRANSLATIONS_DIR \"translations\")")
    install(SCRIPT "${_puttygen_deploy}")
    configure_file("${PUTTYGEN_SOURCE_DIR}/packaging/verify_runtime.cmake.in"
        "${PUTTYGEN_BINARY_DIR}/verify_runtime.cmake" @ONLY)
    install(SCRIPT "${PUTTYGEN_BINARY_DIR}/verify_runtime.cmake")
endif()

set(CPACK_PACKAGE_NAME "puttygen-gui")
set(CPACK_PACKAGE_VENDOR "PuTTYgen GUI contributors")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "PuTTYgen 跨平台图形密钥工具")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_RESOURCE_FILE_LICENSE "${PUTTY_SOURCE_DIR}/LICENSE")
set(CPACK_PACKAGE_CHECKSUM "SHA256")
set(CPACK_STRIP_FILES TRUE)
set(CPACK_MONOLITHIC_INSTALL TRUE)
if(PUTTYGEN_RELEASE_PACKAGING)
    if(WIN32)
        set(CPACK_GENERATOR "ZIP")
        set(CPACK_PACKAGE_FILE_NAME "puttygen-gui-${PROJECT_VERSION}-windows-x64")
        set(CPACK_INCLUDE_TOPLEVEL_DIRECTORY TRUE)
    else()
        set(CPACK_GENERATOR "DEB")
        set(CPACK_SET_DESTDIR TRUE)
        set(CPACK_PACKAGE_RELOCATABLE FALSE)
        set(CPACK_DEBIAN_PACKAGE_NAME "puttygen-gui")
        set(CPACK_DEBIAN_PACKAGE_ARCHITECTURE "amd64")
        set(CPACK_DEBIAN_PACKAGE_MAINTAINER "PuTTYgen GUI contributors")
        set(CPACK_DEBIAN_PACKAGE_SECTION "utils")
        set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS TRUE)
        set(CPACK_DEBIAN_PACKAGE_GENERATE_SHLIBS FALSE)
        # 打包时目标 /opt 尚未安装，使用本次构建的 Qt 私有库目录解析依赖。
        set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS_PRIVATE_DIRS "${Qt6_DIR}/../..")
        set(CPACK_DEBIAN_PACKAGE_DEPENDS "fonts-noto-cjk")
        set(CPACK_DEBIAN_PACKAGE_CONTROL_EXTRA
            "${PUTTYGEN_SOURCE_DIR}/packaging/postinst;${PUTTYGEN_SOURCE_DIR}/packaging/postrm")
        set(CPACK_DEBIAN_PACKAGE_CONTROL_STRICT_PERMISSION TRUE)
        set(CPACK_DEBIAN_PACKAGE_VERSION "${PROJECT_VERSION}-1ubuntu${_puttygen_ubuntu}")
        set(CPACK_PACKAGE_FILE_NAME "puttygen-gui-${PROJECT_VERSION}-ubuntu${_puttygen_ubuntu}-amd64")
    endif()
else()
    # 开发归档不能被误当作含 Qt 的正式便携包。
    set(CPACK_GENERATOR "TGZ")
    set(CPACK_PACKAGE_FILE_NAME "puttygen-gui-${PROJECT_VERSION}-development-${CMAKE_SYSTEM_NAME}")
endif()
set(CPACK_OUTPUT_CONFIG_FILE "${CMAKE_BINARY_DIR}/CPackConfig.cmake")
set(CPACK_SOURCE_OUTPUT_CONFIG_FILE "${CMAKE_BINARY_DIR}/CPackSourceConfig.cmake")
include(CPack)
