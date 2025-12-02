import cv2
import numpy as np
import os
import time
from datetime import datetime

# -----------------------------
# SETTINGS
# -----------------------------
THERMAL_CAM_INDEX = 0       # /dev/video0
BLOCK_SIZE = 24             # pixels per block for averaging
SAVE_FOLDER = "./thermal_capture"
DURATION_MINUTES = 1       # total capture duration
INTERVAL_SECONDS = 20       # capture every 1 minute
os.makedirs(SAVE_FOLDER, exist_ok=True)

# -----------------------------
# OPEN CAMERA
# -----------------------------
cap = cv2.VideoCapture(THERMAL_CAM_INDEX, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('Y','1','6',' '))
cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 160)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 120)

if not cap.isOpened():
    print("? Thermal camera not detected.")
    exit(1)

# -----------------------------
# CAPTURE LOOP WITH BUFFER FLUSH
# -----------------------------
end_time = time.time() + DURATION_MINUTES * 60

while time.time() < end_time:
    # Flush old frames (grab 5 frames quickly)
    for _ in range(5):
        cap.read()

    # Capture the "freshest" frame
    ret, frame = cap.read()
    if not ret:
        print("? Failed to capture frame, retrying...")
        time.sleep(1)
        continue

    thermal_data = frame[:, :, 0].astype(np.uint16) if frame.ndim == 3 else frame.astype(np.uint16)
    #thermal_c = (thermal_data / 100.0) - 273.15  # convert to Celsius
    thermal_c = (thermal_data - 27315) / 100
    print(thermal_data.max(), thermal_c.max()) 

    # -----------------------------
    # CREATE FALSE-COLOR IMAGE WITHOUT OVERLAY
    # -----------------------------
    disp_img = cv2.normalize(thermal_c, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    disp_img_color = cv2.applyColorMap(disp_img, cv2.COLORMAP_INFERNO)

    # -----------------------------
    # CREATE FALSE-COLOR IMAGE WITH BLOCK AVERAGE TEMPS
    # -----------------------------
    disp_img_overlay = disp_img_color.copy()
    h, w = thermal_c.shape
    for y in range(0, h, BLOCK_SIZE):
        for x in range(0, w, BLOCK_SIZE):
            block = thermal_c[y:y+BLOCK_SIZE, x:x+BLOCK_SIZE]
            avg_temp = np.round(block.mean(), 1)
            cv2.putText(
                disp_img_overlay,
                f"{avg_temp:.0f}",
                (x, y + BLOCK_SIZE),
                cv2.FONT_HERSHEY_PLAIN,
                0.7,
                (255, 255, 255),
                1
            )

    # -----------------------------
    # SAVE RESULTS
    # -----------------------------
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    npy_file = os.path.join(SAVE_FOLDER, f"{timestamp}_thermal.npy")
    png_no_overlay = os.path.join(SAVE_FOLDER, f"{timestamp}_thermal_falsecolor.png")
    png_overlay = os.path.join(SAVE_FOLDER, f"{timestamp}_thermal_falsecolor_overlay.png")

    np.save(npy_file, thermal_c)               # Celsius matrix
    cv2.imwrite(png_no_overlay, disp_img_color)   # false-color without overlay
    cv2.imwrite(png_overlay, disp_img_overlay)    # false-color with overlay

    print(f"? Saved: {npy_file}, {png_no_overlay}, {png_overlay}")

    # -----------------------------
    # WAIT UNTIL NEXT INTERVAL
    # -----------------------------
    time.sleep(INTERVAL_SECONDS)

cap.release()
print("? Capture session complete.")
