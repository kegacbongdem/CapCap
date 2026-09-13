# Thiết kế tính năng Video Time-Warp: Slow-Motion, Freeze Frame & Hiển thị trực quan trên Timeline

Ngày: 2026-09-13. Trạng thái: Đã thống nhất thiết kế; chuẩn bị thực thi.

## 1. Mục tiêu và Phạm vi

### Mục tiêu
Cung cấp giải pháp bù trừ thời gian linh hoạt và tự nhiên giữa video và giọng đọc (AI Dubbing/TTS) bằng cách hỗ trợ cả **Slow-Motion (Làm chậm video)** và **Freeze Frame (Đóng băng khung hình)**, đồng thời hiển thị trực quan các phân đoạn bị can thiệp nhịp độ trên giao diện Timeline (Track V1 và Track TS1).

### Phạm vi
1. **Engine Time Warp (`app/services/time_warp_service.py`)**:
   - Mở rộng cấu trúc time-warp hỗ trợ `warp_type`: `"slow"` và `"freeze"`.
   - Tính toán hệ số tốc độ $speed = \frac{D_{\text{orig}}}{D_{\text{orig}} + \Delta t}$.
   - Cập nhật hàm ánh xạ 2 chiều `timeline_to_media_time` và `media_to_timeline_time` cho cả slow và freeze.
   - Mở rộng bộ dựng FFmpeg filtergraph (`build_ffmpeg_timewarp_filtergraph`) hỗ trợ `setpts=(1/speed)*PTS` cho slow và `loop` cho freeze.
2. **Hiển thị trực quan trên Timeline (`ui/views/editor/timeline.py`)**:
   - **Track Video V1 (Virtual Cuts & Ribbon Overlays)**: Không chia nhỏ dữ liệu video, giữ 1 video stream liền mạch nhưng vẽ vạch cắt ảo (Notch lines), phủ dải màu mờ (tint ribbon) và gắn badge tốc độ (`🐢 0.85x`, `⏸ +1.2s`) tại tọa độ của từng segment có warp.
   - **Track Phụ đề TS1**: Hiển thị đuôi mở rộng màu tím pastel cho `slow`, màu xanh ngọc cyan cho `freeze`.
   - Hover tooltip mô tả chi tiết trạng thái time-warp.
3. **Thao tác người dùng trên UI (`ui/main_window.py`)**:
   - Hỗ trợ Fit Voice bằng Slow Motion (mượt mà, không khựng hình) bên cạnh Freeze Frame.
   - Nút thao tác thủ công `+ Slow` / `+ Freeze` và nút `Revert` khôi phục tốc độ $1.0\times$.

---

## 2. Kiến trúc & Công thức Ánh xạ Thời gian (Time Mapping)

Mọi biến đổi thời gian đều được quản lý phi phá hủy (non-destructive) thông qua danh sách `video_time_warps`.

### Cấu trúc dữ liệu Time Warp
```python
{
    "id": "warp_abcd1234",
    "segment_index": 3,
    "type": "slow",           # "slow" | "freeze"
    "time": 12.5,             # Mốc thời gian bắt đầu segment trên video gốc
    "media_end": 15.0,        # Mốc thời gian kết thúc segment trên video gốc (D_orig = 2.5s)
    "duration": 0.5,          # Thời gian bù thêm (delta = +0.5s => D_new = 3.0s)
    "speed": 0.833,           # Hệ số tốc độ = 2.5 / 3.0 = 0.833x
    "note": "fit_voice"
}
```

### Công thức ánh xạ Timeline $\longleftrightarrow$ Media
Giả sử segment $i$ bắt đầu tại $T_{\text{start}}$ trên timeline, thời lượng mới $D_{\text{new}} = D_{\text{orig}} + \Delta t$, tốc độ $S = D_{\text{orig}} / D_{\text{new}}$.

1. **Timeline to Media Time**:
   - Nếu $t < T_{\text{start}}$: $t_{\text{media}} = t - \text{prior\_shifts}$.
   - Nếu $T_{\text{start}} \le t < T_{\text{start}} + D_{\text{new}}$:
     - Với **Slow**: $t_{\text{media}} = t_{\text{orig\_start}} + (t - T_{\text{start}}) \times S$.
     - Với **Freeze**: $t_{\text{media}} = t_{\text{orig\_end}}$ (giữ nguyên frame tại điểm cuối).
   - Nếu $t \ge T_{\text{start}} + D_{\text{new}}$: $t_{\text{media}} = t - (\text{prior\_shifts} + \Delta t)$.

2. **Media to Timeline Time**:
   - Chiều ngược lại tương ứng cho scrubber và đồng bộ playhead.

---

## 3. Thiết kế Giao diện (UX/UI Specification)

### 3.1. Track Video V1
- **Vạch cắt ảo (Notch Lines)**: Đường kẻ dọc 1px màu `rgba(255, 255, 255, 0.4)` tại $t_{\text{start}}$ và $t_{\text{end}}$.
- **Dải phủ màu (Ribbon Overlay)**:
  - `slow`: Phủ mờ màu tím `QColor(139, 92, 246, 65)` kèm viền trên/dưới `QColor(167, 139, 250, 180)`.
  - `freeze`: Phủ mờ màu xanh ngọc `QColor(6, 182, 212, 65)` kèm viền `QColor(45, 212, 191, 180)`.
- **Badge thông tin (Top-left pill)**:
  - `slow`: Icon `🐢` + `{speed:.2f}x (+{dur:.1f}s)`.
  - `freeze`: Icon `⏸` + `+{dur:.1f}s`.

### 3.2. Track Phụ đề TS1
- **Phần mở rộng (Extension Tail)**:
  - `slow`: Nền màu tím đậm `QColor(76, 29, 149)` kèm hoa văn gạch chéo tím sáng `QColor(167, 139, 250, 60)`, badge `🐢 {speed:.2f}x`.
  - `freeze`: Giữ nguyên nền xanh ngọc `QColor(14, 66, 80)` + badge `⏸ +{dur:.1f}s`.

### 3.3. Segment Inspector (Hàng câu thoại)
- Hiển thị badge trạng thái:
  - Đang Slow: Badge tím `[🐢 Slow 0.85x (+0.5s)]` + Nút `Revert`.
  - Đang Freeze: Badge xanh `[⏸ Freeze (+0.5s)]` + Nút `Revert`.
- Nút bù thời gian:
  - `⚡ Fit Voice (Slow)` (khuyên dùng, ưu tiên mượt hình).
  - Menu mở rộng / nút phụ `+ Freeze`.

---

## 4. Xuất Video (FFmpeg Export Filtergraph)

Đối với mỗi đoạn video bị can thiệp thời gian:
- **Đoạn Slow**:
  - Video: `[{in_v}]trim=start={s}:end={e},setpts=(1/{speed})*(PTS-STARTPTS)[v_slow_{idx}]`
  - Audio (nếu giữ âm thanh gốc): `[{in_a}]atrim=start={s}:end={e},asetpts=PTS-STARTPTS,atempo={speed}[a_slow_{idx}]`
- **Đoạn Freeze**:
  - Giữ nguyên filter `loop` hiện tại.
- **Concat**: Nối liền mạch các đoạn bình thường, đoạn slow và đoạn freeze thành 1 luồng video hoàn chỉnh.

---

## 5. Kế hoạch Kiểm thử & Xác minh

1. **Unit tests (`tests/test_time_warp_service.py`)**:
   - Kiểm tra `create_time_warp` với type `"slow"` và `"freeze"`.
   - Kiểm tra `timeline_to_media_time` với slow warp (tính toán chính xác tại đầu, giữa, cuối và sau segment).
   - Kiểm tra `media_to_timeline_time` ánh xạ ngược.
   - Kiểm tra `build_ffmpeg_timewarp_filtergraph` sinh đúng filter `setpts` và `atempo`.
2. **Unit tests UI & Timeline**:
   - Kiểm tra `apply_segment_extension` với slow warp và tính toán `extended_duration` / `speed`.
   - Kiểm tra `remove_segment_extension` khôi phục đúng thời gian.
3. **Kiểm tra hồi quy toàn bộ**: Chạy full test suite 125 tests để đảm bảo không ảnh hưởng đến bất kỳ luồng xuất, preview hay audio nào.
