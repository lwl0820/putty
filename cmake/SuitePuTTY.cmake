# 完整客户端核心与 PuTTYgen 的密钥裁剪库使用不同目标、配置头和探针缓存。
include_guard(GLOBAL)
include(CheckIncludeFiles)
include(CheckSymbolExists)
include(CheckCSourceCompiles)

macro(suite_check_headers headers variable)
  check_include_files("${headers}" SUITE_${variable})
  set(${variable} "${SUITE_${variable}}")
endmacro()

macro(suite_check_symbol symbol headers variable)
  check_symbol_exists("${symbol}" "${headers}" SUITE_${variable})
  set(${variable} "${SUITE_${variable}}")
endmacro()

macro(suite_check_source source variable)
  check_c_source_compiles("${source}" SUITE_${variable})
  set(${variable} "${SUITE_${variable}}")
endmacro()

function(suite_add_putty_core)
  if(TARGET suite-putty)
    return()
  endif()
  set(suite_source "${PUTTY_SOURCE_DIR}/vendor/putty")
  set(suite_generated "${CMAKE_BINARY_DIR}/suite-config")
  file(MAKE_DIRECTORY "${suite_generated}")

  # 屏蔽父目录/pg-putty 的普通变量和同名缓存，不把裁剪结果带入完整核心。
  file(STRINGS "${suite_source}/cmake/cmake.h.in" suite_config_lines
    REGEX "^#cmakedefine")
  foreach(line IN LISTS suite_config_lines)
    string(REGEX REPLACE "^#cmakedefine(01)? +([A-Za-z0-9_]+).*" "\\2" variable "${line}")
    set(${variable} FALSE)
  endforeach()
  set(CMAKE_REQUIRED_DEFINITIONS -D_DEFAULT_SOURCE -D_GNU_SOURCE)
  suite_check_symbol(strtoumax "inttypes.h" HAVE_STRTOUMAX)
  suite_check_source("int main(void) { int a[3]; return _Countof(a); }" HAVE_COUNTOF)

  add_library(suite-config INTERFACE)
  target_compile_definitions(suite-config INTERFACE HAVE_CMAKE_H)
  if(MSVC)
    target_compile_options(suite-config INTERFACE /UNDEBUG /utf-8)
  else()
    target_compile_options(suite-config INTERFACE -UNDEBUG)
  endif()

  if(WIN32)
    set(suite_platform windows)
    set(CMAKE_REQUIRED_DEFINITIONS -D_ARM_WINAPI_PARTITION_DESKTOP_SDK_AVAILABLE)
    suite_check_headers("windows.h;winresrc.h" HAVE_WINRESRC_H)
    if(NOT HAVE_WINRESRC_H)
      suite_check_headers("windows.h;winres.h" HAVE_WINRES_H)
      if(NOT HAVE_WINRES_H)
        suite_check_headers("windows.h;win.h" HAVE_WIN_H)
      endif()
    endif()
    suite_check_headers("stdint.h" HAVE_STDINT_H)
    if(NOT HAVE_STDINT_H)
      set(HAVE_NO_STDINT_H TRUE)
    endif()
    suite_check_headers("windows.h;multimon.h" HAVE_MULTIMON_H)
    if(NOT HAVE_MULTIMON_H)
      set(NO_MULTIMON TRUE)
    endif()
    suite_check_headers("winsock2.h;afunix.h" HAVE_AFUNIX_H)
    suite_check_symbol(AddDllDirectory "windows.h" HAVE_ADDDLLDIRECTORY)
    suite_check_symbol(SetDefaultDllDirectories "windows.h" HAVE_SETDEFAULTDLLDIRECTORIES)
    suite_check_symbol(GetNamedPipeClientProcessId "windows.h" HAVE_GETNAMEDPIPECLIENTPROCESSID)
    suite_check_source("#include <windows.h>\nGCP_RESULTSW gcpw;\nint main(void) { return 0; }" HAVE_GCP_RESULTSW)
    set(CMAKE_REQUIRED_LIBRARIES dwmapi)
    suite_check_source("#include <windows.h>\n#include <dwmapi.h>\nint main(void) { RECT r; return DwmGetWindowAttribute(0, DWMWA_EXTENDED_FRAME_BOUNDS, &r, sizeof(r)); }" HAVE_DWMAPI_H)
    unset(CMAKE_REQUIRED_LIBRARIES)
    target_compile_definitions(suite-config INTERFACE _WINDOWS
      _CRT_SECURE_NO_WARNINGS _WINSOCK_DEPRECATED_NO_WARNINGS
      _ARM_WINAPI_PARTITION_DESKTOP_SDK_AVAILABLE NOMINMAX)
    target_link_libraries(suite-config INTERFACE
      advapi32 comdlg32 gdi32 imm32 ole32 shell32 user32 ws2_32 kernel32 winspool)
  else()
    set(suite_platform unix)
    foreach(header sys/auxv.h asm/hwcap.h sys/sysctl.h sys/types.h glob.h utmp.h utmpx.h)
      string(TOUPPER "${header}" variable)
      string(REGEX REPLACE "[/\\.]" "_" variable "${variable}")
      suite_check_headers("${header}" HAVE_${variable})
    endforeach()
    suite_check_symbol(futimes "sys/time.h" HAVE_FUTIMES)
    suite_check_symbol(getaddrinfo "sys/types.h;sys/socket.h;netdb.h" HAVE_GETADDRINFO)
    suite_check_symbol(posix_openpt "stdlib.h;fcntl.h" HAVE_POSIX_OPENPT)
    suite_check_symbol(ptsname "stdlib.h" HAVE_PTSNAME)
    suite_check_symbol(setresuid "unistd.h" HAVE_SETRESUID)
    suite_check_symbol(setresgid "unistd.h" HAVE_SETRESGID)
    suite_check_symbol(strsignal "string.h" HAVE_STRSIGNAL)
    suite_check_symbol(updwtmpx "utmpx.h" HAVE_UPDWTMPX)
    suite_check_symbol(fstatat "sys/types.h;sys/stat.h;unistd.h" HAVE_FSTATAT)
    suite_check_symbol(dirfd "sys/types.h;dirent.h" HAVE_DIRFD)
    suite_check_symbol(setpwent "sys/types.h;pwd.h" HAVE_SETPWENT)
    suite_check_symbol(endpwent "sys/types.h;pwd.h" HAVE_ENDPWENT)
    suite_check_symbol(getauxval "sys/auxv.h" HAVE_GETAUXVAL)
    suite_check_symbol(elf_aux_info "sys/auxv.h" HAVE_ELF_AUX_INFO)
    suite_check_symbol(sysctlbyname "sys/types.h;sys/sysctl.h" HAVE_SYSCTLBYNAME)
    suite_check_symbol(CLOCK_MONOTONIC "time.h" HAVE_CLOCK_MONOTONIC)
    suite_check_symbol(clock_gettime "time.h" HAVE_CLOCK_GETTIME)
    suite_check_source("#include <sys/types.h>\n#include <sys/socket.h>\nint main(void) { struct ucred cr; socklen_t n=sizeof(cr); return getsockopt(0,SOL_SOCKET,SO_PEERCRED,&cr,&n)+cr.pid+cr.uid+cr.gid; }" HAVE_SO_PEERCRED)
    suite_check_source("#include <sys/types.h>\n#include <unistd.h>\nint main(void) { return setpgrp(); }" HAVE_NULLARY_SETPGRP)
    suite_check_source("#include <sys/types.h>\n#include <unistd.h>\nint main(void) { return setpgrp(0,0); }" HAVE_BINARY_SETPGRP)
    if(NOT HAVE_GETADDRINFO)
      set(NO_IPV6 TRUE)
    endif()
    if(NOT HAVE_UTMPX_H)
      set(OMIT_UTMP TRUE)
    endif()
    # Qt 不依赖 GTK；X11 转发及 Xwayland 能力独立保留。
    find_package(X11 REQUIRED)
    target_link_libraries(suite-config INTERFACE X11::X11 ${CMAKE_DL_LIBS} m)
    target_compile_definitions(suite-config INTERFACE _GNU_SOURCE)
  endif()

  configure_file("${suite_source}/cmake/cmake.h.in" "${suite_generated}/cmake.h")
  target_include_directories(suite-config SYSTEM INTERFACE
    "${suite_generated}" "${suite_source}" "${suite_source}/${suite_platform}"
    "${suite_source}/charset" "${suite_source}/terminal")

  find_package(Git QUIET)
  add_custom_command(OUTPUT "${suite_generated}/cmake_commit.c"
    COMMAND "${CMAKE_COMMAND}" "-DGIT_EXECUTABLE=${GIT_EXECUTABLE}"
      "-DOUTPUT_FILE=${suite_generated}/cmake_commit.c" -DOUTPUT_TYPE=header
      -P "${suite_source}/cmake/gitcommit.cmake"
    WORKING_DIRECTORY "${suite_source}"
    DEPENDS "${suite_source}/cmake/gitcommit.cmake" VERBATIM)
  add_custom_command(OUTPUT "${suite_generated}/licence.h"
    COMMAND "${CMAKE_COMMAND}" "-DLICENCE_FILE=${suite_source}/LICENCE"
      "-DOUTPUT_FILE=${suite_generated}/licence.h" -P "${suite_source}/cmake/licence.cmake"
    DEPENDS "${suite_source}/LICENCE" "${suite_source}/cmake/licence.cmake" VERBATIM)

  include("${CMAKE_CURRENT_FUNCTION_LIST_DIR}/SuitePuTTYSources.cmake")
  set(suite_sources ${suite_utils_sources} ${suite_crypto_sources}
    ${suite_keygen_sources} ${suite_ssh_sources} ${suite_otherbackend_sources}
    callback.c timing.c logging.c utils/logeventf.c settings.c cmdline.c
    errsock.c x11disp.c proxy/proxy.c proxy/http.c proxy/socks4.c proxy/socks5.c
    proxy/telnet.c proxy/newmain.c proxy/local.c proxy/interactor.c
    aqsync.c terminal/terminal.c terminal/bidi.c terminal/lineedit.c
    ldisc.c config.c dialog.c)
  list(APPEND suite_sources ${suite_${suite_platform}_utils_sources})
  if(WIN32)
    list(REMOVE_ITEM suite_sources windows/utils/gui-timing.c)
    list(APPEND suite_sources windows/handle-wait.c windows/handle-io.c windows/select-gui.c
      windows/network.c windows/handle-socket.c windows/named-pipe-client.c
      windows/named-pipe-server.c windows/local-proxy.c windows/x11.c
      windows/noise.c windows/agent-client.c windows/gss.c windows/sharing.c
      windows/storage.c windows/serial.c windows/config.c windows/printing.c)
    if(NOT HAVE_STRTOUMAX)
      list(APPEND suite_sources windows/utils/strtoumax.c)
    endif()
  else()
    find_package(Perl REQUIRED)
    add_custom_command(OUTPUT "${suite_generated}/sbcsdat.c"
      COMMAND "${PERL_EXECUTABLE}" "${suite_source}/charset/sbcsgen.pl"
        -o "${suite_generated}/sbcsdat.c"
      DEPENDS "${suite_source}/charset/sbcsgen.pl" "${suite_source}/charset/sbcs.dat" VERBATIM)
    list(APPEND suite_sources ${suite_charset_sources} unix/uxsel.c
      unix/network.c unix/fd-socket.c unix/agent-socket.c unix/peerinfo.c
      unix/local-proxy.c unix/x11.c unix/noise.c unix/agent-client.c unix/gss.c
      unix/sharing.c unix/storage.c unix/serial.c unix/config-unix.c
      unix/unicode.c unix/printing.c unix/no-gtk.c)
    list(APPEND suite_generated_sources "${suite_generated}/sbcsdat.c")
  endif()
  list(REMOVE_DUPLICATES suite_sources)
  list(TRANSFORM suite_sources PREPEND "${suite_source}/")
  # 单个归档允许客户端/代理共用模块间循环引用，不引入任何前端桩。
  add_library(suite-putty STATIC ${suite_sources} ${suite_generated_sources}
    "${suite_generated}/cmake_commit.c" "${suite_generated}/licence.h")
  target_link_libraries(suite-putty PUBLIC suite-config)
  # Pageant 自带禁止随机调用的 random_read，不能与客户端 PRNG 同归档。
  add_library(suite-pageant-core STATIC "${suite_source}/pageant.c")
  target_link_libraries(suite-pageant-core PUBLIC suite-putty)
endfunction()

# 每个产品使用自己的 APPNAME 和后端选择，不把 Pageant/PuTTY 的列表混在共享库。
function(suite_add_backend_list target appname)
  cmake_parse_arguments(BE "SSH;SERIAL;OTHERBACKENDS" "" "" ${ARGN})
  add_library(suite-${target}-be-list OBJECT "${PUTTY_SOURCE_DIR}/vendor/putty/be_list.c")
  target_link_libraries(suite-${target}-be-list PRIVATE suite-config)
  target_compile_definitions(suite-${target}-be-list PRIVATE APPNAME=${appname})
  foreach(backend SSH SERIAL OTHERBACKENDS)
    target_compile_definitions(suite-${target}-be-list PRIVATE "${backend}=$<BOOL:${BE_${backend}}>")
  endforeach()
  target_sources(${target} PRIVATE $<TARGET_OBJECTS:suite-${target}-be-list>)
endfunction()
