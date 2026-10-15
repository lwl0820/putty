include("${PUTTY_SOURCE_DIR}/cmake/SuiteCLI.cmake")
suite_add_cli(psftp)
include("${PUTTY_SOURCE_DIR}/cmake/SuiteTestSupport.cmake")
suite_add_cli_checks(psftp)
