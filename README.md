# Raspberry Pi / IDS Camera Capture for Perovskite Solar Panel Imaging
This project provides a **Python-based camera capture pipeline** for IDS industrial cameras using the IDS Peak SDK. It captures RGB images, applies hot-pixel correction, saves them locally, and optionally uploads them to **AWS S3**.

## 📌 Features
- Auto-detect and open an IDS Peak camera  
- Configure ROI, exposure, gain, white balance, and frame rate  
- Hot-pixel correction using IDS Peak IPL  
- Save RGB images to local storage  
- Optional upload to Amazon S3 (timestamp-based folder structure)  
- Fully automated acquisition loop  

## 📁 Project Structure
thermal_camera/  
thermal_capture/  
ids_peak/  
display.py  
mainwindow.py  
opencamera.py  # Main camera capture script  
simple_live_qtwidgets.py  
README.md  

## 🖥️ Installation Requirements

### 1️⃣ Install Visual Studio Build Tools (Windows Only)
Required for IDS Peak Python bindings.  
Download: https://visualstudio.microsoft.com/visual-cpp-build-tools/  
Enable components:
- Desktop development with C++  
- MSVC compiler toolset  
- Windows 10/11 SDK  

### 2️⃣ Install IDS Peak SDK
Download: https://en.ids-imaging.com/ids-peak-sdk.html  
Enable during installation:
- Peak Viewer  
- Python bindings (`ids_peak`, `ids_peak_ipl`)  
- GenICam USB support  

Verify installation:
python -c "import ids_peak"  
python -c "import ids_peak_ipl"  

### 3️⃣ Install Python 3.10+
Download: https://www.python.org/downloads/  
Enable:  
[✔] Add Python to PATH  

### 4️⃣ Install Python Dependencies
pip install boto3 python-dotenv  

## 🌩️ AWS S3 Configuration (Optional)
Create a `.env` file:
AWS_ACCESS_KEY_ID=YOUR_KEY  
AWS_SECRET_ACCESS_KEY=YOUR_SECRET  
AWS_REGION=eu-central-1  
S3_BUCKET_NAME=s3-solar-park-module-images-raw  
S3_PREFIX=rgb/raw/  

If empty, uploads will be skipped.

## 📸 Running the Camera Capture Script
Run:
python opencamera.py  

The script will:
1. Initialize the IDS camera  
2. Prepare the data stream  
3. Set ROI to full resolution  
4. Start continuous acquisition  
5. Capture 2 images (default)  
6. Save them to:  
/home/vinni/images7/  
7. Upload them to S3 if enabled  

Example output:
Acquisition started successfully.  
[1/2] Saved: /home/vinni/images7/image_000.jpg  
Uploaded to s3://bucket/YYYY/MM/DD/timestamp_000000.jpg  

## 🔧 Changing Number of Images
In `opencamera.py`:
def save_image(num_images=2):  

For 10 images:
save_image(num_images=10)  

## 📁 Changing Save Directory
Modify:
filename = f"/home/vinni/images7/image_{i:03}.jpg"  

Example:
filename = f"/home/pi/captured_images/image_{i:03}.jpg"  

## 🚀 Notes
- Test camera in **IDS Peak Viewer** before running Python  
- Use correct SDK version for Windows/Linux/Raspberry Pi  
- For AWS uploads, IAM must allow `s3:PutObject` and `s3:ListBucket`  

## 📧 Support
For help, open a GitHub issue in this repository.
