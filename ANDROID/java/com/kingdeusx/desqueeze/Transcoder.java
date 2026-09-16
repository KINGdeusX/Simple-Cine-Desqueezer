package com.kingdeusx.desqueeze;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.util.Range;
import android.view.Surface;

import java.io.FileDescriptor;
import java.nio.ByteBuffer;

/**
 * Re-encodes a clip to HEVC at the desqueezed size, using the phone's hardware
 * encoder.
 *
 * The pipeline never brings a frame into application memory: the decoder writes
 * into a SurfaceTexture, the GPU draws that onto the encoder's input surface at
 * the stretched size, and the encoder hands compressed samples to the muxer.
 * Audio is copied across untouched.
 *
 * Runs on its own thread; the Python side starts it and polls progress, which
 * keeps the UI responsive and makes cancelling immediate.
 */
public class Transcoder implements Runnable {

    private static final String VIDEO_MIME = "video/hevc";
    private static final long DEQUEUE_TIMEOUT_US = 10000;
    private static final int DEFAULT_FRAME_RATE = 30;
    /** Enough audio to cover any sane delay before the muxer starts. */
    private static final int MAX_PENDING_AUDIO = 4000;

    public static final String MODE_VBR = "vbr";
    public static final String MODE_CBR = "cbr";
    public static final String MODE_CQ = "cq";

    private final FileDescriptor input;
    private final FileDescriptor output;
    private final int requestedWidth;
    private final int requestedHeight;
    private final int requestedBitrate;
    private final String requestedMode;
    private final int keyframeSeconds;
    private final int quality;

    private volatile int progress;
    private volatile boolean finished;
    private volatile boolean success;
    private volatile boolean cancelled;
    private volatile String error;
    private volatile String summary = "";
    private volatile int audioSamples;
    private volatile String audioNote = "";

    private Thread thread;

    // resolved once the encoder's real capabilities are known
    private int width;
    private int height;
    private int bitrate;
    private String mode;

    public Transcoder(FileDescriptor input, FileDescriptor output,
                      int width, int height, int bitrate, String mode,
                      int keyframeSeconds, int quality) {
        this.input = input;
        this.output = output;
        this.requestedWidth = width;
        this.requestedHeight = height;
        this.requestedBitrate = bitrate;
        this.requestedMode = mode == null ? MODE_VBR : mode.toLowerCase();
        this.keyframeSeconds = keyframeSeconds > 0 ? keyframeSeconds : 1;
        this.quality = quality;
    }

    // --- the small surface the Python side talks to -------------------------

    public void start() {
        thread = new Thread(this, "desqueeze-transcode");
        thread.start();
    }

    public int getProgress() {
        return progress;
    }

    public boolean isFinished() {
        return finished;
    }

    public boolean isSuccess() {
        return success;
    }

    public String getError() {
        return error == null ? "" : error;
    }

    public String getSummary() {
        return summary;
    }

    public void cancel() {
        cancelled = true;
    }

    /** True when this device can encode HEVC at all. */
    public static boolean isSupported() {
        return findEncoder() != null;
    }

    // --- capability negotiation ---------------------------------------------

    private static MediaCodecInfo findEncoder() {
        MediaCodecList list = new MediaCodecList(MediaCodecList.REGULAR_CODECS);
        for (MediaCodecInfo info : list.getCodecInfos()) {
            if (!info.isEncoder()) {
                continue;
            }
            for (String type : info.getSupportedTypes()) {
                if (type.equalsIgnoreCase(VIDEO_MIME)) {
                    return info;
                }
            }
        }
        return null;
    }

    private static int alignDown(int value, int alignment) {
        if (alignment <= 1) {
            return value;
        }
        return Math.max(alignment, (value / alignment) * alignment);
    }

    /**
     * Fit the requested output to what this particular encoder will accept.
     *
     * Hardware encoders -- MediaTek's especially -- demand that dimensions be
     * multiples of 2, 16 or even 32, and quietly produce garbage or refuse to
     * configure when they are not.  Asking the codec rather than assuming is the
     * difference between working on one phone and working on all of them.
     */
    private void resolveOutputFormat(MediaCodecInfo encoder) {
        MediaCodecInfo.CodecCapabilities caps =
                encoder.getCapabilitiesForType(VIDEO_MIME);
        MediaCodecInfo.VideoCapabilities video = caps.getVideoCapabilities();

        Range<Integer> widths = video.getSupportedWidths();
        Range<Integer> heights = video.getSupportedHeights();

        // Shrink to fit the encoder's limits *proportionally*.  Clamping the two
        // axes separately would silently square the picture -- a 2554x1080
        // desqueeze coming out 512x512 -- which is worse than refusing.
        double factor = 1.0;
        if (requestedWidth > widths.getUpper()) {
            factor = Math.min(factor, widths.getUpper() / (double) requestedWidth);
        }
        if (requestedHeight > heights.getUpper()) {
            factor = Math.min(factor, heights.getUpper() / (double) requestedHeight);
        }
        width = (int) Math.floor(requestedWidth * factor);
        height = (int) Math.floor(requestedHeight * factor);

        width = alignDown(Math.max(width, widths.getLower()),
                video.getWidthAlignment());
        height = alignDown(Math.max(height, heights.getLower()),
                video.getHeightAlignment());

        if (!video.isSizeSupported(width, height)) {
            // Step both axes down together so the aspect ratio survives.
            int widthStep = Math.max(2, video.getWidthAlignment());
            int heightStep = Math.max(2, video.getHeightAlignment());
            double aspect = width / (double) height;
            int candidateWidth = width;
            int candidateHeight = height;
            while (candidateWidth > widthStep && candidateHeight > heightStep
                    && !video.isSizeSupported(candidateWidth, candidateHeight)) {
                candidateWidth = alignDown(candidateWidth - widthStep, widthStep);
                candidateHeight = alignDown(
                        (int) Math.round(candidateWidth / aspect), heightStep);
            }
            if (!video.isSizeSupported(candidateWidth, candidateHeight)) {
                throw new RuntimeException("this device's HEVC encoder cannot "
                        + "handle " + requestedWidth + "x" + requestedHeight);
            }
            width = candidateWidth;
            height = candidateHeight;
        }

        bitrate = clamp(requestedBitrate, video.getBitrateRange());
        mode = resolveBitrateMode(caps.getEncoderCapabilities(), requestedMode);
    }

    private static int clamp(int value, Range<Integer> range) {
        if (value < range.getLower()) {
            return range.getLower();
        }
        if (value > range.getUpper()) {
            return range.getUpper();
        }
        return value;
    }

    private static String resolveBitrateMode(
            MediaCodecInfo.EncoderCapabilities caps, String wanted) {
        if (caps == null) {
            return MODE_VBR;
        }
        if (MODE_CQ.equals(wanted) && caps.isBitrateModeSupported(
                MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CQ)) {
            return MODE_CQ;
        }
        if (MODE_CBR.equals(wanted) && caps.isBitrateModeSupported(
                MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR)) {
            return MODE_CBR;
        }
        if (caps.isBitrateModeSupported(
                MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR)) {
            return MODE_VBR;
        }
        if (caps.isBitrateModeSupported(
                MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR)) {
            return MODE_CBR;
        }
        return MODE_VBR;
    }

    private static int bitrateModeConstant(String mode) {
        if (MODE_CBR.equals(mode)) {
            return MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR;
        }
        if (MODE_CQ.equals(mode)) {
            return MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CQ;
        }
        return MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR;
    }

    // --- the work -----------------------------------------------------------

    @Override
    public void run() {
        MediaExtractor videoExtractor = null;
        MediaCodec decoder = null;
        String summaryBase = "";
        MediaCodec encoder = null;
        MediaMuxer muxer = null;
        InputSurface inputSurface = null;
        OutputSurface outputSurface = null;
        boolean muxerStarted = false;

        try {
            MediaCodecInfo encoderInfo = findEncoder();
            if (encoderInfo == null) {
                throw new RuntimeException("this device has no HEVC encoder");
            }
            resolveOutputFormat(encoderInfo);

            videoExtractor = new MediaExtractor();
            videoExtractor.setDataSource(input);
            int videoTrack = selectTrack(videoExtractor, "video/");
            if (videoTrack < 0) {
                throw new RuntimeException("no video track in this file");
            }
            videoExtractor.selectTrack(videoTrack);
            MediaFormat sourceFormat = videoExtractor.getTrackFormat(videoTrack);

            long durationUs = sourceFormat.containsKey(MediaFormat.KEY_DURATION)
                    ? sourceFormat.getLong(MediaFormat.KEY_DURATION) : 0;
            int frameRate = sourceFormat.containsKey(MediaFormat.KEY_FRAME_RATE)
                    ? sourceFormat.getInteger(MediaFormat.KEY_FRAME_RATE)
                    : DEFAULT_FRAME_RATE;
            int rotation = sourceFormat.containsKey(MediaFormat.KEY_ROTATION)
                    ? sourceFormat.getInteger(MediaFormat.KEY_ROTATION) : 0;

            MediaFormat outputFormat = MediaFormat.createVideoFormat(
                    VIDEO_MIME, width, height);
            outputFormat.setInteger(MediaFormat.KEY_COLOR_FORMAT,
                    MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
            outputFormat.setInteger(MediaFormat.KEY_FRAME_RATE, frameRate);
            outputFormat.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, keyframeSeconds);
            outputFormat.setInteger(MediaFormat.KEY_BITRATE_MODE,
                    bitrateModeConstant(mode));
            if (MODE_CQ.equals(mode)) {
                outputFormat.setInteger(MediaFormat.KEY_QUALITY, quality);
            } else {
                outputFormat.setInteger(MediaFormat.KEY_BIT_RATE, bitrate);
            }

            summaryBase = width + "x" + height + " HEVC "
                    + mode.toUpperCase() + " "
                    + String.format(java.util.Locale.US, "%.1f", bitrate / 1000000.0)
                    + " Mbps";
            if (width != requestedWidth || height != requestedHeight) {
                // Say so rather than quietly handing back a different size.
                summaryBase = requestedWidth + "x" + requestedHeight + " capped to "
                        + summaryBase;
            }
            summary = summaryBase;

            encoder = MediaCodec.createByCodecName(encoderInfo.getName());
            encoder.configure(outputFormat, null, null,
                    MediaCodec.CONFIGURE_FLAG_ENCODE);
            Surface encoderSurface = encoder.createInputSurface();
            inputSurface = new InputSurface(encoderSurface);
            inputSurface.makeCurrent();
            encoder.start();

            outputSurface = new OutputSurface();
            decoder = MediaCodec.createDecoderByType(
                    sourceFormat.getString(MediaFormat.KEY_MIME));
            decoder.configure(sourceFormat, outputSurface.getSurface(), null, 0);
            decoder.start();

            muxer = new MediaMuxer(output, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            if (rotation != 0) {
                muxer.setOrientationHint(rotation);
            }

            // Audio rides along untouched, read from the *same* extractor as
            // the video.  A second MediaExtractor over the same file is not
            // reliable -- it returns a valid track format and then no samples --
            // so both tracks are selected here and dispatched by index.
            int audioTrack = selectTrack(videoExtractor, "audio/");
            MediaFormat audioFormat = null;
            if (audioTrack >= 0) {
                videoExtractor.selectTrack(audioTrack);
                audioFormat = videoExtractor.getTrackFormat(audioTrack);
            } else {
                audioNote = "no audio track";
            }

            MediaCodec.BufferInfo videoInfo = new MediaCodec.BufferInfo();
            MediaCodec.BufferInfo decoderInfo = new MediaCodec.BufferInfo();
            ByteBuffer audioBuffer = ByteBuffer.allocateDirect(1024 * 512);
            MediaCodec.BufferInfo audioInfo = new MediaCodec.BufferInfo();

            int muxVideoTrack = -1;
            int muxAudioTrack = -1;
            boolean extractorDone = false;
            boolean decoderDone = false;
            boolean encoderDone = false;
            long lastVideoUs = 0;
            java.util.ArrayDeque<PendingSample> pendingAudio =
                    new java.util.ArrayDeque<>();

            while (!encoderDone && !cancelled) {
                // 1. pull the next sample and send it where it belongs
                if (!extractorDone) {
                    int sampleTrack = videoExtractor.getSampleTrackIndex();
                    if (sampleTrack == audioTrack && audioTrack >= 0) {
                        readAudioSample(videoExtractor, audioBuffer, pendingAudio);
                        if (muxerStarted) {
                            flushAudio(muxer, muxAudioTrack, pendingAudio, audioInfo);
                        }
                    } else {
                        int index = decoder.dequeueInputBuffer(DEQUEUE_TIMEOUT_US);
                        if (index >= 0) {
                            ByteBuffer buffer = decoder.getInputBuffer(index);
                            int size = videoExtractor.readSampleData(buffer, 0);
                            if (size < 0) {
                                decoder.queueInputBuffer(index, 0, 0, 0,
                                        MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                                extractorDone = true;
                            } else {
                                decoder.queueInputBuffer(index, 0, size,
                                        videoExtractor.getSampleTime(), 0);
                                videoExtractor.advance();
                            }
                        }
                    }
                }

                // 2. decoder -> GPU -> encoder input surface
                if (!decoderDone) {
                    int index = decoder.dequeueOutputBuffer(decoderInfo,
                            DEQUEUE_TIMEOUT_US);
                    if (index >= 0) {
                        boolean render = decoderInfo.size > 0;
                        decoder.releaseOutputBuffer(index, render);
                        if (render) {
                            outputSurface.awaitNewImage();
                            outputSurface.drawImage();
                            inputSurface.setPresentationTime(
                                    decoderInfo.presentationTimeUs * 1000);
                            inputSurface.swapBuffers();
                        }
                        if ((decoderInfo.flags
                                & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                            decoderDone = true;
                            encoder.signalEndOfInputStream();
                        }
                    }
                }

                // 3. encoder -> muxer
                int index = encoder.dequeueOutputBuffer(videoInfo, DEQUEUE_TIMEOUT_US);
                if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    if (muxerStarted) {
                        throw new RuntimeException("encoder changed format twice");
                    }
                    muxVideoTrack = muxer.addTrack(encoder.getOutputFormat());
                    if (audioFormat != null) {
                        muxAudioTrack = muxer.addTrack(audioFormat);
                    }
                    muxer.start();
                    muxerStarted = true;
                    flushAudio(muxer, muxAudioTrack, pendingAudio, audioInfo);
                } else if (index >= 0) {
                    ByteBuffer encoded = encoder.getOutputBuffer(index);
                    boolean isConfig = (videoInfo.flags
                            & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;
                    if (!isConfig && videoInfo.size > 0 && muxerStarted) {
                        encoded.position(videoInfo.offset);
                        encoded.limit(videoInfo.offset + videoInfo.size);
                        muxer.writeSampleData(muxVideoTrack, encoded, videoInfo);
                        lastVideoUs = videoInfo.presentationTimeUs;
                        if (durationUs > 0) {
                            progress = (int) Math.min(99,
                                    lastVideoUs * 100 / durationUs);
                        }
                    }
                    encoder.releaseOutputBuffer(index, false);
                    if ((videoInfo.flags
                            & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        encoderDone = true;
                    }
                }

            }

            if (cancelled) {
                error = "cancelled";
            } else {
                // anything still queued when the video ended
                if (muxerStarted) {
                    flushAudio(muxer, muxAudioTrack, pendingAudio, audioInfo);
                }
                progress = 100;
                success = true;
                summary = summaryBase
                        + (audioSamples > 0 ? "  +audio" : "  (no audio)")
                        + (audioNote.isEmpty() ? "" : "  [" + audioNote + "]");
            }
        } catch (Throwable problem) {
            error = describe(problem);
            success = false;
        } finally {
            closeQuietly(muxer, muxerStarted);
            releaseQuietly(decoder);
            releaseQuietly(encoder);
            if (outputSurface != null) {
                try {
                    outputSurface.release();
                } catch (Throwable ignored) {
                    // releasing GL state must never mask the real failure
                }
            }
            if (inputSurface != null) {
                try {
                    inputSurface.release();
                } catch (Throwable ignored) {
                }
            }
            releaseQuietly(videoExtractor);
            finished = true;
        }
    }

    /** One audio sample held until the muxer is ready to take it. */
    private static final class PendingSample {
        final byte[] data;
        final long presentationTimeUs;
        final int flags;

        PendingSample(byte[] data, long presentationTimeUs, int flags) {
            this.data = data;
            this.presentationTimeUs = presentationTimeUs;
            this.flags = flags;
        }
    }

    /**
     * Take the current audio sample off the extractor and queue it.
     *
     * The muxer cannot accept anything until the encoder has announced its
     * output format, which is a few frames in, so the handful of audio samples
     * that arrive before then wait here.
     */
    private void readAudioSample(MediaExtractor extractor, ByteBuffer buffer,
                                 java.util.ArrayDeque<PendingSample> pending) {
        buffer.clear();
        int size = extractor.readSampleData(buffer, 0);
        if (size <= 0) {
            extractor.advance();
            return;
        }
        if (pending.size() < MAX_PENDING_AUDIO) {
            byte[] copy = new byte[size];
            buffer.position(0);
            buffer.get(copy, 0, size);
            pending.add(new PendingSample(copy, extractor.getSampleTime(),
                    extractor.getSampleFlags()));
        } else if (audioNote.isEmpty()) {
            audioNote = "audio truncated (muxer never became ready)";
        }
        extractor.advance();
    }

    /** Write every queued audio sample into the muxer. */
    private void flushAudio(MediaMuxer muxer, int track,
                            java.util.ArrayDeque<PendingSample> pending,
                            MediaCodec.BufferInfo info) {
        if (track < 0) {
            pending.clear();
            return;
        }
        while (!pending.isEmpty()) {
            PendingSample sample = pending.poll();
            ByteBuffer out = ByteBuffer.wrap(sample.data);
            info.offset = 0;
            info.size = sample.data.length;
            info.presentationTimeUs = sample.presentationTimeUs;
            info.flags = sample.flags;
            out.position(0);
            out.limit(sample.data.length);
            muxer.writeSampleData(track, out, info);
            audioSamples++;
        }
    }

    private static int selectTrack(MediaExtractor extractor, String prefix) {
        for (int i = 0; i < extractor.getTrackCount(); i++) {
            MediaFormat format = extractor.getTrackFormat(i);
            String mime = format.getString(MediaFormat.KEY_MIME);
            if (mime != null && mime.startsWith(prefix)) {
                return i;
            }
        }
        return -1;
    }

    private static String describe(Throwable problem) {
        String message = problem.getMessage();
        String name = problem.getClass().getSimpleName();
        return (message == null || message.isEmpty()) ? name : name + ": " + message;
    }

    private static void closeQuietly(MediaMuxer muxer, boolean started) {
        if (muxer == null) {
            return;
        }
        try {
            if (started) {
                muxer.stop();
            }
        } catch (Throwable ignored) {
        }
        try {
            muxer.release();
        } catch (Throwable ignored) {
        }
    }

    private static void releaseQuietly(MediaCodec codec) {
        if (codec == null) {
            return;
        }
        try {
            codec.stop();
        } catch (Throwable ignored) {
        }
        try {
            codec.release();
        } catch (Throwable ignored) {
        }
    }

    private static void releaseQuietly(MediaExtractor extractor) {
        if (extractor != null) {
            try {
                extractor.release();
            } catch (Throwable ignored) {
            }
        }
    }
}
