#!/usr/bin/env python3
"""
PMTiles ファイルの画素データ検証スクリプト

目的: 実際に画像タイルデータが正しく変換・生成されているか検証
"""

import os
import sys
from pathlib import Path
from pmtiles.reader import Reader, MmapSource, all_tiles
import numpy as np
from PIL import Image
import io

SAMPLE_LIMIT = 20

def validate_pmtiles_pixels(filepath):
    """PMTilesファイルが実際の画像データを含んでいることを検証"""
    try:
        if not os.path.isfile(filepath):
            return False, "File not found"

        size = os.path.getsize(filepath)
        if size < 1024:  # 最小限のデータチェック
            return False, f"File too small ({size} bytes)"

        with open(filepath, 'r+b') as f:
            reader = Reader(MmapSource(f))

            # Real tile coordinates live wherever the actual geography is
            # (e.g. x~971199 at z21 for Freetown) - iterate the archive's
            # own directory instead of guessing coordinates near the origin.
            # Sample every STRIDE-th tile rather than just the first N: tile
            # IDs are ordered (roughly z-order), so the first few tiles are
            # often clustered at one edge and can be legitimate all-nodata
            # tiles even when the archive has plenty of real content.
            total_tiles = 0
            tiles_read = 0
            non_empty_tiles = 0
            STRIDE = 137  # coprime-ish odd stride to avoid periodicity artifacts

            for tile_tuple, tile_data in all_tiles(reader.get_bytes):
                total_tiles += 1
                if total_tiles % STRIDE != 1:
                    continue
                if tiles_read >= SAMPLE_LIMIT:
                    continue
                if tile_data:
                    tiles_read += 1
                    try:
                        img = Image.open(io.BytesIO(tile_data))
                        img_array = np.array(img)
                        if img_array.size > 0 and np.max(img_array) > 0:
                            non_empty_tiles += 1
                    except Exception:
                        pass

        if tiles_read == 0:
            return False, "No tiles found in archive"

        if non_empty_tiles == 0:
            return False, "All tiles are empty/blank"

        occupancy = (non_empty_tiles / tiles_read) * 100
        return True, f"✅ {tiles_read} tiles, {non_empty_tiles} with pixel data ({occupancy:.1f}% occupancy)"

    except Exception as e:
        return False, f"Error: {str(e)[:60]}"


def check_webp_tiles(tmp_folder):
    """一時フォルダのWebPタイルをチェック"""
    if not os.path.isdir(tmp_folder):
        return None

    webp_files = list(Path(tmp_folder).glob('*.webp'))
    if not webp_files:
        return None

    non_empty = 0
    for webp_file in webp_files[:10]:  # 最初の10個チェック
        try:
            img = Image.open(webp_file)
            img_array = np.array(img)
            if np.max(img_array) > 0:
                non_empty += 1
        except:
            pass

    return {
        'total': len(webp_files),
        'sampled': min(10, len(webp_files)),
        'non_empty': non_empty
    }


def main():
    """メイン検証ルーチン"""

    print("=== PMTiles Pixel Validation ===\n")

    # 1. 既存のPMTilesファイルをチェック
    pmtiles_dir = Path('pmtiles-store')
    if pmtiles_dir.exists():
        pmtiles_files = list(pmtiles_dir.glob('**/*.pmtiles'))

        if pmtiles_files:
            print(f"Checking {len(pmtiles_files)} PMTiles files...\n")

            valid_count = 0
            invalid_count = 0

            for pmtiles_file in sorted(pmtiles_files)[:5]:  # 最初の5個をチェック
                is_valid, message = validate_pmtiles_pixels(str(pmtiles_file))
                relpath = pmtiles_file.relative_to(pmtiles_dir)

                if is_valid:
                    print(f"✅ {relpath}: {message}")
                    valid_count += 1
                else:
                    print(f"❌ {relpath}: {message}")
                    invalid_count += 1

            print(f"\n Summary: {valid_count} valid, {invalid_count} invalid\n")

    # 2. 一時フォルダのWebPタイルをチェック
    print("=== Intermediate WebP Tiles ===\n")

    tmp_folders = list(Path('aggregation-store').glob('*/*-tmp'))
    if tmp_folders:
        for tmp_folder in tmp_folders[:3]:
            webp_status = check_webp_tiles(str(tmp_folder))
            if webp_status:
                print(f"{tmp_folder.name}:")
                print(f"  Total: {webp_status['total']} tiles")
                print(f"  Sampled: {webp_status['sampled']}")
                print(f"  Non-empty: {webp_status['non_empty']}")
                print(f"  Occupancy: {(webp_status['non_empty']/webp_status['sampled']*100):.1f}%\n")
    else:
        print("No intermediate WebP tiles found (processing may be starting)\n")


if __name__ == '__main__':
    main()
