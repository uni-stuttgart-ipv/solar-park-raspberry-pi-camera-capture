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
DURATION_MINUTES = 1        # total capture duration
INTERVAL_SECONDS = 20       # capture every 20 seconds
os.makedirs(SAVE_FOLDER, exist_ok=True)

# -----------------------------
# OPEN CAMERA - SPECIFIC TO YOUR CAMERA
# -----------------------------
print("🔍 Initializing thermal camera...")

# Try different configurations
configs = [
    # Try Y16 first (16-bit grayscale)
    {"fourcc": cv2.VideoWriter_fourcc('Y','1','6',' '), "width": 160, "height": 120, "convert_rgb": 0, "name": "Y16_160x120"},
    {"fourcc": cv2.VideoWriter_fourcc('Y','1','6',' '), "width": 80, "height": 60, "convert_rgb": 0, "name": "Y16_80x60"},
    
    # Try UYVY if Y16 fails
    {"fourcc": cv2.VideoWriter_fourcc('U','Y','V','Y'), "width": 160, "height": 120, "convert_rgb": 1, "name": "UYVY_160x120"},
    {"fourcc": cv2.VideoWriter_fourcc('U','Y','V','Y'), "width": 80, "height": 60, "convert_rgb": 1, "name": "UYVY_80x60"},
]

camera_configured = False
cap = None

for config in configs:
    if cap is not None:
        cap.release()
    
    cap = cv2.VideoCapture(THERMAL_CAM_INDEX, cv2.CAP_V4L2)
    
    # Set camera properties
    cap.set(cv2.CAP_PROP_FOURCC, config["fourcc"])
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config["height"])
    cap.set(cv2.CAP_PROP_CONVERT_RGB, config["convert_rgb"])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
    
    # Wait for camera to initialize
    time.sleep(0.5)
    
    # Test capture
    for attempt in range(5):
        ret, frame = cap.read()
        if ret and frame is not None:
            print(f"✅ Camera configured with: {config['name']}")
            print(f"   Frame shape: {frame.shape}, dtype: {frame.dtype}")
            camera_configured = True
            break
        time.sleep(0.1)
    
    if camera_configured:
        break

if not camera_configured or cap is None or not cap.isOpened():
    print("❌ Failed to initialize thermal camera")
    print("Try these manual commands:")
    print("  v4l2-ctl --set-fmt-video=width=160,height=120,pixelformat=Y16")
    print("  v4l2-ctl --set-fmt-video=width=160,height=120,pixelformat=UYVY")
    exit(1)

# -----------------------------
# TEMPERATURE CONVERSION FUNCTIONS
# -----------------------------
def process_thermal_frame(frame, format_type):
    """Convert frame to temperature data based on format"""
    
    if format_type.startswith("Y16"):
        # Y16 format - 16-bit grayscale
        if frame.ndim == 3:
            thermal_data = frame[:, :, 0].astype(np.uint16)
        else:
            thermal_data = frame.astype(np.uint16)
        
        # Convert to Celsius (adjust these values for your camera)
        # Typical Lepton conversion: (raw * 0.01) - 273.15
        thermal_c = (thermal_data * 0.01) - 273.15
        
    else:  # UYVY format
        # Convert to grayscale first
        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        
        # For UYVY, we might need different conversion
        thermal_data = gray.astype(np.uint16)
        thermal_c = (thermal_data * 0.25) - 50.0  # Adjust scaling
    
    return thermal_c, thermal_data

# -----------------------------
# CAPTURE LOOP
# -----------------------------
end_time = time.time() + DURATION_MINUTES * 60
frame_count = 0

print("🎥 Starting capture session...")

while time.time() < end_time:
    try:
        # Flush buffer
        for _ in range(3):
            cap.grab()
        
        # Capture frame
        ret, frame = cap.read()
        if not ret or frame is None:
            print("❌ Failed to capture frame")
            time.sleep(2)
            continue
        
        frame_count += 1
        
        # Process based on current configuration
        current_config = configs[configs.index([c for c in configs if c['name'] == config['name']][0])]
        thermal_c, thermal_data = process_thermal_frame(frame, current_config['name'])
        
        print(f"📸 Frame {frame_count}: shape={frame.shape}, dtype={frame.dtype}, "
              f"temp range=[{thermal_c.min():.1f}C, {thermal_c.max():.1f}C]")
        
        # Create false-color visualization
        temp_min, temp_max = thermal_c.min(), thermal_c.max()
        if temp_max > temp_min:  # Avoid division by zero
            normalized = ((thermal_c - temp_min) / (temp_max - temp_min) * 255).astype(np.uint8)
        else:
            normalized = np.zeros(thermal_c.shape, dtype=np.uint8)
        
        false_color = cv2.applyColorMap(normalized, cv2.COLORMAP_INFERNO)
        
        # Add temperature overlay (optional)
        overlay_img = false_color.copy()
        h, w = thermal_c.shape
        for y in range(0, h, BLOCK_SIZE):
            for x in range(0, w, BLOCK_SIZE):
                if y + BLOCK_SIZE <= h and x + BLOCK_SIZE <= w:
                    block = thermal_c[y:y+BLOCK_SIZE, x:x+BLOCK_SIZE]
                    avg_temp = np.mean(block)
                    cv2.putText(
                        overlay_img,
                        f"{avg_temp:.0f}",
                        (x + 2, y + BLOCK_SIZE - 2),
                        cv2.FONT_HERSHEY_PLAIN,
                        0.5,
                        (255, 255, 255),
                        1
                    )
        
        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        npy_file = os.path.join(SAVE_FOLDER, f"{timestamp}_thermal.npy")
        png_no_overlay = os.path.join(SAVE_FOLDER, f"{timestamp}_thermal_falsecolor.png")
        png_overlay = os.path.join(SAVE_FOLDER, f"{timestamp}_thermal_falsecolor_overlay.png")
        
        np.save(npy_file, thermal_c)
        cv2.imwrite(png_no_overlay, false_color)
        cv2.imwrite(png_overlay, overlay_img)
        
        print(f"💾 Saved: {timestamp}_thermal.*")
        
        # Wait for next interval
        time.sleep(INTERVAL_SECONDS)
        
    except Exception as e:
        print(f"❌ Error in capture loop: {e}")
        time.sleep(2)

cap.release()
print(f"✅ Capture session complete. Captured {frame_count} frames.")