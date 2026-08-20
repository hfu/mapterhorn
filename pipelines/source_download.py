import csv
import sys
from pathlib import Path

import utils


def load_manifest(source):
    with utils.open_manifest(source) as f:
        return list(csv.DictReader(f))


def download_from_internet(source):
    rows = load_manifest(source)
    print(f'{len(rows)} files in manifest.')

    store_dir = Path(f'source-store/{source}')
    store_dir.mkdir(parents=True, exist_ok=True)

    has_md5 = bool(rows) and bool(rows[0].get('md5'))
    input_file = Path(f'/tmp/{source}_aria2_input.txt')

    if has_md5:
        # Trustworthy remote checksum available (source's own manifest
        # carries a real per-file MD5, usually lifted from a single-part
        # S3 ETag). Hand aria2c the whole manifest and let its own
        # --check-integrity do the skip-if-already-correct decision --
        # no separate local pre-filter needed.
        with open(input_file, 'w') as f:
            for row in rows:
                filename = row['url'].rsplit('/', 1)[-1]
                f.write(row['url'] + '\n')
                f.write(f'  out={filename}\n')
                f.write(f'  checksum=md5={row["md5"]}\n')
        targets = rows
        check_flag = '--check-integrity=true '
    else:
        # No trustworthy remote checksum for this source (e.g. a MinIO
        # mirror handing out placeholder ETags) -- fall back to a local
        # size-only pre-filter, so at least already-complete files never
        # trigger a network request; only genuinely missing/mismatched
        # ones get handed to aria2c.
        missing = []
        for row in rows:
            filename = row['url'].rsplit('/', 1)[-1]
            local_path = store_dir / filename
            if not (local_path.exists()
                    and row['size']
                    and local_path.stat().st_size == int(row['size'])):
                missing.append(row)
        print(f'{len(rows) - len(missing)} already present with matching '
              f'size (skipped, no network request).')
        print(f'{len(missing)} to download.')
        if not missing:
            print('Nothing to download.')
            return
        with open(input_file, 'w') as f:
            for row in missing:
                filename = row['url'].rsplit('/', 1)[-1]
                f.write(row['url'] + '\n')
                f.write(f'  out={filename}\n')
        targets = missing
        check_flag = ''

    command = (
        f'cd source-store/{source} && aria2c -i {input_file} '
        f'-c {check_flag}-j 8 '
        f'--auto-file-renaming=false --allow-overwrite=true '
        f'--summary-interval=30 --console-log-level=warn'
    )
    utils.run_command(command, silent=False)


def main():
    source = None
    if len(sys.argv) > 1:
        source = sys.argv[1]
        print(f'downloading {source}...')
    else:
        print('source argument missing...')
        exit()

    utils.create_folder(f'source-store/{source}/')
    download_from_internet(source)


if __name__ == '__main__':
    main()
