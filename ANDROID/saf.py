"""Storage Access Framework pickers, via pyjnius.

Kivy has no SAF integration, so the intents are fired and their results
collected by hand.  Two pickers are needed:

* ``pick_files()`` -- ACTION_OPEN_DOCUMENT with multi-select, for the images to
  process.  These grants are per-run and not persisted: Android caps how many
  URI permissions an app may hold, and picking files each run is the flow.
* ``pick_folder()`` -- ACTION_OPEN_DOCUMENT_TREE for the output folder, made
  persistable so the destination survives a force-close and only has to be
  chosen once.

Every entry point is safe to call on a desktop: it simply reports that SAF is
unavailable instead of raising.
"""

from __future__ import annotations

import os

IS_ANDROID = os.environ.get('ANDROID_ARGUMENT') is not None

REQUEST_SOURCE = 0xDE01
REQUEST_DEST = 0xDE02

_bound = False
_pending = {}


class SafUnavailable(Exception):
    pass


def _android():
    if not IS_ANDROID:
        raise SafUnavailable('not running on Android')
    from jnius import autoclass
    from android import activity, mActivity
    return autoclass, activity, mActivity


def _on_activity_result(request_code, result_code, intent):
    callback = _pending.pop(request_code, None)
    if callback is None:
        return
    try:
        autoclass, _activity, _mActivity = _android()
        Activity = autoclass('android.app.Activity')

        if result_code != Activity.RESULT_OK or intent is None:
            callback(None, None)          # user backed out -- not an error
            return

        if request_code == REQUEST_SOURCE:
            _deliver_files(intent, callback)
        else:
            _deliver_folder(intent, callback)
    except Exception as exc:
        callback(None, str(exc))


def _deliver_folder(intent, callback):
    autoclass, _activity, mActivity = _android()
    Intent = autoclass('android.content.Intent')

    uri = intent.getData()
    if uri is None:
        callback(None, 'no folder returned')
        return

    take_flags = intent.getFlags() & (Intent.FLAG_GRANT_READ_URI_PERMISSION |
                                      Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
    if not take_flags:
        take_flags = (Intent.FLAG_GRANT_READ_URI_PERMISSION |
                      Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
    warning = None
    try:
        mActivity.getContentResolver().takePersistableUriPermission(uri, take_flags)
    except Exception as exc:
        # Not fatal: the folder still works for this session.
        warning = 'output folder access could not be made permanent (%s)' % exc

    from storage import SafFolder
    callback(SafFolder(uri.toString()), warning)


def _deliver_files(intent, callback):
    """Collect every picked document, whether one or many."""
    uris = []
    clip = intent.getClipData()
    if clip is not None:
        for index in range(clip.getItemCount()):
            item = clip.getItemAt(index)
            if item is not None and item.getUri() is not None:
                uris.append(item.getUri())
    elif intent.getData() is not None:
        uris.append(intent.getData())

    if not uris:
        callback(None, 'no files returned')
        return

    items = [(uri.toString(), _display_name(uri)) for uri in uris]

    from storage import SafFileSelection
    selection = SafFileSelection(items)

    import formats
    unsupported = [name for _uri, name in items if not formats.is_supported(name)]
    warning = None
    if unsupported:
        warning = '%d selected file(s) are not a supported format and will be ' \
                  'skipped' % len(unsupported)
    callback(selection, warning)


def _display_name(uri):
    """The document's filename, which is also what the output file is called."""
    autoclass, _activity, mActivity = _android()
    OpenableColumns = autoclass('android.provider.OpenableColumns')
    try:
        cursor = mActivity.getContentResolver().query(
            uri, [OpenableColumns.DISPLAY_NAME], None, None, None)
    except Exception:
        cursor = None
    if cursor is not None:
        try:
            if cursor.moveToFirst():
                name = cursor.getString(0)
                if name:
                    return name
        finally:
            cursor.close()
    # Fall back to the last path segment, which is usually "<id>/<name>".
    tail = uri.toString().rsplit('/', 1)[-1]
    from urllib.parse import unquote
    return unquote(tail)


def _start(intent, request_code, callback):
    autoclass, activity, mActivity = _android()
    global _bound
    if not _bound:
        activity.bind(on_activity_result=_on_activity_result)
        _bound = True
    _pending[request_code] = callback
    mActivity.startActivityForResult(intent, request_code)


def pick_files(callback):
    """Open the system file picker with multi-select enabled.

    ``callback(selection_or_None, warning_or_None)`` runs on the Android UI
    callback thread; the caller is expected to hop back to Kivy's main thread.
    """
    autoclass, _activity, _mActivity = _android()
    import formats

    Intent = autoclass('android.content.Intent')
    intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    intent.setType('*/*')
    intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)
    try:
        intent.putExtra(Intent.EXTRA_MIME_TYPES, formats.PICKER_MIME_TYPES)
    except Exception:
        pass    # an unfiltered picker is better than no picker
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)

    _start(intent, REQUEST_SOURCE, callback)


def pick_folder(callback):
    """Open the system folder picker for the output destination."""
    autoclass, _activity, _mActivity = _android()

    Intent = autoclass('android.content.Intent')
    intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION |
                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION |
                    Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)

    _start(intent, REQUEST_DEST, callback)


def has_persisted_access(tree_uri):
    """True if a previously granted tree URI is still ours to use."""
    if not IS_ANDROID or not tree_uri:
        return False
    try:
        _autoclass, _activity, mActivity = _android()
        permissions = mActivity.getContentResolver().getPersistedUriPermissions()
        for index in range(permissions.size()):
            permission = permissions.get(index)
            if permission.getUri().toString() == tree_uri:
                return permission.isReadPermission()
    except Exception:
        return False
    return False
