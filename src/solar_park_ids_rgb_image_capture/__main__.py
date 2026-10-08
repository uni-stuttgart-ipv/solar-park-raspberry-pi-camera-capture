import os
import time
import logging
import tempfile
import datetime as dt
from dataclasses import dataclass

from ids_peak import ids_peak as peak
from ids_peak_ipl import ids_peak_ipl as ipl
from . import store, LOG_LEVEL_ENV_KEY, LOG_LEVEL_DEFAULT

LOG_FILE = "ipv_solar_park_rgb_camera_data_pipeline.log"
logger = logging.getLogger(__name__)
logging.basicConfig(
    filename=LOG_FILE,
    encoding="utf-8",
    level=LOG_LEVEL_DEFAULT,
    format='{"time"="%(asctime)s", %(message)s}',
    datefmt="%Y-%m-%d %H:%M:%S",
)

CAMERA_ID = "ids_rgb_cam"

CAMERA_BRIGHTNESS_MIN = 15
CAMERA_BRIGHTNESS_MAX = 240
CAMERA_MAX_SATURATED_FRACTION = 0.15
# Minimum time between stored images.
CAMERA_CAPTURE_FREQUENCY_SEC = 5 * 60
# Maximum time between stored images.
CAMERA_MAX_TIME_BETWEEN_CAPTURES_SEC = 10 * 60

# Time between iterations of the main loop.
MAIN_LOOP_SLEEP_SEC = 1 * 60

CAMERA_NODE_SETTINGS: list[tuple[str, str | bool | int | float]] = [
    ("ExposureAuto", "Continuous"),
    ("GainAuto", "Continuous"),
    ("BalanceWhiteAuto", "Continuous"),
    ("AcquisitionMode", "Continuous"),
    ("AcquisitionFrameRateTargetEnable", True),
    ("AcquisitionFrameRateTarget", 20),
    ("ColorCorrectionMatrix", "HQ"),
    ("LUTEnable", False),
    ("Gamma", 2.2),
    ("BrightnessAutoTarget", 150),
    ("ComponentSelector", "Intensity"),
    ("PixelFormat", "BayerRG8"),
    ("BlackLevel", 0.31),
]

def log_data(level: int, camera_id: str, data: dict) -> None:
    msg = f'"camera": "{camera_id}"'
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


@dataclass
class CameraDevice:
    device: peak.Device
    data_stream: peak.DataStream
    node_map: peak.NodeMap

class Camera:
    def __init__(self, id: str, measurement_config: MeasurementConfig):
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

    def is_capture_overdue(self, time: dt.datetime) -> bool:
        """Whether more than the maximum period has passed since the last stored image."""
        if self._last_stored_time is None:
            return False

        elapsed = time - self._last_stored_time
        overdue = (
            elapsed.total_seconds()
            >= self.measurement_config.max_period_between_captures_sec
        )
        log_data(
            logging.DEBUG,
            self.id,
            dict(event="capture_validation", property="max_period_between_captures_sec", value=overdue,),
            )
        return overdue

    def is_bright_enough(self, mean_intensity: float) -> bool:
        passed = mean_intensity >= self.measurement_config.brightness_min
        log_data(
            logging.DEBUG,
            self.id, 
            dict(event="capture_validation", property="is_bright_enough", value=passed),
            )
        return passed

    def is_not_overexposed(self, mean_intensity: float) -> bool:
        passed = mean_intensity <= self.measurement_config.brightness_max
        log_data(
            logging.DEBUG,
            self.id,
            dict(event="capture_validation", property="is_not_overexposed", value=passed),
            )
        return passed

    def is_not_saturated(self, saturated_fraction: float) -> bool:
        passed = saturated_fraction <= self.measurement_config.max_saturated_fraction
        log_data(
            logging.DEBUG,
            self.id,
            dict(event="capture_validation", property="is_not_saturated", value=passed),
            )
        return passed

    def image_should_be_stored(self, time: dt.datetime, mean_intensity: float, saturated_fraction: float) -> bool:
        # Once the maximum period has passed, store the image regardless of exposure.
        if self.is_capture_overdue(time):
            return True
        if not self.is_bright_enough(mean_intensity):
            return False
        if not self.is_not_overexposed(mean_intensity):
            return False
        if not self.is_not_saturated(saturated_fraction):
            return False

        return True

    def set_last_stored_time(self, time: dt.datetime) -> None:
        self._last_stored_time = time


CAMERA = Camera(
    CAMERA_ID,
    MeasurementConfig(
        CAMERA_BRIGHTNESS_MIN,
        CAMERA_BRIGHTNESS_MAX,
        CAMERA_MAX_SATURATED_FRACTION,
        CAMERA_CAPTURE_FREQUENCY_SEC,
        CAMERA_MAX_TIME_BETWEEN_CAPTURES_SEC,
    ),
)

def set_log_level() -> None:
    """Set the log level from the environment variable."""
    log_level = os.getenv(LOG_LEVEL_ENV_KEY)
    if log_level is None:
        return

    level: int | None = getattr(logging, log_level.upper(), None)
    if not isinstance(level, int):
        logger.warning(f"invalid log level: {log_level}")
        return

    logger.setLevel(level)

def open_camera() -> CameraDevice | None:
    """
    Find and open the camera, its remote node map and its data stream.
    Returns:
        CameraDevice | None: The opened camera. `None` if any step failed.
    Raises:
        RuntimeError: More than one camera is connected.
    """
    try:
        device_manager = peak.DeviceManager.Instance()
        device_manager.Update()
        camera_list = device_manager.Devices()
        count = camera_list.size()
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="camera_search_error", error=str(e)))
        return None

    if count == 0:
        log_data(logging.ERROR, CAMERA_ID, dict(event="camera_not_found"))
        return None

    # TODO: extend to support multiple cameras.
    if count > 1:
        raise RuntimeError(f"expected 1 camera, but found {count}")

    camera = camera_list[0]

    try:
        openable = camera.IsOpenable()
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="camera_openable_check_error", error=str(e)))
        return None

    if not openable:
        log_data(logging.ERROR, CAMERA_ID, dict(event="camera_not_openable"))
        return None

    try:
        device = camera.OpenDevice(peak.DeviceAccessType_Control)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="camera_open_error", error=str(e)))
        return None
    
    try:
        node_map = device.RemoteDevice().NodeMaps()[0]
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="node_map_error", error=str(e)))
        return None

    try:
        data_streams = device.DataStreams()
        no_data_streams = data_streams.empty()
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="data_streams_error", error=str(e)))
        return None

    if no_data_streams:
        log_data(logging.ERROR, CAMERA_ID, dict(event="data_stream_not_found"))
        return None

    try:
        data_stream = data_streams[0].OpenDataStream()
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="data_stream_open_error", error=str(e)))
        return None

    return CameraDevice(device, data_stream, node_map)


def find_node(node_map: peak.NodeMap, name: str) -> peak.Node | None:
    try:
        return node_map.FindNode(name)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="node_find_error", node=name, error=str(e)))
        return None

def set_node(
    node_map: peak.NodeMap, name: str, value: str | bool | int | float
) -> bool:
    node = find_node(node_map, name)
    if node is None:
        return False

    try:
        if isinstance(value, str):
            node.SetCurrentEntry(value)
        else:
            node.SetValue(value)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="node_set_error", node=name, value=value, error=str(e)))
        return False

    return True


def configure_camera(camera_device: CameraDevice) -> bool:
    """ROI and CAMERA_NODE_SETTINGS"""
    node_map = camera_device.node_map

    width_node = find_node(node_map, "Width")
    height_node = find_node(node_map, "Height")
    if width_node is None or height_node is None:
        return False

    try:
        width = width_node.Maximum()
        height = height_node.Maximum()
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="roi_limits_error", error=str(e)))
        return False

    roi = dict(OffsetX=0, OffsetY=0, Width=width, Height=height)
    for name, value in roi.items():
        if not set_node(node_map, name, value):
            return False

    all_applied = True
    for name, value in CAMERA_NODE_SETTINGS:
        if not set_node(node_map, name, value):
            all_applied = False

    return all_applied


def queue_buffer(data_stream: peak.DataStream, buffer: peak.Buffer) -> bool:
    try:
        data_stream.QueueBuffer(buffer)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_queue_error", error=str(e)))
        return False
    return True


def alloc_and_announce_buffers(camera_device: CameraDevice) -> bool:
    data_stream = camera_device.data_stream

    try:
        data_stream.Flush(peak.DataStreamFlushMode_DiscardAll)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_flush_error", error=str(e)))
        return False

    try:
        announced = list(data_stream.AnnouncedBuffers())
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_list_error", error=str(e)))
        return False

    for buffer in announced:
        try:
            data_stream.RevokeBuffer(buffer)
        except Exception as e:
            log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_revoke_error", error=str(e)))
            return False

    payload_node = find_node(camera_device.node_map, "PayloadSize")
    if payload_node is None:
        return False

    try:
        payload_size = payload_node.Value()
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="payload_size_error", error=str(e)))
        return False

    try:
        num_buffers = data_stream.NumBuffersAnnouncedMinRequired()
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_count_error", error=str(e)))
        return False

    for _ in range(num_buffers):
        try:
            buffer = data_stream.AllocAndAnnounceBuffer(payload_size)
        except Exception as e:
            log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_alloc_error", error=str(e)))
            return False

        if not queue_buffer(data_stream, buffer):
            return False

    return True


def start_acquisition(camera_device: CameraDevice) -> bool:
    try:
        camera_device.data_stream.StartAcquisition(peak.AcquisitionStartMode_Default, peak.DataStream.INFINITE_NUMBER)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="stream_acquisition_start_error", error=str(e)))
        return False

    if not set_node(camera_device.node_map, "TLParamsLocked", 1):
        return False

    start_node = find_node(camera_device.node_map, "AcquisitionStart")
    if start_node is None:
        return False

    try:
        start_node.Execute()
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="acquisition_start_error", error=str(e)))
        return False

    log_data(logging.INFO, CAMERA_ID, dict(event="acquisition_started"))
    return True


def process_buffer(buffer: peak.Buffer, hotpixel_correction: ipl.HotpixelCorrection) -> ipl.Image | None:
    try:
        image = ipl.Image.CreateFromSizeAndBuffer(
            buffer.PixelFormat(),
            buffer.BasePtr(),
            buffer.Size(),
            buffer.Width(),
            buffer.Height(),
        )
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="image_create_error", error=str(e)))
        return None

    try:
        hotpixels = hotpixel_correction.Detect(image)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="hotpixel_detect_error", error=str(e)))
        return None

    try:
        image = hotpixel_correction.Correct(image, hotpixels)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="hotpixel_correct_error", error=str(e)))
        return None

    try:
        return image.ConvertTo(ipl.PixelFormatName_RGBa8, ipl.ConversionMode_Fast)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="image_convert_error", error=str(e)))
        return None


def acquire_image(camera_device: CameraDevice, hotpixel_correction: ipl.HotpixelCorrection) -> ipl.Image | None:
    data_stream = camera_device.data_stream

    try:
        data_stream.Flush(peak.DataStreamFlushMode_DiscardAll)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_flush_error", error=str(e)))
        return None

    try:
        announced = list(data_stream.AnnouncedBuffers())
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_list_error", error=str(e)))
        return None

    for announced_buffer in announced:
        if not queue_buffer(data_stream, announced_buffer):
            return None

    try:
        buffer = data_stream.WaitForFinishedBuffer(5000)
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="buffer_wait_error", error=str(e)))
        return None

    try:
        return process_buffer(buffer, hotpixel_correction)
    finally:
        queue_buffer(data_stream, buffer)

def image_exposure_metrics(image_rgb: ipl.Image) -> tuple[float, float] | None:
    try:
        width = image_rgb.Width()
        height = image_rgb.Height()
        arr = image_rgb.get_numpy_1D().reshape((height, width, 4))
    except Exception as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="image_to_numpy_error", error=str(e)))
        return None

    rgb = arr[:, :, :3] 
    luminance = rgb.mean(axis=2)
    mean_intensity = float(luminance.mean())
    saturated_fraction = float((luminance >= 255).sum() / luminance.size)
    return mean_intensity, saturated_fraction


def process_capture(camera: Camera, camera_device: CameraDevice, hotpixel_correction: ipl.HotpixelCorrection,) -> bool:
    image_rgb = acquire_image(camera_device, hotpixel_correction)
    timestamp = dt.datetime.now()
    if image_rgb is None:
        log_data(logging.ERROR, camera.id, dict(event="acquisition_failure"))
        return False

    metrics = image_exposure_metrics(image_rgb)
    if metrics is None:
        log_data(logging.ERROR, camera.id, dict(event="exposure_metrics_failure"))
        return False
    mean_intensity, saturated_fraction = metrics

    if not camera.image_should_be_stored(timestamp, mean_intensity, saturated_fraction):
        log_data(
            logging.INFO,
            camera.id,
            dict(
                event="image_rejected_exposure",
                mean_intensity=round(mean_intensity, 2),
                saturated_fraction=round(saturated_fraction, 4),
            ),
        )
        return False

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete_on_close=False) as f:
        try:
            ipl.ImageWriter.Write(f.name, image_rgb)
        except Exception as e:
            log_data(logging.ERROR, camera.id, dict(event="image_write_error", path=f.name, error=str(e)))
            return False
        log_data(logging.INFO, camera.id, dict(event="image_saved_local", path=f.name))

        s3_object_key = store.store_image_in_s3(f.name, timestamp, camera.id)

    if s3_object_key is None:
        log_data(logging.ERROR, camera.id, dict(event="storage_failure"))
        return False

    if not store.register_image_in_influxdb(timestamp, s3_object_key, camera.id):
        log_data(logging.ERROR, camera.id, dict(event="registration_failure", s3_object_key=s3_object_key))

    camera.set_last_stored_time(timestamp)
    log_data(logging.INFO, 
             camera.id,
             dict(event="image_stored", mean_intensity=round(mean_intensity, 2), saturated_fraction=round(saturated_fraction, 4)),
             )
    return True

def main() -> None:
    peak.Library.Initialize()
    camera_device = open_camera()
    if camera_device is None:
        raise RuntimeError("could not open camera")
    if not configure_camera(camera_device):
        raise RuntimeError("could not configure camera")
    if not alloc_and_announce_buffers(camera_device):
        raise RuntimeError("could not allocate buffers")
    if not start_acquisition(camera_device):
        raise RuntimeError("could not start acquisition")

    hotpixel_correction = ipl.HotpixelCorrection()

    while True:
        set_log_level()
        time.sleep(MAIN_LOOP_SLEEP_SEC)

        if not CAMERA.min_time_between_captures_has_elapsed(dt.datetime.now()):
            continue

        process_capture(CAMERA, camera_device, hotpixel_correction)


if __name__ == "__main__":
    logger.info('"event": "script_start"')
    main()