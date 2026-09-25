import os
import sys
import time
from datetime import datetime

import boto3
import botocore
from ids_peak import ids_peak as peak
from ids_peak_ipl import ids_peak_ipl as ipl

m_device = None
m_dataStream = None
m_node_map_remote_device = None


S3_BUCKET = os.getenv("S3_BUCKET_NAME")
S3_PREFIX = os.getenv("S3_PREFIX", "rgb/")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
DELETE_LOCAL_AFTER_UPLOAD = True

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)


def open_camera():
    global m_device, m_node_map_remote_device
    try:
        device_manager = peak.DeviceManager.Instance()
        device_manager.Update()
        if device_manager.Devices().empty():
            return False
        for i in range(device_manager.Devices().size()):
            if device_manager.Devices()[i].IsOpenable():
                m_device = device_manager.Devices()[i].OpenDevice(
                    peak.DeviceAccessType_Control
                )
                m_node_map_remote_device = m_device.RemoteDevice().NodeMaps()[0]
                return True
    except Exception as e:
        print(f"Camera open error: {e}")
        return False


def prepare_acquisition():
    global m_dataStream
    try:
        data_streams = m_device.DataStreams()
        if data_streams.empty():
            return False
        m_dataStream = data_streams[0].OpenDataStream()
        return True
    except Exception as e:
        print(f"Acquisition error: {e}")
        return False


def set_roi(x, y, width, height):
    m_color_corrector_ipl = ipl.ColorCorrector()
    try:
        # ROI bounds
        x_min = m_node_map_remote_device.FindNode("OffsetX").Minimum()
        y_min = m_node_map_remote_device.FindNode("OffsetY").Minimum()
        w_min = m_node_map_remote_device.FindNode("Width").Minimum()
        h_min = m_node_map_remote_device.FindNode("Height").Minimum()

        x_max = m_node_map_remote_device.FindNode("OffsetX").Maximum()
        y_max = m_node_map_remote_device.FindNode("OffsetY").Maximum()
        w_max = m_node_map_remote_device.FindNode("Width").Maximum()
        h_max = m_node_map_remote_device.FindNode("Height").Maximum()

        if not (x_min <= x <= x_max and y_min <= y <= y_max):
            return False
        if not (w_min <= width <= w_max and h_min <= height <= h_max):
            return False

        m_node_map_remote_device.FindNode("OffsetX").SetValue(x)
        m_node_map_remote_device.FindNode("OffsetY").SetValue(y)
        m_node_map_remote_device.FindNode("Width").SetValue(width)
        m_node_map_remote_device.FindNode("Height").SetValue(height)

        # Settings
        m_node_map_remote_device.FindNode("ExposureAuto").SetCurrentEntry("Continuous")
        m_node_map_remote_device.FindNode("GainAuto").SetCurrentEntry("Continuous")
        m_node_map_remote_device.FindNode("BalanceWhiteAuto").SetCurrentEntry(
            "Continuous"
        )
        m_node_map_remote_device.FindNode("AcquisitionMode").SetCurrentEntry(
            "Continuous"
        )
        m_node_map_remote_device.FindNode("AcquisitionFrameRateTargetEnable").SetValue(
            True
        )
        m_node_map_remote_device.FindNode("AcquisitionFrameRateTarget").SetValue(20)
        m_node_map_remote_device.FindNode("ColorCorrectionMatrix").SetCurrentEntry("HQ")

        # Color Correction Matrix values
        def get_gain(row, col):
            selector = f"Gain{row}{col}"
            m_node_map_remote_device.FindNode(
                "ColorCorrectionMatrixValueSelector"
            ).SetCurrentEntry(selector)
            return m_node_map_remote_device.FindNode(
                "ColorCorrectionMatrixValue"
            ).Value()

        factors = ipl.ColorCorrectionFactors(
            get_gain(0, 0),
            get_gain(0, 1),
            get_gain(0, 2),
            get_gain(1, 0),
            get_gain(1, 1),
            get_gain(1, 2),
            get_gain(2, 0),
            get_gain(2, 1),
            get_gain(2, 2),
        )
        m_color_corrector_ipl.SetColorCorrectionFactors(factors)

        m_node_map_remote_device.FindNode("LUTEnable").SetValue(False)
        m_node_map_remote_device.FindNode("Gamma").SetValue(2.2)
        m_node_map_remote_device.FindNode("BrightnessAutoTarget").SetValue(150)
        m_node_map_remote_device.FindNode("ComponentSelector").SetCurrentEntry(
            "Intensity"
        )
        m_node_map_remote_device.FindNode("PixelFormat").SetCurrentEntry("BayerRG8")
        m_node_map_remote_device.FindNode("BlackLevel").SetValue(0.31)

        return True
    except Exception as e:
        print(f"ROI error: {e}")
        return False


def alloc_and_announce_buffers():
    try:
        if not m_dataStream:
            return False
        m_dataStream.Flush(peak.DataStreamFlushMode_DiscardAll)
        for buffer in m_dataStream.AnnouncedBuffers():
            m_dataStream.RevokeBuffer(buffer)

        payload_size = m_node_map_remote_device.FindNode("PayloadSize").Value()
        num_buffers = m_dataStream.NumBuffersAnnouncedMinRequired()

        for _ in range(num_buffers):
            buffer = m_dataStream.AllocAndAnnounceBuffer(payload_size)
            m_dataStream.QueueBuffer(buffer)

        return True
    except Exception as e:
        print(f"Buffer allocation error: {e}")
        return False


def start_acquisition():
    try:
        m_dataStream.StartAcquisition(
            peak.AcquisitionStartMode_Default, peak.DataStream.INFINITE_NUMBER
        )
        m_node_map_remote_device.FindNode("TLParamsLocked").SetValue(1)
        m_node_map_remote_device.FindNode("AcquisitionStart").Execute()
        print("Acquisition started successfully.")
        return True
    except Exception as e:
        print(f"Acquisition start error: {e}")
        return False


def upload_local_file_to_s3(local_path: str, index: int):
    if not S3_BUCKET:
        print("S3_BUCKET_NAME env var not set. Skipping S3 upload.")
        return False

    # key pattern: camera_raw/YYYY/MM/DD/UTC_timestamp_000123.jpg
    now = datetime.utcnow()
    base_name = os.path.basename(local_path)
    key = f"{S3_PREFIX}{now:%Y/%m/%d}/{now:%Y%m%dT%H%M%SZ}_{index:06}.jpg"

    try:
        s3.upload_file(
            local_path, S3_BUCKET, key, ExtraArgs={"ContentType": "image/jpeg"}
        )
        print(f"Uploaded to s3://{S3_BUCKET}/{key}")
        if DELETE_LOCAL_AFTER_UPLOAD:
            try:
                os.remove(local_path)
            except:
                pass
        return True
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        print(f"S3 upload failed ({code}): {e}")
        return False
    except Exception as e:
        print(f"S3 upload failed: {e}")
        return False


def save_image(num_images=2):
    m_hotpixel_correction = ipl.HotpixelCorrection()
    try:
        for i in range(num_images):
            buffer = m_dataStream.WaitForFinishedBuffer(5000)
            image = ipl.Image.CreateFromSizeAndBuffer(
                buffer.PixelFormat(),
                buffer.BasePtr(),
                buffer.Size(),
                buffer.Width(),
                buffer.Height(),
            )
            vec = m_hotpixel_correction.Detect(image)
            image = m_hotpixel_correction.Correct(image, vec)
            image_rgb = image.ConvertTo(
                ipl.PixelFormatName_RGBa8, ipl.ConversionMode_Fast
            )
            filename = f"/home/vinni/images7/image_{i:03}.jpg"
            ipl.ImageWriter.Write(filename, image_rgb)
            print(f"[{i + 1}/{num_images}] Saved: {filename}")

            upload_local_file_to_s3(filename, i)

            m_dataStream.QueueBuffer(buffer)
            time.sleep(9)
        return True
    except Exception as e:
        print(f"Image save error: {e}")
        return False


def main():
    peak.Library.Initialize()
    if not open_camera():
        sys.exit(-1)
    if not prepare_acquisition():
        sys.exit(-2)

    w_max = m_node_map_remote_device.FindNode("Width").Maximum()
    h_max = m_node_map_remote_device.FindNode("Height").Maximum()

    if not set_roi(0, 0, w_max, h_max):
        sys.exit(-3)
    if not alloc_and_announce_buffers():
        sys.exit(-4)
    if not start_acquisition():
        sys.exit(-5)
    if not save_image():
        sys.exit(-6)

    peak.Library.Close()
    sys.exit(0)


if __name__ == "__main__":
    main()
