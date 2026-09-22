"""Small monochrome symbols for native Qt actions throughout the editor."""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QProxyStyle, QStyle


def _render(shape: str) -> QIcon:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           '<g fill="none" stroke="#B7C2BF" stroke-width="1.6" '
           f'stroke-linecap="round" stroke-linejoin="round">{shape}</g></svg>')
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


_SOURCE_SHAPES = {
    "text": '<path d="M5 5h14 M12 5v14 M9 19h6"/>',
    "shape": '<rect x="3" y="10" width="10" height="10" rx="1"/><circle cx="16" cy="8" r="5"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m21 16-5-5-9 9"/>',
    "logo": '<path d="m12 3 2.6 5.3 5.9.9-4.3 4.2 1 5.9L12 16.5l-5.2 2.8 1-5.9-4.3-4.2 5.9-.9z"/>',
    "watermark": '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
    "video": '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
    "time": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    "progress_bar": '<rect x="3" y="10" width="18" height="4" rx="2"/><path d="M5 12h8"/>',
    "album_cover": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>',
    "background": '<path d="m12 3 9 5-9 5-9-5z M3 13l9 5 9-5"/>',
    "audio_visualizer": '<path d="M4 15v-3 M8 18V8 M12 20V4 M16 17V9 M20 14v-3"/>',
    "audio_waveform": '<path d="M2 12h3l2-6 3 12 3-9 2 6 2-3h5"/>',
    "audio_level_meter": '<rect x="5" y="3" width="5" height="18" rx="1"/><rect x="14" y="9" width="5" height="12" rx="1"/>',
    "lyrics": '<path d="M4 5h16v11H9l-5 4z M8 9h8 M8 12h5"/>',
    "track_list": '<path d="M9 5h12 M9 12h12 M9 19h12 M3 5h1 M3 12h1 M3 19h1"/>',
    "now_playing": '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "particle_overlay": '<path d="M12 3v4 M12 17v4 M3 12h4 M17 12h4 M6 6l2 2 M16 16l2 2 M18 6l-2 2 M8 16l-2 2"/>',
}


def source_icon(source_type: str) -> QIcon | None:
    """A glyph depicting one Canvas source type (``SourceType.value``), or None if unknown."""
    shape = _SOURCE_SHAPES.get(source_type)
    return _render(shape) if shape is not None else None


class StudioIconStyle(QProxyStyle):
    def standardIcon(self, standard_icon, option=None, widget=None):
        sp = QStyle.StandardPixmap
        paths = {
            sp.SP_FileIcon: '<path d="M6 3h8l4 4v14H6z M14 3v5h4"/>',
            sp.SP_DialogOpenButton: '<path d="M3 8V5h6l2 3h10l-3 12H3z M3 11h17"/>',
            sp.SP_DirIcon: '<path d="M3 6h6l2 3h10v11H3z"/>',
            sp.SP_DialogSaveButton: '<path d="M4 3h13l3 3v15H4z M8 3v6h8V3 M8 21v-8h8v8"/>',
            sp.SP_ArrowBack: '<path d="m9 5-6 6 6 6 M3 11h11a6 6 0 0 1 6 6"/>',
            sp.SP_ArrowForward: '<path d="m15 5 6 6-6 6 M21 11h-11a6 6 0 0 0-6 6"/>',
            sp.SP_MediaPlay: '<path d="m8 4 12 8-12 8z"/>',
            sp.SP_MediaPause: '<path d="M8 4v16 M16 4v16"/>',
            sp.SP_MediaStop: '<rect x="5" y="5" width="14" height="14" rx="1"/>',
            sp.SP_MediaSkipForward: '<path d="m4 5 12 7-12 7z M20 5v14"/>',
            sp.SP_MediaSkipBackward: '<path d="m20 5-12 7 12 7z M4 5v14"/>',
            sp.SP_TrashIcon: '<path d="M3 6h18 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7"/>',
            sp.SP_FileDialogContentsView: '<path d="M4 5h16 M4 12h16 M4 19h16 M9 3v4 M15 10v4 M8 17v4"/>',
            sp.SP_FileDialogListView: '<path d="M9 5h12 M9 12h12 M9 19h12 M3 5h1 M3 12h1 M3 19h1"/>',
            sp.SP_FileDialogDetailedView: '<path d="M3 5h18v15H3z M3 10h18 M9 5v15"/>',
            sp.SP_FileDialogInfoView: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6 M12 7v1"/>',
            sp.SP_DialogCloseButton: '<path d="m6 6 12 12 M18 6 6 18"/>',
            sp.SP_DesktopIcon: '<rect x="4" y="4" width="16" height="16" rx="2"/>',
            sp.SP_ComputerIcon: '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M12 16v4 M8 20h8"/>',
            sp.SP_DialogResetButton: '<path d="M4 10a8 8 0 1 1 1 8 M4 4v6h6"/>',
            sp.SP_BrowserReload: '<path d="M4 10a8 8 0 1 1 1 8 M4 4v6h6"/>',
            sp.SP_DialogApplyButton: '<path d="m4 12 5 5L20 6"/>',
            sp.SP_ArrowDown: '<path d="M12 4v16 m-6-6 6 6 6-6"/>',
            sp.SP_MediaSeekForward: '<path d="m3 6 8 6-8 6z m9 0 8 6-8 6z"/>',
            sp.SP_MediaSeekBackward: '<path d="m21 6-8 6 8 6z m-9 0-8 6 8 6z"/>',
            sp.SP_MediaVolume: '<path d="m11 4-6 5H2v6h3l6 5z M15 8a6 6 0 0 1 0 8 M18 5a10 10 0 0 1 0 14"/>',
            sp.SP_MediaVolumeMuted: '<path d="m11 4-6 5H2v6h3l6 5z m5 5 6 6 m0-6-6 6"/>',
            sp.SP_TitleBarMenuButton: '<path d="M4 6h16 M4 12h16 M4 18h16"/>',
            sp.SP_FileDialogNewFolder: '<path d="M3 6h6l2 3h10v11H3z M12 12v5 M9.5 14.5h5"/>',
            sp.SP_FileDialogBack: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-5-5"/>',
        }
        shape = paths.get(standard_icon)
        if shape is None:
            return super().standardIcon(standard_icon, option, widget)
        return _render(shape)
