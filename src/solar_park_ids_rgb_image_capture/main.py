import os
import sys
import time
import logging
import datetime as dt
from dataclasses import dataclass

from ids_peak import ids_peak as peak
from ids_peak_ipl import ids_peak_ipl as ipl
from . import store

LOG_FILE = "/home/vinni/rgb_camera_data_pipeline.log"
logger = logging.getLogger(__name__)
logging.basicConfig(
    filename=LOG_FILE,
    encoding="utf-8",
    level=logging.INFO,
    format='{"time"="%(asctime)s", %(message)s}',
    datefmt="%Y-%m-%d %H:%M:%S",
)

CAMERA_ID = "ids_rgb_cam"

CAMERA_BRIGHTNESS_MIN = 15
CAMERA_BRIGHTNESS_MAX = 240
CAMERA_MAX_SATURATED_FRACTION = 0.15

# Minimum time between stored images.
CAMERA_CAPTURE_FREQUENCY_SEC = 30 * 60
# Maximum time between stored images
CAMERA_MAX_TIME_BETWEEN_CAPTURES_SEC = 40 * 60

m_device = None
m_dataStream = None
m_node_map_remote_device = None


def log_data(level: int, camera: "Camera", data: dict):
    msg = f'"camera": "{camera.id}"'
    for key, value in data.items():
        msg += f', "{key}": "{value}"'
    logger.log(level, msg)


@dataclass
class MeasurementConfig:
    brightness_min: float
    brightness_max: float
    max_saturated_fraction: float
    capture_freq_sec: int
    max_period_between_captures_sec: int

class Camera:
    def __init__(self, id: str, measurement_config: MeasurementConfig):
        self.active = True
        self.measurement_config = measurement_config
        self._id = id
        self._last_stored_time: None | dt.datetime = None

    @property
    def id(self) -> str:
        return self._id

    def min_time_between_captures_has_elapsed(self, time: dt.datetime) -> bool:
        if self._last_stored_time is None:
            return True

        elapsed = time - self._last_stored_time
        return elapsed.total_seconds() >= self.measurement_config.capture_freq_sec

    def max_time_between_captures_has_elapsed(self, time: dt.datetime) -> bool:
        if self._last_stored_time is None:
            return True

        elapsed = time - self._last_stored_time
        exceeded = (
            elapsed.total_seconds()
            >= self.measurement_config.max_period_between_captures_sec
        )
        log_data(
            logging.DEBUG,
            self,
            dict(
                event="capture_validation", property="max_period_between_captures_sec", value=exceeded,
            ),
        )

        return exceeded

    def image_surpasses_brightness_min_threshold(self, mean_intensity: float) -> bool:
        exceeded = mean_intensity >= self.measurement_config.brightness_min
        log_data(
            logging.DEBUG,
            self,
            dict(event="capture_validation", property="brightness_min", value=exceeded),
        )
        return exceeded

    def image_surpasses_brightness_max_threshold(self, mean_intensity: float) -> bool:
        exceeded = mean_intensity <= self.measurement_config.brightness_max
        log_data(
            logging.DEBUG,
            self,
            dict(event="capture_validation", property="brightness_max", value=exceeded),
        )
        return exceeded

    def image_surpasses_saturation_threshold(self, saturated_fraction: float) -> bool:
        exceeded = saturated_fraction <= self.measurement_config.max_saturated_fraction
        log_data(
            logging.DEBUG,
            self,
            dict(
                event="capture_validation", property="max_saturated_fraction", value=exceeded,
            ),
        )
        return exceeded

    def image_should_be_stored(
        self, time: dt.datetime, mean_intensity: float, saturated_fraction: float
    ) -> bool:
        if not self.image_surpasses_brightness_min_threshold(mean_intensity):
            return False
        if not self.image_surpasses_brightness_max_threshold(mean_intensity):
            return False
        if not self.image_surpasses_saturation_threshold(saturated_fraction):
            return False

        self.max_time_between_captures_has_elapsed(time)
        return True

    def set_last_stored_time(self, time: dt.datetime):
        self._last_stored_time = time


CAMERAS = [
    Camera(
        CAMERA_ID,
        MeasurementConfig(
            CAMERA_BRIGHTNESS_MIN,
            CAMERA_BRIGHTNESS_MAX,
            CAMERA_MAX_SATURATED_FRACTION,
            CAMERA_CAPTURE_FREQUENCY_SEC,
            CAMERA_MAX_TIME_BETWEEN_CAPTURES_SEC,
        ),
    )
]


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
        log_data(logging.ERROR, CAMERAS[0], dict(event="camera_open_error", error=str(e)))
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
        log_data(logging.ERROR, CAMERAS[0], dict(event="acquisition_prepare_error", error=str(e)))
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
        log_data(logging.ERROR, CAMERAS[0], dict(event="roi_error", error=str(e)))
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
        log_data(logging.ERROR, CAMERAS[0], dict(event="buffer_alloc_error", error=str(e)))
        return False

def start_acquisition():
    try:
        m_dataStream.StartAcquisition(
            peak.AcquisitionStartMode_Default, peak.DataStream.INFINITE_NUMBER
        )
        m_node_map_remote_device.FindNode("TLParamsLocked").SetValue(1)
        m_node_map_remote_device.FindNode("AcquisitionStart").Execute()
        log_data(logging.INFO, CAMERAS[0], dict(event="acquisition_started"))
        return True
    except Exception as e:
        log_data(logging.ERROR, CAMERAS[0], dict(event="acquisition_start_error", error=str(e)))
        return False

def image_exposure_metrics(image_rgb: "ipl.Image") -> tuple[float, float]:
    width = image_rgb.Width()
    height = image_rgb.Height()
    arr = image_rgb.get_numpy_1D().reshape((height, width, 4))
    rgb = arr[:, :, :3]  # drop alpha channel
    luminance = rgb.mean(axis=2)  # simple average; swap for 0.299R+0.587G+0.114B for perceptual luma
    mean_intensity = float(luminance.mean())
    saturated_fraction = float((luminance >= 255).sum() / luminance.size)
    return mean_intensity, saturated_fraction


def acquire_image() -> "ipl.Image | None":
    m_hotpixel_correction = ipl.HotpixelCorrection()
    try:
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
        image_rgb = image.ConvertTo(ipl.PixelFormatName_RGBa8, ipl.ConversionMode_Fast)
        m_dataStream.QueueBuffer(buffer)
        return image_rgb
    except Exception as e:
        log_data(logging.ERROR, CAMERAS[0], dict(event="acquisition_failure", error=str(e)))
        return None


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

    while True:
        time.sleep(5)

        for camera in CAMERAS:
            if not camera.active:
                continue
            if not camera.min_time_between_captures_has_elapsed(dt.datetime.now()):
                continue

            image_rgb = acquire_image()
            timestamp = dt.datetime.now()
            if image_rgb is None:
                log_data(logging.ERROR, camera, dict(event="acquisition_failure"))
                continue

            mean_intensity, saturated_fraction = image_exposure_metrics(image_rgb)

            if not camera.image_should_be_stored(timestamp, mean_intensity, saturated_fraction):
                log_data(
                    logging.INFO,
                    camera,
                    dict(
                        event="image_rejected_exposure",
                        mean_intensity=round(mean_intensity, 2),
                        saturated_fraction=round(saturated_fraction, 4),
                    ),
                )
                continue

            filename = f"/home/vinni/images7/image_{timestamp:%Y%m%d%H%M%S}.jpg"
            ipl.ImageWriter.Write(filename, image_rgb)
            log_data(logging.INFO, camera, dict(event="image_saved_local", path=filename))

            s3_object_key = store.store_image_in_s3(filename, timestamp, camera.id)
            if s3_object_key is None:
                log_data(logging.ERROR, camera, dict(event="storage_failure"))
                continue

            try:
                os.remove(filename)
            except OSError:
                pass

            store.register_image_in_influxdb(timestamp, s3_object_key, camera.id)
            camera.set_last_stored_time(timestamp)
            log_data(
                logging.INFO,
                camera,
                dict(
                    event="image_stored",
                    mean_intensity=round(mean_intensity, 2),
                    saturated_fraction=round(saturated_fraction, 4),
                ),
            )


if __name__ == "__main__":
    logger.info('"event": "script_start"')
    main()