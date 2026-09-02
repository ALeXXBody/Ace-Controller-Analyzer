/*
 * CD3217-Analyzer firmware version.
 *
 * Committed default is bumped with each release; CI overwrites it from the
 * release tag before building (see build.yml "Stamp firmware version"), so
 * release binaries always report their true release version.
 */

#ifndef CD3217_FW_VERSION_H
#define CD3217_FW_VERSION_H

#ifndef CD3217_FW_VERSION
#define CD3217_FW_VERSION "0.9.1"
#endif

#endif  // CD3217_FW_VERSION_H
