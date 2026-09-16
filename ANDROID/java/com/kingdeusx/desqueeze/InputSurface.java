package com.kingdeusx.desqueeze;

import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLExt;
import android.opengl.EGLSurface;
import android.view.Surface;

/**
 * An EGL context and window surface wrapped around the encoder's input Surface.
 *
 * Everything drawn here goes straight into the encoder, so the frame never
 * makes a round trip through application memory.
 */
class InputSurface {

    /** Tells the driver this surface feeds a video encoder rather than a display. */
    private static final int EGL_RECORDABLE_ANDROID = 0x3142;

    private final Surface surface;
    private EGLDisplay display = EGL14.EGL_NO_DISPLAY;
    private EGLContext context = EGL14.EGL_NO_CONTEXT;
    private EGLSurface eglSurface = EGL14.EGL_NO_SURFACE;

    InputSurface(Surface surface) {
        if (surface == null) {
            throw new NullPointerException("encoder gave us no input surface");
        }
        this.surface = surface;
        setUpEgl();
    }

    private void setUpEgl() {
        display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        if (display == EGL14.EGL_NO_DISPLAY) {
            throw new RuntimeException("no EGL display available");
        }
        int[] version = new int[2];
        if (!EGL14.eglInitialize(display, version, 0, version, 1)) {
            display = EGL14.EGL_NO_DISPLAY;
            throw new RuntimeException("could not initialise EGL");
        }

        int[] attributes = {
                EGL14.EGL_RED_SIZE, 8,
                EGL14.EGL_GREEN_SIZE, 8,
                EGL14.EGL_BLUE_SIZE, 8,
                EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                EGL_RECORDABLE_ANDROID, 1,
                EGL14.EGL_NONE
        };
        EGLConfig[] configs = new EGLConfig[1];
        int[] found = new int[1];
        if (!EGL14.eglChooseConfig(display, attributes, 0, configs, 0, 1, found, 0)
                || found[0] <= 0) {
            throw new RuntimeException("no EGL config supports recording");
        }

        int[] contextAttributes = {EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE};
        context = EGL14.eglCreateContext(display, configs[0], EGL14.EGL_NO_CONTEXT,
                contextAttributes, 0);
        checkEglError("eglCreateContext");

        eglSurface = EGL14.eglCreateWindowSurface(display, configs[0], surface,
                new int[]{EGL14.EGL_NONE}, 0);
        checkEglError("eglCreateWindowSurface");
    }

    void makeCurrent() {
        if (!EGL14.eglMakeCurrent(display, eglSurface, eglSurface, context)) {
            throw new RuntimeException("eglMakeCurrent failed");
        }
    }

    boolean swapBuffers() {
        return EGL14.eglSwapBuffers(display, eglSurface);
    }

    /**
     * Stamps the frame's presentation time, in nanoseconds, onto the buffer the
     * next swap will hand to the encoder.  Without this every frame arrives with
     * the same timestamp and the output plays at the wrong speed.
     */
    void setPresentationTime(long nanoseconds) {
        EGLExt.eglPresentationTimeANDROID(display, eglSurface, nanoseconds);
    }

    void release() {
        if (display != EGL14.EGL_NO_DISPLAY) {
            EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE,
                    EGL14.EGL_NO_CONTEXT);
            if (eglSurface != EGL14.EGL_NO_SURFACE) {
                EGL14.eglDestroySurface(display, eglSurface);
            }
            if (context != EGL14.EGL_NO_CONTEXT) {
                EGL14.eglDestroyContext(display, context);
            }
            EGL14.eglReleaseThread();
            EGL14.eglTerminate(display);
        }
        display = EGL14.EGL_NO_DISPLAY;
        context = EGL14.EGL_NO_CONTEXT;
        eglSurface = EGL14.EGL_NO_SURFACE;
        surface.release();
    }

    private void checkEglError(String what) {
        int error = EGL14.eglGetError();
        if (error != EGL14.EGL_SUCCESS) {
            throw new RuntimeException(what + " failed: EGL error 0x"
                    + Integer.toHexString(error));
        }
    }
}
