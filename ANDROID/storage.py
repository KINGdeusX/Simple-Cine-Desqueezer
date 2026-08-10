"""Folder access, abstracted over the two very different worlds this app runs in.

* On Android everything goes through the Storage Access Framework: the user
  grants a *tree* URI, and files are read and written through ContentResolver.
  No storage permission is declared or requested -- SAF is the grant.
* On a desktop (used for development and for the host-side tests) the same
  interface is backed by ordinary filesystem paths.

Both backends expose the identical small surface, so ``engine.py`` never has to
know which one it is talking to.
"""

from __future__ import annotations

import os

import formats

DEFAULT_MIME = 'image/x-adobe-dng'
DIR_MIME = 'vnd.android.document/directory'

MIME_BY_EXTENSION = {
    '.dng': 'image/x-adobe-dng',
    '.arw': 'image/x-sony-arw',
    '.sr2': 'image/x-sony-sr2',
    '.srf': 'image/x-sony-srf',
    '.nef': 'image/x-nikon-nef',
    '.nrw': 'image/x-nikon-nrw',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
}


class StorageError(Exception):
    """A folder could not be listed, read from or written to."""


def mime_for(name):
    return MIME_BY_EXTENSION.get(formats.extension_of(name), DEFAULT_MIME)


# --- desktop / test backend -------------------------------------------------

class LocalFolder:
    """Plain filesystem folder."""

    scheme = 'file'

    def __init__(self, path):
        self.path = os.path.abspath(path)

    # -- identity
    @property
    def key(self):
        return self.path

    @property
    def label(self):
        return self.path

    @property
    def name(self):
        return os.path.basename(self.path.rstrip(os.sep)) or self.path

    def exists(self):
        return os.path.isdir(self.path)

    # -- reading
    def list_images(self):
        try:
            names = os.listdir(self.path)
        except OSError as exc:
            raise StorageError(str(exc))
        return sorted((name, os.path.join(self.path, name))
                      for name in names if formats.is_supported(name))

    def read(self, handle):
        with open(handle, 'rb') as stream:
            return stream.read()

    # -- writing
    def ensure_child_folder(self, name):
        path = os.path.join(self.path, name)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise StorageError(str(exc))
        return LocalFolder(path)

    def write(self, name, data):
        path = os.path.join(self.path, name)
        try:
            os.makedirs(self.path, exist_ok=True)
            with open(path, 'wb') as stream:
                stream.write(data)
        except OSError as exc:
            raise StorageError(str(exc))
        return name


# --- Android SAF backend ----------------------------------------------------

class _Jni:
    """Lazily-resolved JNI handles.

    Resolved on first use rather than at import time so that importing this
    module can never be what crashes the app on a non-Android host.
    """

    _cache = None

    @classmethod
    def get(cls):
        if cls._cache is None:
            from jnius import autoclass, cast  # noqa: F401  (imported for callers)
            from android import mActivity      # provided by p4a's android module

            cls._cache = {
                'autoclass': autoclass,
                'cast': cast,
                'activity': mActivity,
                'Uri': autoclass('android.net.Uri'),
                'DocumentsContract': autoclass('android.provider.DocumentsContract'),
                'Intent': autoclass('android.content.Intent'),
            }
        return cls._cache


def _resolver():
    return _Jni.get()['activity'].getContentResolver()


def read_uri(uri):
    """Read a content:// document into bytes.

    A POSIX fd lets CPython do the I/O directly, which is both faster and far
    less fiddly than marshalling byte[] buffers across JNI; a few providers only
    offer streams, so that path is kept as a fallback.
    """
    try:
        descriptor = _resolver().openFileDescriptor(uri, 'r')
        if descriptor is not None:
            fd = descriptor.detachFd()
            with os.fdopen(fd, 'rb') as stream:
                return stream.read()
    except Exception:
        pass
    return _read_uri_via_stream(uri)


def _read_uri_via_stream(uri):
    stream = _resolver().openInputStream(uri)
    if stream is None:
        raise StorageError('cannot open file for reading')
    try:
        chunks = []
        buffer = bytearray(262144)
        while True:
            count = stream.read(buffer)
            if count <= 0:
                break
            chunks.append(bytes(buffer[:count]))
        return b''.join(chunks)
    finally:
        stream.close()


class SafFolder:
    """A folder inside a persisted SAF tree grant."""

    scheme = 'saf'

    COLUMN_ID = 'document_id'
    COLUMN_NAME = '_display_name'
    COLUMN_MIME = 'mime_type'

    def __init__(self, tree_uri, document_id=None, label=None):
        self._index = None          # name -> (document_id, mime), built lazily
        jni = _Jni.get()
        self.tree_uri_string = tree_uri
        self._tree = jni['Uri'].parse(tree_uri)
        self._contract = jni['DocumentsContract']
        self.document_id = document_id or self._contract.getTreeDocumentId(self._tree)
        self._label = label

    # -- identity
    @property
    def key(self):
        return '%s\n%s' % (self.tree_uri_string, self.document_id)

    @property
    def label(self):
        if self._label:
            return self._label
        # document ids look like "primary:DCIM/clip01" -- show the readable tail
        text = self.document_id
        if ':' in text:
            text = text.split(':', 1)[1]
        return '/' + text if text else self.document_id

    @property
    def name(self):
        return self.label.rstrip('/').rsplit('/', 1)[-1] or 'storage'

    @classmethod
    def from_key(cls, key):
        tree_uri, _, document_id = key.partition('\n')
        return cls(tree_uri, document_id or None)

    def _document_uri(self, document_id=None):
        return self._contract.buildDocumentUriUsingTree(
            self._tree, document_id or self.document_id)

    def exists(self):
        try:
            cursor = _resolver().query(self._document_uri(),
                                       [self.COLUMN_ID], None, None, None)
        except Exception:
            return False
        if cursor is None:
            return False
        try:
            return cursor.getCount() > 0
        finally:
            cursor.close()

    # -- reading
    def _children(self):
        """Yield ``(display_name, document_id, mime_type)`` for every child."""
        children_uri = self._contract.buildChildDocumentsUriUsingTree(
            self._tree, self.document_id)
        try:
            cursor = _resolver().query(
                children_uri,
                [self.COLUMN_ID, self.COLUMN_NAME, self.COLUMN_MIME],
                None, None, None)
        except Exception as exc:
            raise StorageError('cannot list folder: %s' % exc)
        if cursor is None:
            raise StorageError('cannot list folder (no cursor)')
        try:
            while cursor.moveToNext():
                yield (cursor.getString(1), cursor.getString(0), cursor.getString(2))
        finally:
            cursor.close()

    def list_images(self):
        found = [(name, doc_id) for name, doc_id, mime in self._children()
                 if mime != DIR_MIME and formats.is_supported(name or '')]
        found.sort()
        return found

    def read(self, handle):
        return read_uri(self._document_uri(handle))

    # -- writing
    def _child_index(self, refresh=False):
        """Cached ``name -> (document_id, mime)`` map of this folder.

        Every SAF query is a Binder round trip, so looking a name up by
        re-listing the whole folder once per written file turns a 300-frame
        batch into 300 full directory listings.  The map is built once and kept
        current as we create files.
        """
        if self._index is None or refresh:
            self._index = {name: (document_id, mime)
                           for name, document_id, mime in self._children()}
        return self._index

    def _find_child(self, name):
        return self._child_index().get(name, (None, None))

    def ensure_child_folder(self, name):
        document_id, mime = self._find_child(name)
        if document_id is not None:
            if mime != DIR_MIME:
                raise StorageError('"%s" already exists and is not a folder' % name)
            return SafFolder(self.tree_uri_string, document_id)
        uri = self._contract.createDocument(_resolver(), self._document_uri(),
                                            DIR_MIME, name)
        if uri is None:
            raise StorageError('could not create folder "%s"' % name)
        child_id = self._contract.getDocumentId(uri)
        self._child_index()[name] = (child_id, DIR_MIME)
        return SafFolder(self.tree_uri_string, child_id)

    def write(self, name, data):
        document_id, mime = self._find_child(name)
        if document_id is not None and mime != DIR_MIME:
            uri = self._document_uri(document_id)      # overwrite in place
            written_name = name
        else:
            uri = self._contract.createDocument(_resolver(), self._document_uri(),
                                                mime_for(name), name)
            if uri is None:
                raise StorageError('could not create "%s"' % name)
            new_id = self._contract.getDocumentId(uri)
            written_name = new_id.rsplit('/', 1)[-1]
            self._child_index()[written_name] = (new_id, mime_for(name))

        descriptor = None
        try:
            descriptor = _resolver().openFileDescriptor(uri, 'wt')
        except Exception:
            descriptor = None
        if descriptor is not None:
            fd = descriptor.detachFd()
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            return written_name

        stream = _resolver().openOutputStream(uri, 'wt')
        if stream is None:
            raise StorageError('cannot open "%s" for writing' % name)
        try:
            stream.write(data)
            stream.flush()
        finally:
            stream.close()
        return written_name


def folder_from_key(key):
    """Rebuild a folder from the string stored in the app's state file."""
    if not key:
        return None
    if key.startswith('content://'):
        return SafFolder.from_key(key)
    return LocalFolder(key)


# --- selections of individual files -----------------------------------------

class LocalFileSelection:
    """A fixed list of filesystem paths, used on the desktop and in tests."""

    scheme = 'files'

    def __init__(self, paths):
        self.paths = [os.path.abspath(path) for path in paths]

    @property
    def key(self):
        return '\n'.join(self.paths)

    @property
    def label(self):
        return describe_selection(len(self.paths),
                                  os.path.basename(self.paths[0]) if self.paths else '')

    def exists(self):
        return any(os.path.isfile(path) for path in self.paths)

    def list_images(self):
        return [(os.path.basename(path), path) for path in self.paths
                if formats.is_supported(path)]

    def read(self, handle):
        with open(handle, 'rb') as stream:
            return stream.read()


class SafFileSelection:
    """The documents a user picked with ACTION_OPEN_DOCUMENT.

    Unlike a tree grant this is not persisted between launches: Android caps how
    many URI permissions an app may hold, and a fresh pick each run is both the
    expected flow and cheaper than hoarding grants for hundreds of frames.
    """

    scheme = 'saf-files'

    COLUMN_NAME = '_display_name'

    def __init__(self, uris_and_names):
        self.items = list(uris_and_names)       # [(uri_string, display_name)]

    @property
    def key(self):
        return ''       # deliberately not persisted

    @property
    def label(self):
        first = self.items[0][1] if self.items else ''
        return describe_selection(len(self.items), first)

    def exists(self):
        return bool(self.items)

    def list_images(self):
        return [(name, uri) for uri, name in self.items
                if formats.is_supported(name or '')]

    def read(self, handle):
        return read_uri(_Jni.get()['Uri'].parse(handle))


def describe_selection(count, first_name):
    if count == 0:
        return 'No files selected'
    if count == 1:
        return first_name or '1 file selected'
    return '%d files selected' % count
