from datetime import UTC, datetime, timedelta

from app.proxy.dvr import MAX_VOLUME, PARENT_RESERVE, DvrManager, VolumeProof


def fixture_manager(tmp_path, offsets):
    now = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)
    manager = DvrManager(tmp_path, enabled=True, now=lambda: now,
                         verify=lambda _: VolumeProof(MAX_VOLUME, MAX_VOLUME, PARENT_RESERVE * 2))
    directory = manager.reserve('fixture', 'fixture')
    lines = ['#EXTM3U']
    for i, offset in enumerate(offsets):
        name = f'seg_{i:05d}.ts'
        (directory / name).write_bytes(b'fixture')
        start = now + timedelta(seconds=offset)
        lines += [f'#EXT-X-PROGRAM-DATE-TIME:{start.isoformat()}', '#EXTINF:2,', name]
    (directory / 'index.m3u8').write_text('\n'.join(lines) + '\n')
    return manager, now


def test_full_hour_seek_between_sealed_segment_boundaries(tmp_path):
    manager, now = fixture_manager(tmp_path, range(-3661, -1, 2))
    window = manager.window('fixture')
    assert window['buffered_seconds'] >= 3600
    assert 'TIME-OFFSET=1.000' in manager.manifest('fixture', now - timedelta(hours=1))


def test_future_burst_never_sets_cutoff_or_evicts_sealed_history(tmp_path):
    manager, now = fixture_manager(tmp_path, range(-3661, 501, 2))
    window = manager.window('fixture')
    assert window['buffered_seconds'] >= 3600
    assert window['end'] <= now
    assert len(manager.segments) <= 1862
    assert len(list(manager.directory.glob('seg_*.ts'))) <= 1862
    manager.manifest('fixture', window['end'] - timedelta(hours=1))


def test_stalled_input_uses_sealed_edge_without_inventing_recent_media(tmp_path):
    manager, now = fixture_manager(tmp_path, range(-4001, -301, 2))
    window = manager.window('fixture')
    assert window['buffered_seconds'] >= 3600
    assert window['end'] == now - timedelta(seconds=301)
    manager.manifest('fixture', window['end'] - timedelta(hours=1))


def test_quota_wins_over_hour_target(tmp_path):
    from app.proxy.dvr import HEADROOM
    manager, _ = fixture_manager(tmp_path, range(-3661, -1, 2))
    manager.verify = lambda _: VolumeProof(HEADROOM + 70, MAX_VOLUME, PARENT_RESERVE * 2)
    assert manager.window('fixture')['buffered_seconds'] < 3600
    assert sum(p.stat().st_size for p in manager.directory.glob('seg_*.ts')) <= 70
