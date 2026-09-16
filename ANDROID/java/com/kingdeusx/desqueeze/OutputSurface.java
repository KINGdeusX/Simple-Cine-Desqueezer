package com.kingdeusx.desqueeze;

import android.graphics.SurfaceTexture;
import android.os.Handler;
import android.os.HandlerThread;
import android.view.Surface;

/**
 * Where the decoder puts its frames: a SurfaceTexture backed by an external GL
 * texture, so each decoded frame becomes something the GPU can draw.
 */
class OutputSurface implements SurfaceTexture.OnFrameAvailableListener {

    private static final int FRAME_TIMEOUT_MS = 10000;

    private final Object frameLock = new Object();
    private final TextureRender render = new TextureRender();
    private final HandlerThread callbackThread;

    private SurfaceTexture surfaceTexture;
    private Surface surface;
    private boolean frameAvailable;

    OutputSurface() {
        render.surfaceCreated();
        surfaceTexture = new SurfaceTexture(render.getTextureId());

        // The frame-available callback needs a Looper to be delivered on, and
        // the transcoding thread deliberately has none -- so give it its own.
        callbackThread = new HandlerThread("desqueeze-frames");
        callbackThread.start();
        surfaceTexture.setOnFrameAvailableListener(this,
                new Handler(callbackThread.getLooper()));

        surface = new Surface(surfaceTexture);
    }

    Surface getSurface() {
        return surface;
    }

    /** Block until the decoder has produced a frame, then latch it. */
    void awaitNewImage() {
        synchronized (frameLock) {
            long deadline = System.currentTimeMillis() + FRAME_TIMEOUT_MS;
            while (!frameAvailable) {
                long remaining = deadline - System.currentTimeMillis();
                if (remaining <= 0) {
                    throw new RuntimeException(
                            "timed out waiting for a decoded frame");
                }
                try {
                    frameLock.wait(remaining);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    throw new RuntimeException("interrupted while decoding");
                }
            }
            frameAvailable = false;
        }
        surfaceTexture.updateTexImage();
    }

    void drawImage() {
        render.drawFrame(surfaceTexture);
    }

    @Override
    public void onFrameAvailable(SurfaceTexture unused) {
        synchronized (frameLock) {
            frameAvailable = true;
            frameLock.notifyAll();
        }
    }

    void release() {
        render.release();
        if (surface != null) {
            surface.release();
            surface = null;
        }
        if (surfaceTexture != null) {
            surfaceTexture.release();
            surfaceTexture = null;
        }
        callbackThread.quit();
    }
}
