#!/usr/bin/env python3
"""
ダウンサンプリング進捗監視スクリプト

定期的に実行して：
1. ログからファイル処理数をカウント
2. 処理速度を計算
3. 推定完了時間を更新
4. 画素検証を実行
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import sys

def parse_log():
    """downsampling.log からファイル処理情報を抽出"""
    log_file = Path('downsampling.log')

    if not log_file.exists():
        return None

    entries = []
    with open(log_file) as f:
        for line in f:
            # "downsampling {filename}. {timestamp}. {n} / {total}."
            match = re.search(
                r'downsampling (.+?)\. ([\d\-]+ [\d:\.]+)\. (\d+) / (\d+)\.',
                line
            )
            if match:
                filename, timestamp_str, current, total = match.groups()
                try:
                    # マイクロ秒を含むタイムスタンプをパース
                    timestamp_str = timestamp_str.strip()
                    # "2026-07-12 18:11:26.764193" 形式(マイクロ秒が0の場合は省略されうる)
                    if '.' in timestamp_str:
                        parts = timestamp_str.split('.')
                        timestamp_str = parts[0] + '.' + parts[1][:6]
                    timestamp = datetime.fromisoformat(timestamp_str)
                    entries.append({
                        'filename': filename,
                        'timestamp': timestamp,
                        'current': int(current),
                        'total': int(total)
                    })
                except Exception as e:
                    pass

    return entries if entries else None


def calculate_metrics(entries):
    """処理メトリクスを計算"""
    if not entries:
        return None

    first_entry = entries[0]
    last_entry = entries[-1]

    # タイムスタンプから経過時間を計算
    elapsed_time = (last_entry['timestamp'] - first_entry['timestamp']).total_seconds()
    files_processed = last_entry['current']
    total_files = last_entry['total']

    if files_processed == 0:
        return None

    # 1ファイルあたりの平均処理時間
    avg_time_per_file = elapsed_time / files_processed

    # 推定完了時間
    remaining_files = total_files - files_processed
    estimated_remaining_seconds = remaining_files * avg_time_per_file
    estimated_completion = last_entry['timestamp'] + timedelta(seconds=estimated_remaining_seconds)

    return {
        'elapsed_time': elapsed_time,
        'files_processed': files_processed,
        'total_files': total_files,
        'progress_percent': (files_processed / total_files) * 100,
        'avg_time_per_file': avg_time_per_file,
        'remaining_files': remaining_files,
        'estimated_remaining': estimated_remaining_seconds,
        'estimated_completion': estimated_completion,
        'last_timestamp': last_entry['timestamp']
    }


def format_duration(seconds):
    """秒を「d日 HH:MM:SS」形式でフォーマット"""
    if seconds < 0:
        return "N/A"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    days = hours // 24

    if days > 0:
        hours = hours % 24
        return f"{days}日 {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def print_progress_report(metrics):
    """進捗レポートを表示"""

    print("=" * 70)
    print("DOWNSAMPLING PROGRESS REPORT")
    print("=" * 70)
    print()

    print(f"📊 Processing Status")
    print(f"  Completed:  {metrics['files_processed']:4d} / {metrics['total_files']} files")
    print(f"  Progress:   {metrics['progress_percent']:6.2f}%")
    print()

    print(f"⏱️  Performance Metrics")
    print(f"  Elapsed:         {format_duration(metrics['elapsed_time'])}")
    print(f"  Avg per file:    {metrics['avg_time_per_file']:.2f} sec/CSV")
    print()

    print(f"⏳ Estimated Completion")
    print(f"  Remaining:   {metrics['remaining_files']} files")
    print(f"  Est. time:   {format_duration(metrics['estimated_remaining'])}")
    print(f"  Completion:  {metrics['estimated_completion'].strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    print("=" * 70)


def run_pixel_validation():
    """画素検証を実行"""
    try:
        result = subprocess.run(
            [sys.executable, 'validate_pixels.py'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.stdout:
            print("\n" + result.stdout)
    except Exception as e:
        print(f"Pixel validation skipped: {e}")


def main():
    """メイン監視ルーチン"""

    entries = parse_log()

    if not entries:
        print("❌ No log entries found. Processing may not have started yet.")
        return

    metrics = calculate_metrics(entries)

    if not metrics:
        print("⏳ Insufficient data for metrics calculation.")
        return

    # 進捗レポート表示
    print_progress_report(metrics)

    # 画素検証実行
    run_pixel_validation()


if __name__ == '__main__':
    main()
