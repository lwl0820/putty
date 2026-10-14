# 上游仅提供密钥库；保持发布构建中的断言，避免绕过上游安全约束。
include_guard(GLOBAL)
set(PG_PUTTY_DIR "${PUTTY_SOURCE_DIR}/vendor/putty")
set(PG_PUTTY_CONFIG "${CMAKE_CURRENT_BINARY_DIR}/putty-config")
file(MAKE_DIRECTORY "${PG_PUTTY_CONFIG}")
include(CheckSymbolExists)
check_symbol_exists(strtoumax "inttypes.h" HAVE_STRTOUMAX)
set(NO_GSSAPI ON)
set(NOT_X_WINDOWS ON)
configure_file("${PG_PUTTY_DIR}/cmake/cmake.h.in" "${PG_PUTTY_CONFIG}/cmake.h")
# 保守使用上游软件实现，避免将开发机 CPU 能力作为发布物前提。
file(GLOB pg_crypto CONFIGURE_DEPENDS "${PG_PUTTY_DIR}/crypto/*.c")
list(FILTER pg_crypto EXCLUDE REGEX "(-ni|-neon|-clmul|enable_dit)\\.c$")
file(GLOB pg_keygen CONFIGURE_DEPENDS "${PG_PUTTY_DIR}/keygen/*.c")
set(pg_utils
  base64_decode_atom base64_decode base64_encode_atom base64_encode base64_valid
  burnstr chomp dupcat dupprintf dupstr fgetline key_components marshal memory
  memxor nullstrcmp out_of_memory ptrlen read_file_into seat_dialog_text smemclr
  smemeq ssh_key_clone ssh2_pick_fingerprint sshutils strbuf string_length_for_printf
  tree234 ltime version bufchain write_c_string_literal)
foreach(pg_util IN LISTS pg_utils)
  list(APPEND pg_util_sources "${PG_PUTTY_DIR}/utils/${pg_util}.c")
endforeach()
add_library(pg-putty STATIC ${pg_crypto} ${pg_keygen} ${pg_util_sources}
  "${PG_PUTTY_DIR}/import.c" "${PG_PUTTY_DIR}/sshpubk.c"
  "${PG_PUTTY_DIR}/sshrand.c" "${PG_PUTTY_DIR}/stubs/no-timing.c" "${PG_PUTTY_DIR}/stubs/null-key.c"
  "${PG_PUTTY_DIR}/stubs/null-cipher.c" "${PG_PUTTY_DIR}/stubs/null-mac.c")
if(WIN32)
  set(pg_platform windows)
  target_compile_definitions(pg-putty PUBLIC _WINDOWS WIN32_LEAN_AND_MEAN NOMINMAX _CRT_SECURE_NO_WARNINGS)
  target_link_libraries(pg-putty PUBLIC bcrypt)
else()
  set(pg_platform unix)
  target_compile_definitions(pg-putty PUBLIC _GNU_SOURCE)
  target_link_libraries(pg-putty PUBLIC m)
endif()
target_compile_definitions(pg-putty PUBLIC HAVE_CMAKE_H)
target_include_directories(pg-putty SYSTEM PUBLIC "${PG_PUTTY_DIR}" "${PG_PUTTY_CONFIG}"
  "${PG_PUTTY_DIR}/${pg_platform}" "${PG_PUTTY_DIR}/charset")
if(MSVC)
  target_compile_options(pg-putty PUBLIC /UNDEBUG /utf-8)
else()
  target_compile_options(pg-putty PUBLIC -UNDEBUG)
endif()
