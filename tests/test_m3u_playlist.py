"""M3U8 playlist reading and writing."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.models.playlist import PlaylistTrack
from app.services.m3u_playlist import M3uPlaylistError, read_m3u, write_m3u8


class M3uPlaylistTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory(prefix="pc-m3u-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.music = self.root / "music"
        self.music.mkdir()
        self.lists = self.root / "lists"
        self.lists.mkdir()
        self.songs = [self.music / "a.mp3", self.root / "b 곡.flac", self.music / "c.wav"]
        for song in self.songs:
            song.write_bytes(b"audio")

    def test_reads_relative_absolute_and_file_url_entries_in_order(self) -> None:
        a, b, c = self.songs
        playlist = self.lists / "mix.m3u8"
        playlist.write_text(
            "﻿#EXTM3U\n#EXTINF:10,Artist - A\n../music/a.mp3\n\n"
            f"{b}\r\n{c.as_uri()}\n"
            "https://radio.example/stream.mp3\n../music/missing.mp3\n../music/a.mp3.txt\n",
            encoding="utf-8",
        )
        entries = read_m3u(playlist)
        self.assertEqual(entries.audio_paths, [a.resolve(), b.resolve(), c.resolve()])
        self.assertEqual(
            entries.skipped,
            ["https://radio.example/stream.mp3", "../music/missing.mp3", "../music/a.mp3.txt"],
        )

    def test_legacy_m3u_in_the_system_code_page_is_read(self) -> None:
        playlist = self.lists / "old.m3u"
        playlist.write_bytes(b"\xff\xfe\n../music/c.wav\n")  # not valid UTF-8
        self.assertEqual(read_m3u(playlist).audio_paths, [self.songs[2].resolve()])

    def test_missing_playlist_file_raises(self) -> None:
        with self.assertRaises(M3uPlaylistError):
            read_m3u(self.lists / "nope.m3u8")

    def test_writes_extended_m3u8_with_relative_paths_that_read_back(self) -> None:
        a, b, _c = self.songs
        tracks = [
            PlaylistTrack(str(a), "Song A", "Artist", "Album", 61.4),
            PlaylistTrack(str(b), "Song B", "", duration_seconds=0.0),
        ]
        playlist = self.lists / "out.m3u8"
        self.assertEqual(write_m3u8(tracks, playlist), 2)
        lines = playlist.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, [
            "#EXTM3U",
            "#EXTINF:61,Artist - Song A",
            os.path.join("..", "music", "a.mp3"),
            "#EXTINF:-1,Song B",
            os.path.join("..", "b 곡.flac"),
        ])
        self.assertEqual(read_m3u(playlist).audio_paths, [a.resolve(), b.resolve()])


if __name__ == "__main__":
    unittest.main()
