#!/usr/bin/env python3
"""
YTMusic Track Enricher

Enriches MP3 files downloaded by Synthwave-master with track numbers
by querying YouTube Music.

Usage:
    python enricher.py /path/to/music/folder
    python enricher.py /path/to/music/folder --dry-run
    python enricher.py --daemon  # runs continuously with SCAN_INTERVAL

Environment variables:
    MUSIC_FOLDER: Path to music folder (default: /music)
    SCAN_INTERVAL: Seconds between scans in daemon mode (default: 3600 = 1 hour)
"""

import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, TRCK, ID3NoHeaderError
from ytmusicapi import YTMusic


def extract_video_id(filename: str) -> str | None:
    """
    Extract YouTube video ID from Synthwave filename format.
    Format: "Artist - Title - VIDEO_ID.mp3"
    Video IDs are 11 characters: alphanumeric plus - and _
    """
    match = re.search(r'-\s*([a-zA-Z0-9_-]{11})\.mp3$', filename)
    if match:
        return match.group(1)
    return None


def normalize_title(title: str) -> str:
    """Normalize a title for comparison by removing punctuation and lowercasing."""
    title = re.sub(r'\s*[\(\[].*?[\)\]]', '', title)
    title = re.sub(r'[^\w\s]', '', title.lower())
    return ' '.join(title.split())


def get_track_metadata(ytmusic: YTMusic, video_id: str) -> dict | None:
    """
    Query YouTube Music API for track number.
    Returns dict with 'track_number' and 'total_tracks' keys.
    """
    try:
        result = {
            'track_number': None,
            'total_tracks': None,
        }

        watch_data = ytmusic.get_watch_playlist(video_id)
        if not watch_data or not watch_data.get('tracks'):
            return None

        track_info = watch_data['tracks'][0]
        title = track_info.get('title', '')
        normalized_title = normalize_title(title)

        album = track_info.get('album', {})
        album_id = album.get('id') if album else None

        if album_id:
            try:
                album_data = ytmusic.get_album(album_id)
                if album_data:
                    tracks = album_data.get('tracks', [])
                    result['total_tracks'] = len(tracks)

                    for idx, track in enumerate(tracks, start=1):
                        if track.get('videoId') == video_id:
                            result['track_number'] = idx
                            break

                    if result['track_number'] is None:
                        for idx, track in enumerate(tracks, start=1):
                            track_title = normalize_title(track.get('title', ''))
                            if track_title == normalized_title:
                                result['track_number'] = idx
                                break
                            if normalized_title in track_title or track_title in normalized_title:
                                result['track_number'] = idx
                                break
            except Exception:
                pass

        return result

    except Exception as e:
        print(f"  Error fetching metadata: {e}")
        return None


def write_tags(filepath: str, track_number: int | None, total_tracks: int | None) -> bool:
    """Write track number to MP3 file's ID3 tags."""
    try:
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            tags = ID3()

        if track_number is None:
            return False

        if total_tracks:
            track_str = f"{track_number}/{total_tracks}"
        else:
            track_str = str(track_number)
        tags['TRCK'] = TRCK(encoding=3, text=track_str)
        tags.save(filepath)
        return True

    except Exception as e:
        print(f"  Error writing tags: {e}")
        return False


def get_existing_track_number(filepath: str) -> str | None:
    """Get existing track number from file."""
    try:
        tags = ID3(filepath)
        trck = tags.get('TRCK')
        if trck:
            return str(trck)
    except ID3NoHeaderError:
        pass
    except Exception:
        pass
    return None


def scan_folder(folder_path: str, dry_run: bool = False) -> dict:
    """
    Scan folder recursively for MP3 files missing track numbers and enrich them.
    Only calls API for files that need enrichment.
    """
    stats = {
        'scanned': 0,
        'needs_enrichment': 0,
        'enriched': 0,
        'failed': 0,
        'no_video_id': 0,
    }

    folder = Path(folder_path)
    if not folder.exists():
        print(f"Error: Folder not found: {folder_path}")
        return stats

    # Recursive glob
    mp3_files = list(folder.rglob('*.mp3'))
    if not mp3_files:
        print(f"No MP3 files found in {folder_path}")
        return stats

    # First pass: find files needing enrichment (no API calls)
    files_to_enrich = []
    for mp3_file in mp3_files:
        stats['scanned'] += 1
        existing_track = get_existing_track_number(str(mp3_file))
        if existing_track:
            continue

        video_id = extract_video_id(mp3_file.name)
        if not video_id:
            stats['no_video_id'] += 1
            continue

        files_to_enrich.append((mp3_file, video_id))

    stats['needs_enrichment'] = len(files_to_enrich)

    if not files_to_enrich:
        print(f"Scanned {stats['scanned']} files, none need enrichment")
        return stats

    print(f"Scanned {stats['scanned']} files, {len(files_to_enrich)} need enrichment")
    print()

    # Second pass: enrich files that need it
    ytmusic = YTMusic()

    for i, (mp3_file, video_id) in enumerate(files_to_enrich, start=1):
        rel_path = mp3_file.relative_to(folder)
        print(f"[{i}/{len(files_to_enrich)}] {rel_path}")
        print(f"  Fetching metadata for video ID: {video_id}")

        metadata = get_track_metadata(ytmusic, video_id)

        if not metadata:
            print("  Failed: Could not fetch metadata from YouTube Music")
            stats['failed'] += 1
            continue

        track_num = metadata.get('track_number')
        total_tracks = metadata.get('total_tracks')

        if track_num is None:
            print("  Failed: No track number found in album")
            stats['failed'] += 1
            continue

        track_str = f"{track_num}/{total_tracks}" if total_tracks else str(track_num)
        print(f"  Found track number: {track_str}")

        if dry_run:
            print("  Dry run: Would write tags")
            stats['enriched'] += 1
        else:
            if write_tags(str(mp3_file), track_num, total_tracks):
                print("  Tags written successfully")
                stats['enriched'] += 1
            else:
                stats['failed'] += 1

    return stats


def print_stats(stats: dict):
    """Print scan statistics."""
    print()
    print("=" * 50)
    print("Summary:")
    print(f"  Files scanned:        {stats['scanned']}")
    print(f"  Needed enrichment:    {stats['needs_enrichment']}")
    print(f"  Successfully enriched: {stats['enriched']}")
    print(f"  Failed:               {stats['failed']}")
    if stats['no_video_id']:
        print(f"  No video ID:          {stats['no_video_id']}")
    print("=" * 50)


def run_daemon(folder: str, interval: int, dry_run: bool = False):
    """Run continuously, scanning at the specified interval."""
    print(f"Starting daemon mode")
    print(f"  Music folder: {folder}")
    print(f"  Scan interval: {interval} seconds ({interval // 60} minutes)")
    print()

    while True:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Starting scan...")

        stats = scan_folder(folder, dry_run=dry_run)
        print_stats(stats)

        print(f"\nNext scan in {interval} seconds...")
        print("-" * 50)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(
        description='Enrich MP3 files with track numbers from YouTube Music'
    )
    parser.add_argument(
        'folder',
        nargs='?',
        default=os.environ.get('MUSIC_FOLDER', '/music'),
        help='Path to folder containing MP3 files (default: /music or MUSIC_FOLDER env)'
    )
    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Show what would be done without making changes'
    )
    parser.add_argument(
        '--daemon', '-d',
        action='store_true',
        help='Run continuously, scanning at regular intervals'
    )
    parser.add_argument(
        '--interval', '-i',
        type=int,
        default=int(os.environ.get('SCAN_INTERVAL', 3600)),
        help='Seconds between scans in daemon mode (default: 3600 = 1 hour)'
    )

    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN MODE - No changes will be made")
        print()

    if args.daemon:
        run_daemon(args.folder, args.interval, dry_run=args.dry_run)
    else:
        stats = scan_folder(args.folder, dry_run=args.dry_run)
        print_stats(stats)


if __name__ == '__main__':
    main()
