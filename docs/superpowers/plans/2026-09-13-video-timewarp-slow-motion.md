# Video Time-Warp: Slow-Motion, Freeze Frame & Timeline Visual Cuts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Triển khai tính năng Video Time-Warp (Slow-Motion và Freeze Frame) để bù trừ thời gian tự nhiên cho giọng đọc AI Dubbing/TTS, đồng thời hiển thị trực quan Virtual Cuts, dải màu và Badge trên Track Video V1 và Track Phụ đề TS1.

**Architecture:** Mở rộng `TimeWarpService` để quản lý ánh xạ thời gian 2 chiều (non-destructive) cho cả `"slow"` và `"freeze"`, tích hợp vào FFmpeg filtergraph khi export. Trên `EditorTimeline`, vẽ các vạch cắt ảo (Notch lines), dải phủ màu mờ (tint ribbon) và badge tốc độ trên V1/TS1 bằng `QPainter`. Trên `MainWindow`, cho phép chọn chế độ Fit Voice (Slow hoặc Freeze) và hoàn tác.

**Tech Stack:** Python 3.10+, PySide6 (Qt QPainter), PyAV / FFmpeg filtergraph, unittest.

**Spec:** `docs/superpowers/specs/2026-09-13-video-timewarp-slow-motion-design.md`

## Global Constraints
- Không chia nhỏ hay làm thay đổi file video gốc (Non-destructive editing).
- Tương thích ngược với các project cũ (chỉ có freeze warps).
- Tốc độ render trên timeline phải tức thời (< 0.1ms cho việc vẽ overlay), không làm drop FPS khi playback/scrubbing.
- Tất cả 125 tests hiện có phải tiếp tục pass.

---

### Task 1: Nâng cấp `TimeWarpService` cho Slow-Motion và FFmpeg Filtergraph

**Files:**
- Modify: `app/services/time_warp_service.py:1-347`
- Test: `tests/test_time_warp_service.py`

**Interfaces:**
- Produces:
  - `TimeWarpService.create_time_warp(time, duration, warp_type="slow", segment_index=None, speed=1.0, media_start=None, media_end=None, note="") -> dict`
  - `TimeWarpService.apply_segment_extension(segments, segment_index, added_duration, translated_segments=None, warp_type="slow") -> tuple[dict, list, list]`
  - `TimeWarpService.timeline_to_media_time(timeline_time: float, warps: list[dict]) -> float`
  - `TimeWarpService.media_to_timeline_time(media_time: float, warps: list[dict]) -> float`
  - `TimeWarpService.build_ffmpeg_timewarp_filtergraph(video_stream, warps, total_media_duration, fps=30.0, audio_stream=None)` (kèm alias `build_ffmpeg_freeze_filtergraph`)

- [x] **Step 1: Viết unit test cho TimeWarpService với Slow-Motion**
  Tạo hoặc cập nhật `tests/test_time_warp_service.py`:
  - Test `create_time_warp` với `warp_type="slow"` và `speed`.
  - Test `apply_segment_extension` tính đúng `speed = orig_dur / (orig_dur + delta)`.
  - Test `timeline_to_media_time`: trong khoảng slow, thời gian media trôi với vận tốc `speed`.
  - Test `media_to_timeline_time`: ánh xạ ngược chính xác.
  - Test `build_ffmpeg_timewarp_filtergraph`: sinh đúng `setpts=(1/speed)*(PTS-STARTPTS)` cho slow slice và `loop` cho freeze slice.

- [x] **Step 2: Chạy test để xác nhận test thất bại (Red)**
  ```powershell
  .\.venv\Scripts\python.exe -m unittest tests/test_time_warp_service.py
  ```

- [x] **Step 3: Cập nhật `app/services/time_warp_service.py`**
  - Mở rộng `create_time_warp`: thêm các trường `speed`, `media_start`, `media_end`.
  - Cập nhật `apply_segment_extension`: nhận `warp_type="slow"`, tính `D_orig = orig_end - orig_start`, tính `speed = round(D_orig / (D_orig + delta), 3)`. Ghi nhận vào metadata của target segment: `target_seg["warp_type"] = warp_type`, `target_seg["warp_speed"] = speed`.
  - Cập nhật `timeline_to_media_time` & `media_to_timeline_time` xử lý đúng logic hàm bậc nhất cho slow warp.
  - Mở rộng filtergraph export hỗ trợ cả slow (`setpts`, `atempo`) và freeze (`loop`, silence).

- [x] **Step 4: Chạy test để xác nhận test thành công (Green)**
  ```powershell
  .\.venv\Scripts\python.exe -m unittest tests/test_time_warp_service.py
  ```

- [x] **Step 5: Commit thay đổi Task 1**
  ```powershell
  git add app/services/time_warp_service.py tests/test_time_warp_service.py
  git commit -m "feat(timewarp): add slow-motion support and time mapping in TimeWarpService"
  ```

---

### Task 2: Hiển thị trực quan Virtual Cuts & Overlays trên Timeline (Track V1 & TS1)

**Files:**
- Modify: `ui/views/editor/timeline.py:1380-1680`
- Test: `tests/test_timeline_timewarp_visuals.py`

**Interfaces:**
- Consumes:
  - `layer.metadata.get("warp_type")`, `layer.metadata.get("warp_speed")`, `layer.metadata.get("extended_duration")`
  - `self._timeline` và `self.video_time_warps`
- Produces:
  - Hàm `_draw_video_timewarp_overlays(painter, x, bar_y, w, bar_h, view_w)` trên track V1.
  - Cập nhật `_draw_standard_layer_bar` hỗ trợ vẽ extension tail màu tím cho `slow` và màu cyan cho `freeze`.

- [x] **Step 1: Viết unit test cho hiển thị visual timewarp trên timeline**
  Tạo `tests/test_timeline_timewarp_visuals.py` kiểm tra:
  - Phân loại màu sắc và icon badge: `slow` -> tím / `🐢`, `freeze` -> cyan / `⏸`.
  - Tính toán tọa độ pixel `(x, w)` tương ứng với start/end của warps.

- [x] **Step 2: Chạy test để xác nhận test thất bại (Red)**
  ```powershell
  .\.venv\Scripts\python.exe -m unittest tests/test_timeline_timewarp_visuals.py
  ```

- [x] **Step 3: Triển khai vẽ trên `ui/views/editor/timeline.py`**
  - Thêm thuộc tính `set_video_time_warps(warps)` vào `EditorTimeline`.
  - Trong `_draw_track_layers` khi `track.type == LayerType.VIDEO`:
    - Sau khi gọi `_draw_video_thumbnails`: gọi `self._draw_video_timewarp_overlays(painter, x, bar_y, w, bar_h, view_w)`.
    - `_draw_video_timewarp_overlays`: Duyệt qua các warps hợp lệ, vẽ 2 vạch notch line trắng mờ ở 2 đầu, dải màu mờ `QColor(139, 92, 246, 65)` (slow) hoặc `QColor(6, 182, 212, 65)` (freeze), và pill badge ở góc trên trái: `🐢 0.85x (+0.8s)` hoặc `⏸ +1.2s`.
  - Trong `_draw_standard_layer_bar` (Track TS1):
    - Đọc `warp_type = segment_metadata.get("warp_type", "freeze")` và `warp_speed = segment_metadata.get("warp_speed")`.
    - Nếu là `slow`: vẽ đuôi mở rộng màu tím `QColor(76, 29, 149)` kèm hatch tím `QColor(167, 139, 250, 60)` và badge `🐢 {speed:.2f}x` hoặc `🐢 +{ext:.1f}s`.
    - Nếu là `freeze`: giữ nguyên màu cyan `⏸ +{ext:.1f}s`.

- [x] **Step 4: Chạy test để xác nhận test thành công (Green)**
  ```powershell
  .\.venv\Scripts\python.exe -m unittest tests/test_timeline_timewarp_visuals.py
  ```

- [x] **Step 5: Commit thay đổi Task 2**
  ```powershell
  git add ui/views/editor/timeline.py tests/test_timeline_timewarp_visuals.py
  git commit -m "feat(timeline): render virtual cuts, color ribbon and badges for slow/freeze on V1 and TS1"
  ```

---

### Task 3: Tích hợp Thao tác Fit Voice (Slow/Freeze) trên Segment Inspector & MainWindow

**Files:**
- Modify: `ui/main_window.py:11130-11210, 11508-11585`
- Test: `tests/test_media_audio.py`

**Interfaces:**
- Consumes:
  - `TimeWarpService.apply_segment_extension(..., warp_type="slow")`
  - `self.timeline.set_video_time_warps(self.video_time_warps)`
- Produces:
  - `MainWindow.extend_segment_video(segment_index, added_duration, warp_type="slow")`
  - UI nút `⚡ Fit Voice (Slow)` và tùy chọn `+ Freeze` / `+ Slow`.
  - Badge trạng thái `[🐢 Slow 0.85x (+0.8s)]` và `Revert`.

- [x] **Step 1: Cập nhật hàm `extend_segment_video` và `revert_segment_video_extension` trong `ui/main_window.py`**
  - Nhận tham số `warp_type: str = "slow"`.
  - Truyền `warp_type` vào `TimeWarpService.apply_segment_extension`.
  - Truyền `self.video_time_warps` vào `self.timeline.set_video_time_warps(self.video_time_warps)`.

- [x] **Step 2: Cập nhật giao diện Segment Inspector Row**
  - Khi chưa warp:
    - Nút `fit_voice_btn`: Đổi label thành `⚡ Fit Voice (Slow)` (hoặc `⚡ Fit Voice (+Xs)` với tooltip giải thích là làm chậm video để khớp giọng).
    - Cung cấp thêm lựa chọn `+ Freeze` hoặc `+ Slow` tùy chỉnh.
  - Khi đã warp (`extended_duration > 0`):
    - Đọc `warp_type`: nếu là slow, hiển thị badge `🐢 Slow {speed:.2f}x (+{dur:.1f}s)`; nếu là freeze, hiển thị `⏸ Frozen (+{dur:.1f}s)`.
    - Nút `Revert` khôi phục về $1.0\times$.

- [x] **Step 3: Chạy unit tests và toàn bộ test suite (Full Regression Check)**
  ```powershell
  .\.venv\Scripts\python.exe -m unittest discover tests
  ```

- [x] **Step 4: Commit thay đổi Task 3**
  ```powershell
  git add ui/main_window.py
  git commit -m "feat(ui): add slow-motion fit voice action and status badges in segment inspector"
  ```
