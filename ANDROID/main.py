"""
Desqueeze -- Anamorphic DNG Processor (Android port of Simple Cine Desqueezer).

Startup rules this file follows on purpose (see Instruction.md, Phase 6):

* module-level imports are limited to Kivy itself -- the DNG engine, the SAF
  bridge and anything else heavy are imported inside the function that first
  needs them, so nothing delays the first frame;
* every step that can fail (state file, fonts, JNI, folder access) is wrapped,
  because on a phone an uncaught exception during startup is a crash dialog
  rather than a traceback someone can read;
* the batch runs on a plain thread and *never* touches a widget -- events are
  queued and drained by a Clock tick on the main thread, which is the Kivy
  equivalent of the desktop app's QThread + pyqtSignal split.
"""

from __future__ import annotations

import os
import threading
import traceback
from collections import deque

from kivy.config import Config

# Must be set before the window is created.  The mouse provider is a desktop
# convenience (it stops the red multitouch dots); adding an input provider that
# has no business existing on a phone is a needless startup risk.
if os.environ.get('ANDROID_ARGUMENT') is None:
    Config.set('input', 'mouse', 'mouse,multitouch_on_demand')
Config.set('kivy', 'exit_on_escape', '0')

from kivy.app import App                                          # noqa: E402
from kivy.base import ExceptionHandler, ExceptionManager          # noqa: E402
from kivy.clock import Clock, mainthread                          # noqa: E402
from kivy.core.text import LabelBase                              # noqa: E402
from kivy.core.window import Window                               # noqa: E402
from kivy.metrics import dp                                       # noqa: E402
from kivy.properties import (BooleanProperty, ListProperty,       # noqa: E402
                             NumericProperty, StringProperty)
from kivy.uix.boxlayout import BoxLayout                          # noqa: E402
from kivy.utils import platform                                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
IS_ANDROID = platform == 'android'

MAX_LOG_LINES = 600          # bounded so a huge batch cannot exhaust memory
LOG_FLUSH_SECONDS = 0.15     # batch redraws instead of one per file
WIDE_BREAKPOINT_DP = 720     # side-by-side above this width


class Root(BoxLayout):
    """Defined in desqueeze.kv."""


class _CrashGuard(ExceptionHandler):
    """Turn an unexpected UI exception into a log line instead of a crash."""

    def handle_exception(self, inst):
        try:
            app = App.get_running_app()
            if app is not None:
                app.log_lines_from_traceback(traceback.format_exc())
        except Exception:
            pass
        return ExceptionManager.PASS


class DesqueezeApp(App):
    title = 'Desqueeze'
    icon = os.path.join(HERE, 'icon.png')
    # Absolute path so App.load_kv() loads the rules exactly once.  Letting Kivy
    # auto-resolve it *and* calling Builder.load_file() ourselves would register
    # the <Root> rule twice and draw the whole screen twice over.
    kv_file = os.path.join(HERE, 'desqueeze.kv')

    # --- state surfaced to the .kv file
    source_text = StringProperty('No files selected')
    dest_text = StringProperty('Choose where results are written')
    dest_folder_name = StringProperty('desqueezed')
    engine_status = StringProperty('Starting engine...')
    engine_ready = BooleanProperty(False)
    preset_labels = ListProperty([])
    preset_label = StringProperty('')
    custom_x = StringProperty('1.33')
    custom_y = StringProperty('1.0')
    is_custom = BooleanProperty(False)
    is_running = BooleanProperty(False)
    progress_value = NumericProperty(0)
    wide = BooleanProperty(False)
    inset_top = NumericProperty(0)      # status bar, in pixels
    inset_bottom = NumericProperty(0)   # navigation / gesture bar, in pixels

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._source = None
        self._dest = None
        self._events = deque()          # producer: worker thread, consumer: Clock
        self._log = []
        self._engine = None
        self._worker = None
        self._state = None
        self._presets = []

    # --- startup ------------------------------------------------------------

    def build(self):
        from engine import CUSTOM_INDEX, LENS_PRESETS

        self._presets = LENS_PRESETS
        self.preset_labels = [label for label, _x, _y in LENS_PRESETS]
        self.preset_label = self.preset_labels[0]
        self._custom_index = CUSTOM_INDEX

        self._register_font()
        Window.clearcolor = (0.078, 0.078, 0.078, 1)
        Window.softinput_mode = 'below_target'
        Window.bind(size=self._on_window_size)
        self._apply_breakpoint(Window.width)

        ExceptionManager.add_handler(_CrashGuard())

        root = Root()
        Clock.schedule_interval(self._drain_events, LOG_FLUSH_SECONDS)
        return root

    def _register_font(self):
        """Bundle a monospace face -- Android has no Consolas/Courier New.

        Every widget in the .kv asks for 'desqueeze-mono', so this name has to
        end up registered no matter what: an unregistered font name makes each
        Label raise, which on a phone means a blank screen instead of an app.
        """
        from kivy.resources import resource_find

        bold = os.path.join(HERE, 'data', 'DejaVuSansMono-Bold.ttf')
        candidates = [os.path.join(HERE, 'data', 'DejaVuSansMono.ttf'),
                      resource_find('data/fonts/RobotoMono-Regular.ttf'),
                      resource_find('data/fonts/Roboto-Regular.ttf')]
        for path in candidates:
            if not path or not os.path.exists(path):
                continue
            try:
                LabelBase.register(name='desqueeze-mono', fn_regular=path,
                                   fn_bold=bold if os.path.exists(bold) else path)
                return
            except Exception:
                continue
        try:
            LabelBase.register(name='desqueeze-mono',
                               fn_regular=LabelBase.default_font)
        except Exception:
            pass

    def on_start(self):
        self._apply_system_bar_insets()
        try:
            self._restore_state()
        except Exception:
            self.log('!  could not restore previous session')
        self._announce_engine()

    def _announce_engine(self):
        try:
            from engine import build_engine
            self.engine_status = 'Desqueeze engine ready'
            self.engine_ready = True
            self.log('Desqueeze engine ready')
            self.log(build_engine().name)
        except Exception as exc:
            self.engine_status = 'Engine failed to load'
            self.engine_ready = False
            self.log('!  engine unavailable: %s' % exc)
        import formats
        self.log(formats.describe_support())
        if not IS_ANDROID:
            self.log('(desktop preview - folder pickers use plain paths)')

    def _apply_system_bar_insets(self):
        """Keep the UI out from under the status and navigation bars.

        Android 15+ draws apps edge to edge whether they ask for it or not, and
        Kivy has no notion of window insets, so without this the header sits
        under the clock and the log sits under the gesture bar.

        The heights come from the platform's dimension resources rather than
        from ``View.getRootWindowInsets()``: Kivy's main loop is not Android's
        UI thread, and reading resources is safe from any thread while touching
        a View from the wrong one is not.
        """
        if not IS_ANDROID:
            return
        try:
            from android import mActivity

            resources = mActivity.getResources()

            def bar(name):
                identifier = resources.getIdentifier(name, 'dimen', 'android')
                return resources.getDimensionPixelSize(identifier) if identifier else 0

            self.inset_top = bar('status_bar_height')
            self.inset_bottom = bar('navigation_bar_height')
        except Exception:
            pass    # cosmetic only -- never worth failing startup over

    # --- persistence --------------------------------------------------------

    def _state_dir(self):
        try:
            return self.user_data_dir
        except Exception:
            return HERE

    def _restore_state(self):
        from appstate import AppState

        self._state = AppState(self._state_dir()).load()
        state = self._state

        index = state['preset_index']
        if not 0 <= index < len(self._presets):
            index = 0
        self.preset_label = self.preset_labels[index]
        self.is_custom = index == self._custom_index
        self.custom_x = str(state.get('custom_x') or '1.33')
        self.custom_y = str(state.get('custom_y') or '1.0')
        self.dest_folder_name = state.get('dest_folder_name')
        if self.dest_folder_name is None:
            self.dest_folder_name = 'desqueezed'

        dest_key = state.get('dest_key') or ''
        if dest_key:
            folder = self._reopen(dest_key)
            if folder is not None:
                self._set_dest(folder, remember=False)
            else:
                self.log('i  previous output folder is no longer accessible')
        self._refresh_dest_text()

    def _reopen(self, key):
        """Rebuild a saved folder, checking the SAF grant still stands."""
        import saf
        import storage

        try:
            if key.startswith('content://'):
                if not saf.has_persisted_access(key.split('\n', 1)[0]):
                    return None
            folder = storage.folder_from_key(key)
            return folder if folder is not None and folder.exists() else None
        except Exception:
            return None

    def _save_state(self):
        if self._state is None:
            return
        try:
            self._state.update({
                # the file selection is per-run and deliberately not persisted
                'dest_key': self._dest.key if self._dest else '',
                'dest_folder_name': self.dest_folder_name,
                'preset_index': self._preset_index(),
                'custom_x': self.custom_x,
                'custom_y': self.custom_y,
            })
            self._state.save()
        except Exception:
            pass

    def on_pause(self):
        self._save_state()
        return True         # returning False would tear the app down on pause

    def on_resume(self):
        return True

    def on_stop(self):
        if self._engine is not None:
            self._engine.cancel()
        self._save_state()

    # --- layout -------------------------------------------------------------

    def _on_window_size(self, _window, size):
        self._apply_breakpoint(size[0])

    def _apply_breakpoint(self, width):
        try:
            self.wide = width >= dp(WIDE_BREAKPOINT_DP)
        except Exception:
            self.wide = False

    # --- presets ------------------------------------------------------------

    def _preset_index(self):
        try:
            return self.preset_labels.index(self.preset_label)
        except ValueError:
            return 0

    def select_preset(self, label):
        self.preset_label = label
        self.is_custom = self._preset_index() == self._custom_index

    def _current_scale(self):
        """Return ``(x, y)`` as strings, or ``None`` if the custom values are bad."""
        index = self._preset_index()
        _label, scale_x, scale_y = self._presets[index]
        if scale_x is None:
            scale_x = (self.custom_x or '').strip()
            scale_y = (self.custom_y or '').strip()
            try:
                if float(scale_x) <= 0 or float(scale_y) <= 0:
                    raise ValueError
            except ValueError:
                return None
        return scale_x, scale_y

    # --- folder pickers -----------------------------------------------------

    def browse_source(self):
        self._browse('source')

    def browse_dest(self):
        self._browse('dest')

    def _browse(self, which):
        if IS_ANDROID:
            import saf
            try:
                callback = lambda picked, warning: self._picked(which, picked, warning)
                if which == 'source':
                    saf.pick_files(callback)
                else:
                    saf.pick_folder(callback)
            except Exception as exc:
                self.log('!  could not open the picker: %s' % exc)
            return
        self._desktop_browse(which)

    def _desktop_browse(self, which):
        """Development-only picker so the UI can be exercised on a desktop.

        Takes a folder for the destination and a glob (or a folder) for the
        files, mirroring what the SAF pickers return on a phone.
        """
        from kivy.uix.textinput import TextInput
        from kivy.uix.button import Button
        from kivy.uix.popup import Popup

        box = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(8))
        field = TextInput(text=os.path.expanduser('~'), multiline=False,
                          size_hint_y=None, height=dp(40))
        button = Button(text='Use this', size_hint_y=None, height=dp(44))
        box.add_widget(field)
        box.add_widget(button)
        popup = Popup(title='Select %s' % which, content=box,
                      size_hint=(0.9, None), height=dp(180))

        def choose(*_args):
            import glob
            import storage
            popup.dismiss()
            text = field.text.strip()
            if which == 'dest':
                self._picked(which, storage.LocalFolder(text), None)
                return
            paths = sorted(glob.glob(text)) if any(c in text for c in '*?[') \
                else [text]
            if len(paths) == 1 and os.path.isdir(paths[0]):
                paths = sorted(os.path.join(paths[0], entry)
                               for entry in os.listdir(paths[0]))
            self._picked(which, storage.LocalFileSelection(paths), None)

        button.bind(on_release=choose)
        popup.open()

    @mainthread
    def _picked(self, which, picked, warning):
        """Called back from the Android UI thread -- hop to Kivy's main thread."""
        if warning:
            self.log('i  %s' % warning)
        if picked is None:
            return
        try:
            if which == 'source':
                self._set_source(picked)
            else:
                self._set_dest(picked)
        except Exception as exc:
            self.log('!  %s' % exc)

    def _set_source(self, selection):
        self._source = selection
        self.source_text = selection.label
        try:
            supported = len(selection.list_images())
        except Exception:
            supported = 0
        self.log('Selected: %s (%d supported)' % (selection.label, supported))

    def _set_dest(self, folder, remember=True):
        self._dest = folder
        self._refresh_dest_text()
        if remember:
            self.log('Output : %s' % self.dest_text)
            self._save_state()

    def _refresh_dest_text(self):
        if self._dest is None:
            self.dest_text = 'Choose where results are written'
            return
        name = (self.dest_folder_name or '').strip()
        base = self._dest.label.rstrip('/')
        self.dest_text = '%s/%s' % (base, name) if name else base

    def on_dest_folder_name(self, _instance, _value):
        self._refresh_dest_text()

    # --- run / cancel -------------------------------------------------------

    def start_run(self):
        if self.is_running:
            return
        if self._source is None:
            self.log('!  Please select the images to process.')
            return
        if self._dest is None:
            self.log('!  Please choose an output folder.')
            return

        scale = self._current_scale()
        if scale is None:
            self.log('!  Invalid custom ratio values.')
            return
        scale_x, scale_y = scale

        try:
            if not self._source.exists():
                self.log('!  The selected files are no longer available.')
                return
            dest = self._resolve_dest()
        except Exception as exc:
            self.log('!  %s' % exc)
            return

        self._log = []
        self._refresh_log()
        self.progress_value = 0
        self.is_running = True
        self.log('Files  : %s' % self._source.label)
        self.log('Output : %s' % dest.label)
        self._save_state()

        from engine import build_engine
        self._engine = build_engine()
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(self._engine, self._source, dest, scale_x, scale_y),
            daemon=True)
        self._worker.start()

    def _resolve_dest(self):
        """The output folder, creating the named subfolder inside it.

        The subfolder matters: people naturally pick the folder their images
        came from, and writing there directly would overwrite the originals
        with their desqueezed versions.
        """
        if not self._dest.exists():
            raise RuntimeError('Output folder is no longer available.')
        name = (self.dest_folder_name or '').strip()
        if not name:
            return self._dest
        return self._dest.ensure_child_folder(name)

    def _run_worker(self, engine, source, dest, scale_x, scale_y):
        """Runs off the main thread; only ever appends to ``self._events``."""
        try:
            for event in engine.process(source, dest, scale_x, scale_y):
                self._events.append(event)
        except Exception:
            from engine import done
            for line in traceback.format_exc().strip().split('\n'):
                self._events.append(_log_event(line))
            self._events.append(done(False, 'Unexpected error - see log'))

    def cancel_run(self):
        if self._engine is not None and self.is_running:
            self._engine.cancel()
            self.log('Cancelling after the current file...')

    # --- log ----------------------------------------------------------------

    def _drain_events(self, _dt):
        """Main-thread consumer for everything the worker produced."""
        if not self._events:
            return
        dirty = False
        finished = None
        while self._events:
            try:
                event = self._events.popleft()
            except IndexError:
                break
            if event.kind == 'log':
                self._append_line(event.message)
                dirty = True
            elif event.kind == 'progress':
                self.progress_value = event.value
            elif event.kind == 'done':
                finished = event

        if finished is not None:
            self._append_line('')
            self._append_line('%s  %s' % ('OK' if finished.ok else 'FAILED',
                                          finished.message))
            self.progress_value = 100 if finished.ok else 0
            self.is_running = False
            dirty = True
        if dirty:
            self._refresh_log()

    def _append_line(self, text):
        self._log.append(text)
        if len(self._log) > MAX_LOG_LINES:
            del self._log[:len(self._log) - MAX_LOG_LINES]

    def log(self, text):
        for line in str(text).split('\n'):
            self._append_line(line)
        self._refresh_log()

    def log_lines_from_traceback(self, text):
        self._append_line('!  internal error (recovered):')
        for line in text.strip().split('\n')[-4:]:
            self._append_line('   ' + line)
        self._refresh_log()

    def _refresh_log(self):
        try:
            view = self.root.ids.log_view
        except Exception:
            return
        view.data = [{'text': line} for line in self._log]
        view.scroll_y = 0


def _log_event(message):
    from engine import log as make_log
    return make_log(message)


if __name__ == '__main__':
    DesqueezeApp().run()
