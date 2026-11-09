#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the actual VPP formatter against wire vectors and truncated headers."""
import argparse
import os
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('root', type=Path)
args = parser.parse_args()
root = args.root.resolve()
node = (root / 'plugin-example/uet/node.c').read_text()
end = node.index('} uet_trace_t;') + len('} uet_trace_t;')
trace_type = node[node.rfind('typedef struct', 0, end):end]
helpers = node[node.index('static u16\nuet_trace_u16'):node.index('static u8 *\nformat_uet_trace')]
defines = '\n'.join(re.findall(r'^#define UET_WIRE_.*$', node, re.MULTILINE))
tests = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>

static char *decode(uet_trace_t *trace) {
  u8 *s = uet_trace_format_packet(0, trace);
  vec_add1(s, 0);
  char *copy = strdup((char *)s);
  vec_free(s);
  return copy;
}
static void contains(uet_trace_t *trace, const char *expected) {
  char *s = decode(trace);
  if (!strstr(s, expected)) {
    fprintf(stderr, "Expected %s in %s\n", expected, s);
    abort();
  }
  free(s);
}
int main(void) {
  clib_mem_init(0, 32 << 20);
  /* IPv4/native RUD request, RETX, PSN 0x12345678, SPDCID 65, DPDCID 66. */
  const u8 wire[] = {
    0x45,0,0,36,0,0,0x40,0,64,253,0,0,198,18,0,1,198,18,0,2,
    0,1,0,0, 0x11,0x90,0,0,0x12,0x34,0x56,0x78,0,65,0,66
  };
  uet_trace_t t = { .ip_version = 4, .header_length = sizeof(wire) };
  memcpy(t.header, wire, sizeof(wire));
  contains(&t, "198.18.0.1 -> 198.18.0.2 PDS RUD_REQ");
  contains(&t, "RETX 1 SPDCID 65 DPDCID 66 PSN 305419896");
  for (unsigned n = 0; n < sizeof(wire); n++) {
    t.header_length = n;
    char *s = decode(&t);
    assert(strstr(s, "unavailable") || strstr(s, "truncated"));
    assert(!strstr(s, "SPDCID"));
    free(s);
  }
  t.header_length = sizeof(wire);
  t.header[25] |= 4;
  contains(&t, "PDC-info 0x0042 SYN 1");
  t.header[25] &= ~4;
  t.header[6] = 0x20;
  contains(&t, "fragmented IPv4");
  t.header[6] = 0x40;
  t.header[24] = 0x08;
  contains(&t, "type 1"); /* Security header must not be decoded as a request. */
  t.header[24] = 0x51;
  t.header[25] = 0x98;
  contains(&t, "RUDI NACK layout not decoded");

  /* IPv6/UDP ACK, signed ACK offset, CACK_PSN 0x12345678. */
  const u8 udp[] = {0x12,0x34,0xbf,0xfe,0,20,0,0,
                    0x39,0x90,0xff,0xff,0x12,0x34,0x56,0x78,0,66,0,65};
  memset(&t, 0, sizeof(t));
  t.ip_version = 6;
  t.header_length = 60;
  t.header[0] = 0x60;
  t.header[5] = 20;
  t.header[6] = 17;
  t.header[23] = 1;
  t.header[39] = 2;
  memcpy(t.header + 40, udp, sizeof(udp));
  contains(&t, "::1 -> ::2 PDS ACK");
  contains(&t, "SPDCID 66 DPDCID 65 CACK_PSN 305419896");
  for (unsigned n = 0; n < 60; n++) {
    t.header_length = n;
    char *s = decode(&t);
    assert(strstr(s, "unavailable") || strstr(s, "truncated"));
    assert(!strstr(s, "SPDCID"));
    free(s);
  }
  t.header_length = 60;
  t.header[45] = 7;
  contains(&t, "invalid UET UDP");
  t.header[45] = 20;
  t.header[6] = 44;
  contains(&t, "unsupported IP protocol 44");
  puts("Formatter vectors, SYN/security/RUDI cases and all truncated-header lengths passed.");
  return 0;
}
'''
directory = root / 'trace-format-check'
directory.mkdir(exist_ok=True)
source = directory / 'check.c'
source.write_text('#include <uet/uet.h>\n#include <vnet/udp/udp_packet.h>\n' +
                  trace_type + '\n' + defines + '\n' + helpers + tests)
lib = next((root / 'vpp-install').rglob('libvnet.so')).parent
binary = directory / 'check'
subprocess.run(['cc', '-O1', '-g', '-fsanitize=address,undefined',
                '-I' + str(root / 'plugin-example'),
                '-I' + str(root / 'vpp-install/include'), str(source),
                '-L' + str(lib), '-Wl,-rpath,' + str(lib),
                '-lvnet', '-lvlib', '-lvppinfra', '-o', str(binary)], check=True)
env = dict(os.environ, LD_LIBRARY_PATH=str(lib), ASAN_OPTIONS='detect_leaks=0')
subprocess.run([str(binary)], env=env, check=True)
