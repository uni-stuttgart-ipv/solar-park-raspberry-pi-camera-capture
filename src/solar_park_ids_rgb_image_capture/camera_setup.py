from dataclasses import dataclass

from ids_peak import ids_peak as peak

CAMERA_NODE_SETTINGS: dict[str, str | bool | int | float] = {
    "ExposureAuto": "Continuous",
    "GainAuto": "Continuous",
    "BalanceWhiteAuto": "Continuous",
    "AcquisitionMode": "Continuous",
    "AcquisitionFrameRateTargetEnable": True,
    "AcquisitionFrameRateTarget": 20,
    "ColorCorrectionMatrix": "HQ",
    "LUTEnable": False,
    "Gamma": 2.2,
    "BrightnessAutoTarget": 150,
    "ComponentSelector": "Intensity",
    "PixelFormat": "BayerRG8",
    "BlackLevel": 0.31
    }

@dataclass
class CameraDevice:
    device: peak.Device
    data_stream: peak.DataStream
    node_map: peak.NodeMap

def open_camera() -> CameraDevice:
    """Find and open the camera, its remote node map and its data stream.

    Raises:
        RuntimeError: No camera was found, more than one was found, or a step failed.

    Returns:
        CameraDevice
    """
    try:
        device_manager = peak.DeviceManager.Instance()
    except Exception as e:
        raise RuntimeError(f"failed to access DeviceManager instance: {e}") from e

    try:
        device_manager.Update()
    except Exception as e:
        raise RuntimeError(f"port scan failed: {e}") from e

    try:
        descriptors = device_manager.Devices()
        count = descriptors.size()
    except Exception as e:
        raise RuntimeError(f"failed to read or count devices: {e}") from e

    if count == 0:
        raise RuntimeError("camera not found")

    # TODO: extend to support multiple cameras.
    if count > 1:
        raise RuntimeError(f"expected 1 camera, but found {count}")

    descriptor = descriptors[0]

    try:
        openable = descriptor.IsOpenable()
    except Exception as e:
        raise RuntimeError(f"camera openable check failed: {e}") from e

    if not openable:
        raise RuntimeError("camera is not openable")

    try:
        device = descriptor.OpenDevice(peak.DeviceAccessType_Control)
    except Exception as e:
        raise RuntimeError(f"camera open error: {e}") from e

    try:
        node_map = device.RemoteDevice().NodeMaps()[0]
    except Exception as e:
        raise RuntimeError(f"node map failed: {e}") from e

    try:
        data_streams = device.DataStreams()
        no_data_streams = data_streams.empty()
    except Exception as e:
        raise RuntimeError(f"data streams failed: {e}") from e

    if no_data_streams:
        raise RuntimeError("data stream not found")

    try:
        data_stream = data_streams[0].OpenDataStream()
    except Exception as e:
        raise RuntimeError(f"data stream open failed: {e}") from e

    return CameraDevice(device, data_stream, node_map)

def find_node(node_map: peak.NodeMap, name: str) -> peak.Node:
    try:
        return node_map.FindNode(name)
    except Exception as e:
        raise RuntimeError(f"node {name} not found: {e}") from e

def set_node(node_map: peak.NodeMap, name: str, value: str | bool | int | float):
    node = find_node(node_map, name)
    try:
        if isinstance(value, str):
            node.SetCurrentEntry(value)
        else:
            node.SetValue(value)
    except Exception as e:
        raise RuntimeError(f"could not set node {name} to {value}: {e}") from e

def configure_camera(camera_device: CameraDevice):
    node_map = camera_device.node_map
    width_node = find_node(node_map, "Width")
    height_node = find_node(node_map, "Height")

    try:
        width = width_node.Maximum()
        height = height_node.Maximum()
    except Exception as e:
        raise RuntimeError(f"could not read sensor size: {e}") from e

    roi = dict(OffsetX=0, OffsetY=0, Width=width, Height=height)
    for name, value in roi.items():
        set_node(node_map, name, value)

    failures = []
    for name, value in CAMERA_NODE_SETTINGS.items():
        try:
            set_node(node_map, name, value)
        except RuntimeError as e:
            failures.append(str(e))

    if failures:
        raise RuntimeError("; ".join(failures))

def queue_buffer(data_stream: peak.DataStream, buffer: peak.Buffer):
    try:
        data_stream.QueueBuffer(buffer)
    except Exception as e:
        raise RuntimeError(f"buffer queue failed: {e}") from e

def alloc_and_announce_buffers(camera_device: CameraDevice):
    data_stream = camera_device.data_stream

    try:
        data_stream.Flush(peak.DataStreamFlushMode_DiscardAll)
    except Exception as e:
        raise RuntimeError(f"buffer flush failed: {e}") from e

    try:
        announced = list(data_stream.AnnouncedBuffers())
    except Exception as e:
        raise RuntimeError(f"buffer list failed: {e}") from e

    for buffer in announced:
        try:
            data_stream.RevokeBuffer(buffer)
        except Exception as e:
            raise RuntimeError(f"buffer revoke failed: {e}") from e

    payload_node = find_node(camera_device.node_map, "PayloadSize")

    try:
        payload_size = payload_node.Value()
    except Exception as e:
        raise RuntimeError(f"payload size failed: {e}") from e

    try:
        num_buffers = data_stream.NumBuffersAnnouncedMinRequired()
    except Exception as e:
        raise RuntimeError(f"buffer count failed: {e}") from e

    for _ in range(num_buffers):
        try:
            buffer = data_stream.AllocAndAnnounceBuffer(payload_size)
        except Exception as e:
            raise RuntimeError(f"buffer alloc failed: {e}") from e

        queue_buffer(data_stream, buffer)

def start_acquisition(camera_device: CameraDevice):
    try:
        camera_device.data_stream.StartAcquisition(peak.AcquisitionStartMode_Default, peak.DataStream.INFINITE_NUMBER)
    except Exception as e:
        raise RuntimeError(f"stream acquisition start failed: {e}") from e

    set_node(camera_device.node_map, "TLParamsLocked", 1)
    start_node = find_node(camera_device.node_map, "AcquisitionStart")

    try:
        start_node.Execute()
    except Exception as e:
        raise RuntimeError(f"acquisition start failed: {e}") from e

def setup_camera() -> CameraDevice:
    camera_device = open_camera()
    configure_camera(camera_device)
    alloc_and_announce_buffers(camera_device)
    start_acquisition(camera_device)
    return camera_device