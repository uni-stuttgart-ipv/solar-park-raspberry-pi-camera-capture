import os
import time
import logging
import tempfile
import datetime as dt
from dataclasses import dataclass

from ids_peak import ids_peak as peak
from ids_peak_ipl import ids_peak_ipl as ipl
from . import store, LOG_LEVEL_ENV_KEY, LOG_LEVEL_DEFAULT
from .camera_setup import CameraDevice, queue_buffer, setup_camera

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
CAMERA_MIN_CAPTURE_INTERVAL_SEC = 1 * 60
# Maximum time between stored images.
CAMERA_MAX_CAPTURE_INTERVAL_SEC = 2 * 60

# Time between iterations of the main loop.
MAIN_LOOP_SLEEP_SEC = 10

def log_data(level: int, camera_id: str, data: dict):
    msg = f'"camera": "{camera_id}"'
    for key, value in data.items():
        msg += f', "{key}": "{value}"'
    logger.log(level, msg)

@dataclass
class MeasurementConfig:
    brightness_min: float
    brightness_max: float
    max_saturated_fraction: float
    min_capture_interval_sec: int
    max_capture_interval_sec: int

MEASUREMENT_CONFIG = MeasurementConfig(
    CAMERA_BRIGHTNESS_MIN,
    CAMERA_BRIGHTNESS_MAX,
    CAMERA_MAX_SATURATED_FRACTION,
    CAMERA_MIN_CAPTURE_INTERVAL_SEC,
    CAMERA_MAX_CAPTURE_INTERVAL_SEC,
)

def is_capture_due(time: dt.datetime, last_stored_time: dt.datetime | None, config: MeasurementConfig) -> bool:
    if last_stored_time is None:
        return True

    elapsed = time - last_stored_time
    return elapsed.total_seconds() >= config.min_capture_interval_sec

def is_capture_overdue(time: dt.datetime, last_stored_time: dt.datetime | None, config: MeasurementConfig) -> bool:
    if last_stored_time is None:
        return False

    elapsed = time - last_stored_time
    overdue = elapsed.total_seconds() >= config.max_capture_interval_sec
    log_data(
        logging.DEBUG,
        CAMERA_ID,
        dict(event="capture_validation", property="max_capture_interval_sec", value=overdue),
        )
    return overdue

def is_bright_enough(mean_intensity: float, config: MeasurementConfig) -> bool:
    passed = mean_intensity >= config.brightness_min
    log_data(
        logging.DEBUG,
        CAMERA_ID,
        dict(event="capture_validation", property="is_bright_enough", value=passed),
        )
    return passed

def is_not_overexposed(mean_intensity: float, config: MeasurementConfig) -> bool:
    passed = mean_intensity <= config.brightness_max
    log_data(
        logging.DEBUG,
        CAMERA_ID,
        dict(event="capture_validation", property="is_not_overexposed", value=passed),
        )
    return passed

def is_not_saturated(saturated_fraction: float, config: MeasurementConfig) -> bool:
    passed = saturated_fraction <= config.max_saturated_fraction
    log_data(
        logging.DEBUG,
        CAMERA_ID,
        dict(event="capture_validation", property="is_not_saturated", value=passed),
        )
    return passed

def image_should_be_stored(time: dt.datetime, last_stored_time: dt.datetime | None, mean_intensity: float, saturated_fraction: float, config: MeasurementConfig) -> bool:
    # Once the maximum period has passed, store the image regardless of exposure.
    if is_capture_overdue(time, last_stored_time, config):
        return True
    if not is_bright_enough(mean_intensity, config):
        return False
    if not is_not_overexposed(mean_intensity, config):
        return False
    if not is_not_saturated(saturated_fraction, config):
        return False

    return True

def set_log_level():
    """Set the log level from the environment variable."""
    log_level = os.getenv(LOG_LEVEL_ENV_KEY)
    if log_level is None:
        return

    level: int | None = getattr(logging, log_level.upper(), None)
    if not isinstance(level, int):
        logger.warning(f"invalid log level: {log_level}")
        return

    logger.setLevel(level)

def buffer_to_rgb_image(buffer: peak.Buffer, hotpixel_correction: ipl.HotpixelCorrection) -> ipl.Image:
    try:
        image = ipl.Image.CreateFromSizeAndBuffer(
            buffer.PixelFormat(),
            buffer.BasePtr(),
            buffer.Size(),
            buffer.Width(),
            buffer.Height(),
            )
    except Exception as e:
        raise RuntimeError(f"image create failed: {e}") from e

    try:
        hotpixels = hotpixel_correction.Detect(image)
    except Exception as e:
        raise RuntimeError(f"hotpixel detect failed: {e}") from e

    try:
        image = hotpixel_correction.Correct(image, hotpixels)
    except Exception as e:
        raise RuntimeError(f"hotpixel correct failed: {e}") from e

    try:
        return image.ConvertTo(ipl.PixelFormatName_RGBa8, ipl.ConversionMode_Fast)
    except Exception as e:
        raise RuntimeError(f"image convert failed: {e}") from e

def acquire_image(camera_device: CameraDevice, hotpixel_correction: ipl.HotpixelCorrection) -> ipl.Image:
    data_stream = camera_device.data_stream

    try:
        buffer = data_stream.WaitForFinishedBuffer(5000)
    except Exception as e:
        raise RuntimeError(f"buffer wait failed: {e}") from e

    try:
        image_rgb = buffer_to_rgb_image(buffer, hotpixel_correction)
    except RuntimeError as e:
        try:
            queue_buffer(data_stream, buffer)
        except RuntimeError as queue_error:
            raise RuntimeError(f"{e}; {queue_error}") from e
        raise

    queue_buffer(data_stream, buffer)
    return image_rgb

def measure_exposure(image_rgb: ipl.Image) -> tuple[float, float]:
    try:
        width = image_rgb.Width()
        height = image_rgb.Height()
        arr = image_rgb.get_numpy_1D().reshape((height, width, 4))
    except Exception as e:
        raise RuntimeError(f"image to numpy failed: {e}") from e

    rgb = arr[:, :, :3]
    luminance = rgb.mean(axis=2)
    mean_intensity = float(luminance.mean())
    saturated_fraction = float((luminance >= 255).sum() / luminance.size)
    return mean_intensity, saturated_fraction

def main():
    peak.Library.Initialize()

    try:
        camera_device = setup_camera()
    except RuntimeError as e:
        log_data(logging.ERROR, CAMERA_ID, dict(event="camera_setup_failure", error=str(e)))
        raise

    log_data(logging.INFO, CAMERA_ID, dict(event="acquisition_started"))

    hotpixel_correction = ipl.HotpixelCorrection()
    last_stored_time: dt.datetime | None = None

    while True:
        set_log_level()
        time.sleep(MAIN_LOOP_SLEEP_SEC)

        now = dt.datetime.now()
        if not is_capture_due(now, last_stored_time, MEASUREMENT_CONFIG):
            continue

        try:
            image_rgb = acquire_image(camera_device, hotpixel_correction)
            mean_intensity, saturated_fraction = measure_exposure(image_rgb)
        except RuntimeError as e:
            log_data(logging.ERROR, CAMERA_ID, dict(event="capture_failure", error=str(e)))
            continue

        if not image_should_be_stored(now, last_stored_time, mean_intensity, saturated_fraction, MEASUREMENT_CONFIG):
            log_data(
                logging.INFO,
                CAMERA_ID,
                dict(event="image_rejected_exposure", mean_intensity=round(mean_intensity, 2), saturated_fraction=round(saturated_fraction, 4)),
                )
            continue

        with tempfile.TemporaryDirectory(suffix=".jpg") as f:
            try:
                ipl.ImageWriter.Write(f.name, image_rgb)
            except Exception as e:
                log_data(logging.ERROR, CAMERA_ID, dict(event="image_write_error", path=f.name, error=str(e)))
                continue
            log_data(logging.INFO, CAMERA_ID, dict(event="image_saved_local", path=f.name))

            s3_object_key = store.store_image_in_s3(f.name, now, CAMERA_ID)

        if s3_object_key is None:
            log_data(logging.ERROR, CAMERA_ID, dict(event="storage_failure"))
            continue

        store.register_image_in_influxdb(now, s3_object_key, CAMERA_ID)

        last_stored_time = now
        log_data(
            logging.INFO,
            CAMERA_ID,
            dict(event="image_stored", mean_intensity=round(mean_intensity, 2), saturated_fraction=round(saturated_fraction, 4)),
            )


if __name__ == "__main__":
    logger.info('"event": "script_start"')
    main()