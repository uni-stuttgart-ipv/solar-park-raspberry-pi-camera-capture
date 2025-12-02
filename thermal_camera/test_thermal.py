import cv2
import time

def test_camera_formats():
    formats = [
        ('Y16', cv2.VideoWriter_fourcc('Y','1','6',' '), 0),
        ('UYVY', cv2.VideoWriter_fourcc('U','Y','V','Y'), 1),
    ]
    
    for name, fourcc, convert_rgb in formats:
        print(f"\nTesting {name}...")
        cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 160)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 120)
        cap.set(cv2.CAP_PROP_CONVERT_RGB, convert_rgb)
        
        time.sleep(0.5)
        
        for i in range(5):
            ret, frame = cap.read()
            if ret:
                print(f"  ✅ Frame {i}: shape={frame.shape}, dtype={frame.dtype}")
            else:
                print(f"  ❌ Frame {i}: failed")
            time.sleep(0.1)
        
        cap.release()

test_camera_formats()