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

# Anything we cannot name precisely is created as a generic binary.  This must
# never be a real format: SAF providers append the extension implied by the MIME
# type they are handed, so a wrong guess turns "clip.mp4" into "clip.mp4.dng".
DEFAULT_MIME = 'application/octet-stream'
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
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.mov': 'video/quicktime',
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
    def list_images(self, media=None):
        try:
            names = os.listdir(self.path)
        except OSError as exc:
            raise StorageError(str(exc))
        return sorted((name, os.path.join(self.path, name))
                      for name in names if _wanted(name, media))

    def read(self, handle):
        with open(handle, 'rb') as stream:
            return stream.read()

    def open_read(self, handle):
        try:
            return open(handle, 'rb')
        except OSError as exc:
            raise StorageError(str(exc))

    def size_of(self, handle):
        try:
            return os.path.getsize(handle)
        except OSError:
            return None

    # -- writing
    def ensure_child_folder(self, name):
        path = os.path.join(self.path, name)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise StorageError(str(exc))
        return LocalFolder(path)

    def open_write(self, name):
        """Return ``(stream, written_name)``; the caller closes the stream."""
        path = os.path.join(self.path, name)
        try:
            os.makedirs(self.path, exist_ok=True)
            return open(path, 'wb'), name
        except OSError as exc:
            raise StorageError(str(exc))

    def write(self, name, data):
        stream, written = self.open_write(name)
        try:
            stream.write(data)
        finally:
            stream.close()
        return written

    def delete(self, name):
        """Remove a file we created but could not finish writing."""
        try:
            os.remove(os.path.join(self.path, name))
            return True
        except OSError:
            return False

    def path_for_write(self, name):
        """A real filesystem path, for APIs that cannot take a stream."""
        try:
            os.makedirs(self.path, exist_ok=True)
        except OSError as exc:
            raise StorageError(str(exc))
        return os.path.join(self.path, name)


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


def open_uri_read(uri):
    """A seekable Python file object over a content:// document.

    Video files run to gigabytes, so they are never loaded whole; everything
    that touches one works through a stream.  A detached POSIX fd gives a real
    Python file object, which is both faster than marshalling byte[] across JNI
    and seekable -- and the MP4 writer needs to seek.
    """
    try:
        descriptor = _resolver().openFileDescriptor(uri, 'r')
        if descriptor is not None:
            return os.fdopen(descriptor.detachFd(), 'rb')
    except Exception as exc:
        raise StorageError('cannot open file for reading (%s)' % exc)
    raise StorageError('cannot open file for reading')


def _open_uri_write(uri, name):
    try:
        descriptor = _resolver().openFileDescriptor(uri, 'wt')
        if descriptor is not None:
            return os.fdopen(descriptor.detachFd(), 'wb')
    except Exception:
        pass        # a few providers only offer streams
    stream = _resolver().openOutputStream(uri, 'wt')
    if stream is None:
        raise StorageError('cannot open "%s" for writing' % name)
    return _JavaOutputStream(stream)


def size_of_uri(uri):
    try:
        descriptor = _resolver().openFileDescriptor(uri, 'r')
        if descriptor is None:
            return None
        try:
            return int(descriptor.getStatSize())
        finally:
            descriptor.close()
    except Exception:
        return None


class _JavaOutputStream:
    """Minimal file-like wrapper over a java.io.OutputStream."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        self._stream.write(bytes(data) if not isinstance(data, bytes) else data)
        return len(data)

    def flush(self):
        self._stream.flush()

    def close(self):
        try:
            self._stream.flush()
        finally:
            self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _wanted(name, media):
    """Extension filter shared by every listing."""
    if media in ('photo', 'video'):
        return formats.accepts(name, media)
    return formats.is_supported(name)


def copy_stream(source, destination, total=None, on_progress=None,
                should_cancel=None, chunk=1024 * 1024):
    """Copy one stream into another, reporting progress as it goes.

    Returns the number of bytes copied, or ``None`` if cancelled partway.
    """
    copied = 0
    while True:
        if should_cancel is not None and should_cancel():
            return None
        block = source.read(chunk)
        if not block:
            break
        destination.write(block)
        copied += len(block)
        if on_progress is not None and total:
            on_progress(min(99, int(copied * 100 / total)))
    return copied


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

    def list_images(self, media=None):
        found = [(name, doc_id) for name, doc_id, mime in self._children()
                 if mime != DIR_MIME and _wanted(name or '', media)]
        found.sort()
        return found

    def read(self, handle):
        return read_uri(self._document_uri(handle))

    def open_read(self, handle):
        return open_uri_read(self._document_uri(handle))

    def size_of(self, handle):
        return size_of_uri(self._document_uri(handle))

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

    def _target_uri(self, name):
        """The document to write ``name`` into, creating it if necessary.

        Returns ``(uri, written_name)``.  The name can differ from the one asked
        for: if the provider refuses to overwrite it appends " (1)" and similar,
        and the caller should report whatever actually got written.
        """
        document_id, mime = self._find_child(name)
        if document_id is not None and mime != DIR_MIME:
            return self._document_uri(document_id), name      # overwrite in place

        uri = self._contract.createDocument(_resolver(), self._document_uri(),
                                            mime_for(name), name)
        if uri is None:
            raise StorageError('could not create "%s"' % name)
        new_id = self._contract.getDocumentId(uri)
        written_name = new_id.rsplit('/', 1)[-1]
        self._child_index()[written_name] = (new_id, mime_for(name))
        return uri, written_name

    def open_write(self, name):
        """Return ``(stream, written_name)``; the caller closes the stream."""
        uri, written_name = self._target_uri(name)
        return _open_uri_write(uri, name), written_name

    def write(self, name, data):
        stream, written_name = self.open_write(name)
        try:
            stream.write(data)
        finally:
            stream.close()
        return written_name

    def delete(self, name):
        """Remove a file we created but could not finish writing."""
        document_id, mime = self._find_child(name)
        if document_id is None or mime == DIR_MIME:
            return False
        try:
            removed = self._contract.deleteDocument(_resolver(),
                                                    self._document_uri(document_id))
        except Exception:
            return False
        if removed:
            self._child_index().pop(name, None)
        return bool(removed)

    def descriptor_for_write(self, name):
        """A seekable ParcelFileDescriptor, for MediaMuxer.

        Returns ``(descriptor, written_name)``.  MediaMuxer seeks backwards to
        patch the header once it knows the final durations, so the descriptor
        has to be opened read-write ('rwt'), not write-only.
        """
        uri, written_name = self._target_uri(name)
        try:
            descriptor = _resolver().openFileDescriptor(uri, 'rwt')
        except Exception as exc:
            raise StorageError('cannot open "%s" for writing (%s)' % (name, exc))
        if descriptor is None:
            raise StorageError('cannot open "%s" for writing' % name)
        return descriptor, written_name


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

    def list_images(self, media=None):
        return [(os.path.basename(path), path) for path in self.paths
                if _wanted(path, media)]

    def read(self, handle):
        with open(handle, 'rb') as stream:
            return stream.read()

    def open_read(self, handle):
        try:
            return open(handle, 'rb')
        except OSError as exc:
            raise StorageError(str(exc))

    def size_of(self, handle):
        try:
            return os.path.getsize(handle)
        except OSError:
            return None

    def path_for_read(self, handle):
        return handle


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

    def list_images(self, media=None):
        return [(name, uri) for uri, name in self.items
                if _wanted(name or '', media)]

    def read(self, handle):
        return read_uri(_Jni.get()['Uri'].parse(handle))

    def open_read(self, handle):
        return open_uri_read(_Jni.get()['Uri'].parse(handle))

    def size_of(self, handle):
        return size_of_uri(_Jni.get()['Uri'].parse(handle))

    def descriptor_for_read(self, handle):
        """A ParcelFileDescriptor, for MediaExtractor."""
        uri = _Jni.get()['Uri'].parse(handle)
        try:
            descriptor = _resolver().openFileDescriptor(uri, 'r')
        except Exception as exc:
            raise StorageError('cannot open source for reading (%s)' % exc)
        if descriptor is None:
            raise StorageError('cannot open source for reading')
        return descriptor


def describe_selection(count, first_name):
    if count == 0:
        return 'No files selected'
    if count == 1:
        return first_name or '1 file selected'
    return '%d files selected' % count
