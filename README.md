# <img src="assets/capcap.png" style="width: 5%; height: auto;"> CapCap

[English](#capcap) | [Tiếng Việt](#tieng-viet)

![CapCap Editor Preview](assets/preview.JPG)

### [🎬 Demo & Tutorial](https://www.tiktok.com/@nguyenthach617/video/7674305087023369493)

**CapCap** is a Windows desktop application for video localization, designed to simplify the entire workflow from transcription and translation to voice-over, visual editing, and final export.

It supports creating **Vietnamese and English subtitles**, translating video content, generating speech with TTS, and editing timed visual layers directly on the timeline.

## ✨ Highlights

* Guided workflow: **Prepare → Transcript → Translate → TTS → Export**
* Speech-to-text transcription with **Faster-Whisper** or **SenseVoice**
* Extract existing subtitles from video using **OCR**
* Support for multiple cloud/API translation providers, with **Google Translate** as a fallback
* Text-to-speech with **Piper TTS** and **Edge TTS**
* Optional speaker diarization and per-speaker voice assignment
* Timeline-based editor with support for:

  * Subtitles
  * Blur regions
  * Logos
  * Masks
  * Text layers
  * Selection ranges
* Layer locking to prevent accidental edits
* **Fast Preview** for quickly reviewing edits without a full export

## 🚀 Upcoming Features

CapCap is actively being developed, with new features and improvements added over time.

👉 [View the development roadmap](https://github.com/users/notepower2k1/projects/2)

## 📚 Documentation

* [How to Use](docs/how-to-use.md)
* [Requirements and Resources](docs/requirements.md)
* [Technical Stack](docs/technical-stack.md)
* [Project Structure](docs/project-structure.md)

## 🛠️ Run from Source

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap

python -m venv venv
venv\Scripts\activate

pip install -r requirements-local.txt
python ui/gui.py
```

You only need to copy `.env_example` to `.env` if you want to manually configure translation providers or remote servers.

Most CapCap settings can be configured directly from within the application.

## ❤️ Support CapCap

If CapCap is useful to you, consider supporting its continued development and maintenance.

### 🇻🇳 Donate in Vietnam

Scan the QR code below:

<img src="assets/qr.png" style="width: 25%; height: auto;">

### 🌍 International Donations

[![Buy Me a Coffee](assets/buymeacoffee.png)](https://buymeacoffee.com/hcaht)

Click the image above or visit [Buy Me a Coffee](https://buymeacoffee.com/hcaht).

## 📄 License

CapCap is licensed under the **Apache License 2.0**.

See [LICENSE](LICENSE) for details.

---

<a id="tieng-viet"></a>

<details>
<summary>🇻🇳 <strong>Tiếng Việt</strong></summary>

# <img src="assets/capcap.png" style="width: 5%; height: auto;"> CapCap

![Giao diện CapCap](assets/preview.JPG)

### [🎬 Demo & Hướng dẫn sử dụng](https://www.tiktok.com/@nguyenthach617/video/7674305087023369493)

**CapCap** là ứng dụng biên tập và bản địa hóa video dành cho Windows, giúp đơn giản hóa toàn bộ quy trình từ nhận diện giọng nói, dịch nội dung, lồng tiếng, chỉnh sửa hình ảnh cho đến xuất video hoàn chỉnh.

CapCap hỗ trợ tạo **phụ đề tiếng Việt và tiếng Anh**, dịch nội dung video, tạo giọng đọc bằng TTS và chỉnh sửa các lớp nội dung theo thời gian trực tiếp trên timeline.

## ✨ Điểm nổi bật

* Quy trình xử lý trực quan theo từng bước: **Chuẩn bị → Chép lời → Dịch → TTS → Xuất video**
* Chuyển giọng nói thành văn bản với **Faster-Whisper** hoặc **SenseVoice**
* Trích xuất phụ đề có sẵn trong video bằng **OCR**
* Hỗ trợ nhiều dịch vụ dịch thuật qua Cloud/API, với **Google Translate** làm phương án dự phòng
* Hỗ trợ tạo giọng đọc bằng **Piper TTS** và **Edge TTS**
* Tùy chọn nhận diện người nói và gán giọng đọc riêng cho từng người
* Timeline biên tập với nhiều loại lớp nội dung:

  * Phụ đề
  * Vùng làm mờ
  * Logo
  * Mặt nạ
  * Văn bản
  * Vùng chọn
* Hỗ trợ khóa lớp để tránh chỉnh sửa ngoài ý muốn
* **Fast Preview** giúp xem nhanh kết quả mà không cần xuất toàn bộ video

## 🚀 Tính năng sắp tới

CapCap vẫn đang được phát triển tích cực, với nhiều tính năng mới và cải tiến được bổ sung theo thời gian.

👉 [Xem lộ trình phát triển](https://github.com/users/notepower2k1/projects/2)

## 📚 Tài liệu

* [Hướng dẫn sử dụng](docs/how-to-use.md)
* [Yêu cầu hệ thống và tài nguyên](docs/requirements.md)
* [Công nghệ sử dụng](docs/technical-stack.md)
* [Cấu trúc dự án](docs/project-structure.md)

## 🛠️ Chạy từ mã nguồn

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap

python -m venv venv
venv\Scripts\activate

pip install -r requirements-local.txt
python ui/gui.py
```

Bạn chỉ cần sao chép `.env_example` thành `.env` nếu muốn cấu hình thủ công các dịch vụ dịch thuật hoặc máy chủ từ xa.

Phần lớn thiết lập của CapCap có thể được cấu hình trực tiếp ngay trong ứng dụng.

## ❤️ Ủng hộ CapCap

Nếu CapCap hữu ích với bạn, bạn có thể ủng hộ để giúp dự án tiếp tục được duy trì và phát triển.

### 🇻🇳 Ủng hộ tại Việt Nam

Quét mã QR bên dưới:

<img src="assets/qr.png" style="width: 25%; height: auto;">

### 🌍 Ủng hộ quốc tế

[![Buy Me a Coffee](assets/buymeacoffee.png)](https://buymeacoffee.com/hcaht)

Nhấp vào hình ảnh phía trên hoặc truy cập [Buy Me a Coffee](https://buymeacoffee.com/hcaht).

## 📄 Giấy phép

CapCap được phát hành theo **Apache License 2.0**.

Xem chi tiết tại [LICENSE](LICENSE).

</details>
