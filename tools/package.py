#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Package this public tree with the already-sanitized evidence archives."""
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'downloads'
EVIDENCE = ['uet-vpp-debugging-evidence-20261108.zip',
            'uet-vpp-transport-evidence-20261109.zip',
            'uet-vpp-native-cli-evidence-20261108.zip']
LAB = ['build.sh', 'lab.py', 'README.md', 'peer-identity.patch',
       'vpp-pds-trace.patch', 'nsim-trace.patch', 'check_trace_format.py',
       'transport_scenarios.py']
NOTICES = ['LICENSE', 'NOTICE', 'PRIVACY.md', 'privacy-report.json',
           'LICENSES/MIT-UEC.txt', 'LICENSES/Apache-2.0.txt']


def add(bundle, name, data):
    item = ZipInfo(name, date_time=(2026, 11, 10, 0, 0, 0))
    item.compress_type = ZIP_DEFLATED
    item.create_system = 3
    item.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
    bundle.writestr(item, data)


def main():
    OUTPUT.mkdir(exist_ok=True)
    for name in EVIDENCE:
        if not (OUTPUT / name).is_file():
            raise SystemExit('Download the sanitized evidence release assets into downloads/ first: ' + name)
    with ZipFile(OUTPUT / 'uet-vpp-observability-lab.zip', 'w', ZIP_DEFLATED) as bundle:
        for name in LAB:
            add(bundle, 'public-uet-vpp-lab/' + name, (ROOT / 'lab' / name).read_bytes())
        for name in NOTICES:
            add(bundle, 'public-uet-vpp-lab/' + name, (ROOT / name).read_bytes())
    with ZipFile(OUTPUT / 'uet-vpp-article-and-lab.zip', 'w', ZIP_DEFLATED) as bundle:
        for name in ('article.html', 'article.md', 'article.txt'):
            add(bundle, 'uet-vpp-article/' + name, (ROOT / 'docs' / name).read_bytes())
        for name in EVIDENCE + ['uet-vpp-observability-lab.zip']:
            add(bundle, 'uet-vpp-article/' + name, (OUTPUT / name).read_bytes())
        for name in ('vpp-cli-debugging-results.txt', 'vpp-cli-transport-results.txt',
                     'transport-validation-x86-20261109.json',
                     'reorder-recovery.md', 'fresh-vm-validation.json'):
            add(bundle, 'uet-vpp-article/' + name, (ROOT / 'evidence' / name).read_bytes())
        for name in NOTICES:
            add(bundle, 'uet-vpp-article/' + name, (ROOT / name).read_bytes())
    checksums = []
    for path in sorted(OUTPUT.glob('*.zip')):
        checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name)
    (OUTPUT / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n')
    print('Rebuilt companion and full bundle; wrote SHA256SUMS.')


if __name__ == '__main__':
    main()
