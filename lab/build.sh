#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build an isolated lab from public upstream sources. Run as a regular user.
set -euo pipefail

lab_root=${1:-"$PWD/uet-vpp-lab"}
jobs=${JOBS:-4}
recipe_dir=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$lab_root"
lab_root=$(cd "$lab_root" && pwd)

vpp_revision=4acb320269bc4f3533e35c80afedac38853b2f60
uet_revision=5b4e0491329b8e28764bca6847e2f6a0eaa84b9b
fabric_revision=e43589a5113ad3db0a18a6a1a02b6e6f4ee317d6

checkout_tag() {
    local url=$1 directory=$2 tag=$3 expected=$4
    if [[ ! -d "$directory/.git" ]]; then
        git clone --depth 1 --branch "$tag" "$url" "$directory"
    fi
    [[ $(git -C "$directory" rev-parse HEAD) == "$expected" ]] || {
        echo "Unexpected revision in $directory; expected $expected" >&2
        exit 1
    }
    git -C "$directory" diff --exit-code --quiet
    git -C "$directory" diff --cached --exit-code --quiet
}

checkout_tag https://github.com/FDio/vpp.git "$lab_root/vpp" v26.10 "$vpp_revision"
if [[ ${UET_LAB_INSTALL_DEPS:-0} == 1 ]]; then
    make -C "$lab_root/vpp" UNATTENDED=yes install-dep
    sudo apt-get install -y autoconf automake libtool pkg-config libnuma-dev
fi
checkout_tag https://github.com/ofiwg/libfabric.git "$lab_root/libfabric" v1.20.1 "$fabric_revision"
if [[ ! -d "$lab_root/uet-ref-prov/.git" ]]; then
    git init "$lab_root/uet-ref-prov"
    git -C "$lab_root/uet-ref-prov" remote add origin https://github.com/ultraethernet/uet-ref-prov.git
    git -C "$lab_root/uet-ref-prov" fetch --depth 1 origin "$uet_revision"
    git -C "$lab_root/uet-ref-prov" checkout --detach FETCH_HEAD
fi
[[ $(git -C "$lab_root/uet-ref-prov" rev-parse HEAD) == "$uet_revision" ]]
git -C "$lab_root/uet-ref-prov" diff --exit-code --quiet
git -C "$lab_root/uet-ref-prov" diff --cached --exit-code --quiet

cmake -S "$lab_root/vpp/src" -B "$lab_root/vpp-build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$lab_root/vpp-install" \
    '-DVPP_PLUGINS=memif;nsim;af_packet;tracepath;policer' \
    -DVPP_DRIVERS=none -DVPP_CRYPTO_ENGINES=none \
    -DVPP_BUILD_VPPINFRA_TESTS=OFF -DVPP_USE_LTO=OFF
cmake --build "$lab_root/vpp-build" --parallel "$jobs"
cmake --install "$lab_root/vpp-build"

if [[ ! -f "$lab_root/libfabric/Makefile" ]]; then
    (cd "$lab_root/libfabric"
     ./autogen.sh
     ./configure --enable-only --enable-sockets --disable-static)
fi
make -C "$lab_root/libfabric" -j"$jobs"

sdk_config=$(find "$lab_root/vpp-install" -name VPPConfig.cmake -print -quit)
[[ -n "$sdk_config" ]]
# Keep the pinned checkout and libuet unchanged. Add PDS header decoding only
# to a generated copy of the VPP plugin used by this diagnostic lab.
mkdir -p "$lab_root/plugin-example"
cp -a "$lab_root/uet-ref-prov/vpp-plugin/." "$lab_root/plugin-example/"
patch --batch -s -d "$lab_root/plugin-example" -p1 -i "$recipe_dir/vpp-pds-trace.patch"
cp -a "$lab_root/vpp/src/plugins/nsim" "$lab_root/plugin-example/"
patch --batch -s -d "$lab_root/plugin-example" -p1 -i "$recipe_dir/nsim-trace.patch"
printf '\nadd_subdirectory(nsim)\n' >> "$lab_root/plugin-example/CMakeLists.txt"
cmake -S "$lab_root/plugin-example" -B "$lab_root/uet-trace-plugin-build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$lab_root/vpp-install" \
    -DVPP_DIR="$(dirname "$sdk_config")"
cmake --build "$lab_root/uet-trace-plugin-build" --parallel "$jobs"
make -C "$lab_root/uet-ref-prov" -j"$jobs" vpp \
    LIBFABRIC="$lab_root/libfabric" VPP_PLUGIN_BUILD="$lab_root/uet-trace-plugin-build" \
    CFLAGS='-O2 -g -Wall -Wno-unused-variable -Wno-implicit-function-declaration -Wno-int-conversion -Wno-address-of-packed-member'

# Generate a separate example application. The official checkout, transport
# library remain unmodified. See peer-identity.patch and vpp-pds-trace.patch.
mkdir -p "$lab_root/client-example"
cp "$lab_root/uet-ref-prov/uet.c" "$lab_root/client-example/uet.c"
patch --batch -s -d "$lab_root/client-example" -p1 -i "$recipe_dir/peer-identity.patch"
cc -O2 -g -D_GNU_SOURCE -Wno-implicit-function-declaration -Wno-int-conversion \
    -I"$lab_root/uet-ref-prov" -I"$lab_root/uet-ref-prov/util" \
    -I"$lab_root/uet-ref-prov/imp_shim" -I"$lab_root/uet-ref-prov/nic_shim" \
    -I"$lab_root/uet-ref-prov/crypto" \
    -I"$lab_root/libfabric" -I"$lab_root/libfabric/include" \
    "$lab_root/client-example/uet.c" -o "$lab_root/uet_demo" \
    -L"$lab_root/uet-ref-prov" -lvppuet \
    -L"$lab_root/uet-trace-plugin-build/lib" -luet_vpp_client \
    -L"$lab_root/libfabric/src/.libs" -lfabric -lpthread

"$lab_root/vpp-install/bin/vpp" --version
printf 'Lab built in %s\n' "$lab_root"
