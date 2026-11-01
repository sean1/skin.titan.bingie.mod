# Native VPN library

The shared library provides a local authenticated CONNECT proxy backed by a
userspace WireGuard tunnel. The Android ARM64, Android ARMv7, and Linux AMD64 builds require
Go 1.22.2. The Android build also requires the Linux-hosted Android NDK r27d.
Google publishes SHA-1 `22105e410cf29afcf163760cc95522b9fb981121` for
`android-ndk-r27d-linux.zip`.

From this directory, create isolated build caches and point `VPN_NDK_ROOT` at
the extracted Android NDK r27d directory:

```sh
mkdir -p ../../../../tmp/vpn-native-build ../bin
VPN_BUILD_ROOT="$(cd ../../../../tmp/vpn-native-build && pwd)"
VPN_NDK_ROOT=/path/to/android-ndk-r27d
export GOCACHE="$VPN_BUILD_ROOT/gocache" GOMODCACHE="$VPN_BUILD_ROOT/gomodcache" GOPATH="$VPN_BUILD_ROOT/gopath" GOTOOLCHAIN=local
GOOS=linux GOARCH=amd64 CGO_ENABLED=1 go build -mod=readonly -buildvcs=false -trimpath -buildmode=c-shared -o ../bin/libbingie_vpn_linux_amd64.so .
GOOS=android GOARCH=arm64 CGO_ENABLED=1 CC="$VPN_NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android24-clang" go build -mod=readonly -buildvcs=false -trimpath -buildmode=c-shared -o ../bin/libbingie_vpn_android_arm64.so .
GOOS=android GOARCH=arm GOARM=7 CGO_ENABLED=1 CC="$VPN_NDK_ROOT/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi24-clang" go build -mod=readonly -buildvcs=false -trimpath -buildmode=c-shared -o ../bin/libbingie_vpn_android_armv7.so .
rm -f ../bin/libbingie_vpn_linux_amd64.h ../bin/libbingie_vpn_android_arm64.h ../bin/libbingie_vpn_android_armv7.h
```

Keep a loaded Go shared library loaded for the lifetime of the Kodi process.
`Status` reports whether a handshake has ever completed in the current run; it
is not a live connectivity check. Third-party license and patent notices are
in `third_party/`, organized by module and version.
