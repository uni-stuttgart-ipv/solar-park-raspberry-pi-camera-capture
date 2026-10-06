import os
import posixpath
import logging
import datetime as dt
import boto3
from botocore.exceptions import ClientError, EndpointConnectionError
import influxdb_client_3 as influx
from influxdb_client_3.exceptions.exceptions import InfluxDBError

S3_BUCKET = os.getenv("S3_BUCKET_NAME")
S3_PREFIX = os.getenv("S3_PREFIX")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_DATA_FILE_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"

INFLUXDB_HOST = "https://eu-central-1-1.aws.cloud2.influxdata.com"
INFLUXDB_DATABASE = "solar_park_data"
INFLUXDB_TOKEN_ENV_KEY = "SOLAR_PARK_SPECTRA_INFLUXDB_TOKEN"
INFLUXDB_MEASUREMENT_NAME = "module_rgb_image"
INFLUXDB_CAMERA_TAG_NAME = "camera"
INFLUXDB_CAMERA_BUCKET_FIELD_NAME = "s3_bucket"
INFLUXDB_CAMERA_FILE_PATH_FIELD_NAME = "s3_object_key"

logger = logging.getLogger(__name__)

def log_data(level: int, data: dict):
    msg_data = []
    for key, value in data.items():
        msg_data.append(f'"{key}": "{value}"')

    msg = ", ".join(msg_data)
    logger.log(level, msg)

def store_image_in_s3(local_path: str, timestamp: dt.datetime, camera_id: str) -> str | None:
    if AWS_ACCESS_KEY_ID is None:
        raise RuntimeError("environment variable AWS_ACCESS_KEY_ID is not set")
    if AWS_SECRET_ACCESS_KEY is None:
        raise RuntimeError("environment variable AWS_SECRET_ACCESS_KEY is not set")
    if S3_BUCKET is None:
        raise RuntimeError("environment variable S3_BUCKET is not set")
    if S3_PREFIX is None:
        raise RuntimeError("environment variable S3_PREFIX is not set")

    s3 = boto3.client("s3", aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)

    timestamp_str = timestamp.strftime(AWS_DATA_FILE_TIMESTAMP_FORMAT)
    filename = f"{timestamp_str}.jpeg"
    object_key = posixpath.join(S3_PREFIX, camera_id, filename)
 
    try:
        s3.upload_file(
            local_path,
            S3_BUCKET,
            object_key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
    except EndpointConnectionError as e:
        log_data(logging.ERROR, dict(event="s3_storage_failure", error=e))
        return None
    except ClientError as e:
        log_data(logging.ERROR, dict(event="s3_storage_failure", error=e))
        return None
    except Exception as e:
        log_data(logging.ERROR, dict(event="s3_storage_failure", error=e))
        return None

    return object_key


def influx_success(self, data: bytes):
    data_str = data.decode()
    data_str = data_str.replace('"', '\\"')
    log_data(logging.INFO, dict(event="influxdb_registration_success", data=data_str))


def influx_error(
    self,
    data: str,
    exception: InfluxDBError,
):
    """Log influx write error.

    Args:
        data (str): Data trying to be written.
        exception (influx.InfluxDBError): Error that ocurred.
    """
    log_data(
        logging.ERROR,
        dict(event="influxdb_write_failure", config=self, data=data, cause=exception),
    )

def influx_retry(self, data: str, exception: InfluxDBError):
    log_data(
        logging.DEBUG,
        dict(event="influxdb_retry", config=self, data=data, cause=exception),
    )


def register_image_in_influxdb(timestamp: dt.datetime, s3_object_key: str, camera_id: str):
    """ 
    Args:
        timestamp (dt.datetime): Timestamp associated with the capture.
        s3_object_key (str): S3 object key (object path) to the image file.
        camera_id (str): Name of the camera.
 
    Raises:
        RuntimeError: Required environment variables are not set.
    """
    point = (
        influx.Point(INFLUXDB_MEASUREMENT_NAME)
        .time(timestamp, write_precision=influx.WritePrecision.S)
        .tag(INFLUXDB_CAMERA_TAG_NAME, camera_id)
        .field(INFLUXDB_CAMERA_BUCKET_FIELD_NAME, S3_BUCKET)
        .field(INFLUXDB_CAMERA_FILE_PATH_FIELD_NAME, s3_object_key)
    )

    access_token = os.getenv(INFLUXDB_TOKEN_ENV_KEY)
    if access_token is None:
        raise RuntimeError(f"environment variable {INFLUXDB_TOKEN_ENV_KEY} is not set")

    write_options = influx.WriteOptions(
        flush_interval=10_000,
        jitter_interval=2_000,
        retry_interval=5_000,
        max_retries=5,
        max_retry_delay=30_000,
        exponential_base=2,
    )

    options = influx.write_client_options(
        success_callback=influx_success,
        error_callback=influx_error,
        retry_callback=influx_retry,
        write_options=write_options,
    )

    with influx.InfluxDBClient3(
        host=INFLUXDB_HOST,
        token=access_token,
        database=INFLUXDB_DATABASE,
        write_client_options=options,
    ) as client:
        client.write(point)