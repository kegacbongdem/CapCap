# Native Media Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Thực hiện tuần tự; đây là kế hoạch, chưa có task nào được triển khai.

**Goal:** Loại việc gọi FFmpeg CLI và tạo media trung gian khỏi thao tác tương tác, chuyển sang decode/mix trong RAM và preview GPU có giới hạn tài nguyên.

**Architecture:** Giữ libmpv video, thay audio sidecar đã mix thành WAV bằng PCM worker và QAudioSink. Dùng PyAV/SoundFile cho decode native, chung logic visuals giữa launcher/editor; giữ FFmpeg export và đường tương thích trong giai đoạn chuyển đổi.

**Tech Stack:** Python 3.11, PySide6/Qt Multimedia, python-mpv/libmpv, PyAV, SoundFile, NumPy, SciPy; unittest stdlib.

**Spec:** [Thiết kế và tiêu chí nghiệm thu](../specs/2026-09-12-native-media-preview-design.md).

## Global Constraints

- Windows 10/11; giữ Python 3.11 chạy từ source.
- Không thêm miniaudio, custom C++ DLL, framework task queue hoặc framework test mới.
- UI thread không decode media, trộn toàn track, sinh LUT lớn, chờ worker hoặc chờ subprocess.
- Mỗi decoder/container chỉ do một worker sở hữu; không chia sẻ PyAV container giữa audio và thumbnail.
- Audio dùng một output và một clock timeline; không để hai backend audio phát đồng thời.
- PCM cache tối đa 128 MiB; thumbnail cache tối đa 32 MiB, tính cả bản sao ảnh do UI giữ; queue tối đa 8 block audio. Đây là ngân sách buffer do ứng dụng quản lý, không phải giới hạn RSS toàn process.
- PCM nội bộ ban đầu giữ 16 kHz mono float32 để tương thích mixer hiện tại; output được resample theo format thiết bị. Nâng chất lượng stereo/48 kHz nội bộ là thay đổi riêng.
- File media nguồn được đọc theo nhu cầu. WAV câu TTS hoàn chỉnh, project và cache nhỏ có ích vẫn được lưu; không đặt mục tiêu SSD chỉ đọc một lần.
- Tác vụ media trả kết quả kèm generation ID; kết quả thuộc project/seek cũ bị loại. Đóng project phải đóng decoder, ngừng audio và giải phóng cache.
- Chỉ bật backend mới mặc định sau khi qua kiểm thử đúng chức năng và bundle. Fallback phải ghi lý do, không âm thầm công bố đã đạt đường native.

Không dùng một lần rewrite để thay cả pipeline. Các giai đoạn bên dưới là các đơn vị bàn giao riêng; chỉ giai đoạn có kiểm thử đạt mới bật. Không commit source/asset của người dùng không thuộc thay đổi.

## Bản đồ file

| File | Trách nhiệm / thay đổi |
| --- | --- |
| Mới `app/media_decode.py` | Decode audio bytes/cửa sổ và frame RGB; giữ decoder state, không phụ thuộc Qt |
| `app/audio_mixer.py` | Tái sử dụng schema track, gain và time-fit; thêm phép mix block, dùng native filters |
| Mới `ui/utils/preview_audio.py` | Worker PCM, QAudioSink, cache và transport audio; một implementation cụ thể |
| `ui/utils/media_backend.py` | Tích hợp audio mới, seek mode, clock, GPU options; giữ Qt fallback |
| `ui/utils/media_utils.py` | Đồng bộ play/pause/seek/freeze, duration label và subtitle theo timeline clock |
| `ui/main_window.py` | Snapshot track/volume/fade; bỏ gọi tạo WAV khỏi slider; nhận visuals trong RAM |
| `ui/views/editor/timeline.py`, `ui/views/preview_panel.py` | Tách kéo/thả seek, kết nối transport mới |
| `app/tts_processor.py`, `app/capcut/tts.py`, `app/vieneu_tts.py` | Conversion native ở các provider hiện có |
| `app/workflows/voice_workflow.py`, `app/engines/audio_mix_adapter.py` | Giữ PCM qua speed/fit/trim của một câu, serialize tại biên |
| `ui/worker_adapters/processing_workers.py`, `ui/views/launcher.py` | Decode visuals chung, hủy tác vụ cũ, mở editor trước khi visuals hoàn tất |
| `app/experimental_mpv_lut.py`, `app/video_filter_chain.py`, mới `assets/shaders/preview_color.glsl` | GPU color/LUT, quy tắc màu chung, kiểm chứng thứ tự effects |
| `ui/controllers/preview_controller.py` | Chỉ render file khi yêu cầu Fast Preview/export hoặc fallback; snapshot audio export |
| `requirements-local.txt`, `CapCap.spec` | Dependency trực tiếp, đóng gói native libraries; `CapCap_gui.spec` dùng lại spec chính |
| Mới `scripts/benchmark_preview.py` | Probe capability, đo event loop/process/file/RAM và lưu kết quả JSON theo yêu cầu |
| Mới `tests/test_media_audio.py`, `tests/test_preview_transport.py`, `tests/test_timeline_visuals.py`, `tests/test_preview_color.py` | Các invariant chức năng, không tạo framework riêng |

Không tách cả `main_window.py` hoặc tạo service/factory tổng quát. Chỉ đưa phần audio stateful mới ra một file vì cần owner thread và cleanup riêng.

## Thứ tự và checkpoint

`0. Baseline -> 1. Decode -> 2. Audio PCM -> 3. Transport/seek -> 4. TTS -> 5. Visuals -> 6. GPU -> 7. Release checks`

Task 2/3 tạo một mốc audio hoàn chỉnh: chưa thay backend mặc định trước khi cả hai đạt. Task 4/5 có thể bàn giao riêng sau Task 1 nhưng thứ tự mặc định ưu tiên audio như yêu cầu. Task 6 có checkpoint API/parity trước khi viết shader production.

### Task 0: Baseline và khả năng của bundle

**Files:** tạo `scripts/benchmark_preview.py`; chỉ thêm hook đo có điều kiện ở `ui/main_window.py`, `ui/utils/media_backend.py` nếu cần ghi nhận thao tác. Kết quả runtime đặt dưới `temp/benchmarks/`, không commit media/kết quả riêng của máy.

**Interfaces:** script nhận `--probe`, `--capture-ui`, `--video PATH`, `--output PATH`. Probe không load AI model. Khi capture, chạy GUI qua entry point sẵn có, ghi event loop delay, process được tạo và số byte/file media mới; log chỉ bật ở chế độ benchmark.

- [ ] Ghi cấu hình máy và phiên bản PySide6/PyAV/libsndfile/libmpv/FFmpeg, output device, codec/fps/GOP của video benchmark. Dùng các mẫu trong spec; mọi số cold/warm được tách riêng.
- [ ] Probe PyAV `atempo`, decode MP3/MP4, output QAudioSink; probe DLL mpv thực tế với `hwdec-current`, shader options và renderer. DLL probe chạy helper process để nếu native crash thì GUI chính không chết; đây là kiểm tra capability, không phải pipeline media chạy CLI.
- [ ] Đo đường cũ: 100 lần kéo volume với TTS + Music, 100 seek, 200 câu conversion, mở project không cache và có cache. Không tính thời gian TTS mạng thành thời gian decode.
- [ ] Dùng `sys.addaudithook` trong benchmark để bắt `subprocess.Popen` do Python tạo; đối chiếu process tree/Process Monitor nếu Qt hoặc DLL tự spawn, vì audit hook Python không thấy các process native tự tạo. Chụp danh sách/size media trước-sau để nhận diện file trung gian.

Lõi phân vị dùng được trong script:

```python
from math import ceil

def percentile95(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("No measurements")
    return ordered[ceil(0.95 * len(ordered)) - 1]

assert percentile95(list(range(1, 101))) == 95
```

- [ ] Chạy `.venv/Scripts/python.exe -B scripts/benchmark_preview.py --probe --output temp/benchmarks/capabilities.json`; expected: JSON capability hoặc lỗi thành phần có tên, không kết luận mọi GPU/API đều có chỉ từ tên DLL.
- [ ] Review số đo và commit riêng benchmark với message `test: establish native preview baseline`.

**Bàn giao:** baseline có thể chạy lại, những API bundle hỗ trợ và danh sách process/file cần biến mất trên native path. Chưa sửa hành vi app.

### Task 1: Decode native dùng chung, có giới hạn RAM

**Files:** tạo `app/media_decode.py`, `tests/test_media_audio.py`; sửa `requirements-local.txt`. PyAV đã cài nhưng cần khai báo `av` trực tiếp; không tự nâng toàn bộ dependencies.

**Interfaces tạo ra:**

```python
decode_audio(source: str | bytes, *, sample_rate: int = 16000) -> tuple[np.ndarray, int]
# float32, shape (samples,), mono; chỉ dùng cho một câu/clip ngắn.

AudioReader(path: str, *, sample_rate: int = 16000)
AudioReader.read(start_sample: int, sample_count: int) -> np.ndarray
AudioReader.close() -> None
# Đọc cửa sổ, padding zero ngoài EOF, tiếp tục decode nếu vị trí liên tiếp.
# Hỗ trợ context manager để đóng container dù lỗi/cancel.
```

- [ ] Viết test decode WAV bytes, MP3 mẫu, stereo -> mono, resample, EOF, input hỏng, count âm; patch `subprocess.Popen` thành lỗi để bảo đảm đường native không spawn. Test WAV tối thiểu:

```python
import io
import numpy as np
import soundfile as sf
from unittest.mock import patch
from app.media_decode import decode_audio

source = io.BytesIO()
sf.write(source, np.full(1600, 0.25, dtype=np.float32), 16000,
         format="WAV", subtype="FLOAT")
with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
    pcm, rate = decode_audio(source.getvalue())
assert rate == 16000 and pcm.shape == (1600,)
np.testing.assert_allclose(pcm, 0.25, atol=1e-6)
```

- [ ] Chạy `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_media_audio.py -v`; xác nhận fail do API chưa có.
- [ ] Decode bytes bằng SoundFile/`io.BytesIO` khi hỗ trợ; PyAV cho container hoặc fallback native. Chọn một định nghĩa downmix/resample và dùng lại `_resample_audio` khi xử lý clip ngắn; không duplicate hệ số ở từng caller.
- [ ] `AudioReader` giữ container/resampler giữa các block, seek backward theo stream time base rồi bỏ mẫu trước target dựa trên PTS. Tính offset `stream.start_time`, gap và codec delay; flush resampler EOF. Khi seek lại phải reset decoder/resampler state; không giữ queue toàn track.
- [ ] So sánh cửa sổ `[a:b]` đọc sau seek với slice tương ứng của decode tuần tự, có kiểm tra sai số/sample alignment; dùng WAV chính xác và MP3/AAC có encoder delay. File không timestamp/duration được xử lý bằng decode tuần tự hoặc lỗi rõ, không đoán duration bằng 0.
- [ ] Chạy lại test, đo file 4 giờ bằng reader cửa sổ và đóng/mở nhiều lần; commit `feat: add bounded native media decoding`.

**Bàn giao:** decode không CLI, không load toàn video vào RAM. `decode_audio()` không được dùng thay `AudioReader` cho track dài.

### Task 2: Mixer PCM và output audio thay WAV preview

**Files:** sửa `app/audio_mixer.py`, `ui/main_window.py`, `ui/utils/media_backend.py`, `ui/controllers/preview_controller.py`; tạo `ui/utils/preview_audio.py`; mở rộng `tests/test_media_audio.py`.

**Consumes:** `AudioReader.read()` / `.close()` Task 1; track schema từ `mix_audio_tracks()` và `_music_audio_tracks()`.

**Produces:**

```python
mix_pcm_block(blocks: list[np.ndarray], gains: list[float]) -> np.ndarray
# Các block cùng chiều dài/shape float32; không mutate PCM cache; clip [-1, 1].

PreviewAudioEngine.set_tracks(tracks: list[dict], warps: list[dict]) -> None
PreviewAudioEngine.set_track_gain(track_id: str, gain: float, muted: bool) -> None
PreviewAudioEngine.seek(timeline_ms: int) -> None
PreviewAudioEngine.play() -> None
PreviewAudioEngine.pause() -> None
PreviewAudioEngine.stop() -> None
PreviewAudioEngine.set_rate(rate: float) -> None
PreviewAudioEngine.timeline_position_ms() -> int
PreviewAudioEngine.close() -> None
# Facade Qt phát queued signals sang QObject worker; worker sở hữu sink/reader/timer.
# Signals: timelinePositionChanged(int), error(str), stateChanged(int).
```

- [ ] Viết test mix gain/mute/overload không mutate input; kiểm tra offset, loop, source_start, EOF, gain 0/100/200%, fade hiện có và keyframe volume được lấy từ UI. Khóa quyết định clip thay global normalize ở spec bằng test:

```python
import numpy as np
from app.audio_mixer import mix_pcm_block

a = np.array([0.2, 0.9], dtype=np.float32)
b = np.array([0.4, 0.9], dtype=np.float32)
original = a.copy()
mixed = mix_pcm_block([a, b], [0.5, 1.0])
np.testing.assert_allclose(mixed, [0.5, 1.0], atol=1e-6)
np.testing.assert_array_equal(a, original)
```

- [ ] Chạy test và xác nhận fail trước implementation. Cài lõi cộng block tối thiểu, kiểm tra lengths/non-finite/shape ở biên, clip đúng một lần sau cộng; không normalize riêng từng block. Áp ramp gain 5ms từ gain trước sang gain mới trong engine, không nhân vào cache.
- [ ] Worker decode theo cửa sổ, giữ cache PCM chưa mix tối đa 128 MiB với `OrderedDict`, key `(path, size, mtime_ns, sample_rate, window_start)`. Chặn eviction buffer đang dùng bằng ownership của block; giới hạn queue `deque`, bỏ generation cũ khi seek/đổi track. Không giữ tất cả reader của project mở nếu track không còn hoạt động.
- [ ] Tạo QAudioSink trong worker thread. Chọn format thiết bị hỗ trợ, resample liên tục từ PCM nội bộ, dùng push mode và chỉ ghi tới `bytesFree()`. Giữ phần bytes chưa write hết; không bỏ sample khi partial write. Timer worker 10ms, queue tối đa 8 block; benchmark buffer thực tế, không giả định yêu cầu 40ms luôn được driver chấp nhận.
- [ ] `_apply_audio_track_settings()` gửi gain/mute theo ID, không gọi `_schedule_preview_audio_refresh()` để mix file. `_resolve_preview_dubbed_playback_source()` và `sync_preview_audio_track_to_output()` truyền snapshot raw tracks cho native backend; chọn Original/Dub và các cờ mute vẫn giữ ý nghĩa hiện có. Original có thể decode trực tiếp video, không buộc extract WAV trước.
- [ ] Sửa `mix_audio_tracks()` trên đường export timeline để dùng cùng linear gains/clip, ghi block bằng SoundFile và publish atomically khi hoàn tất. Loại peak normalize toàn track chỉ ở mixer này; giữ các xử lý normalize giọng TTS khác. Test preview/export trên cùng samples không overload phải khớp sau lượng tử PCM16; overload cùng giới hạn biên. Khi export đang chạy dùng snapshot riêng, không mượn reader/buffer preview.
- [ ] Giữ backend cũ qua `CAPCAP_NATIVE_AUDIO=0` trong đợt chuyển đổi; backend mới opt-in ở Task 2. Nếu native output fail, đóng sink/worker trước khi bật sidecar cũ; log lý do. Không chạy cả hai output.
- [ ] Chạy audio tests; phát 1/3/8 track và thay volume 100 lần, xác nhận 0 WAV mới, 0 FFmpeg process. Nghe A/B để duyệt thay đổi overload và ramp; commit `feat: stream preview audio as PCM`.

**Bàn giao:** audio PCM chạy opt-in, volume phản hồi theo block; chưa bật mặc định trước Task 3.

### Task 3: Clock, freeze, speed và scrubbing

**Files:** sửa `ui/utils/media_backend.py`, `ui/utils/media_utils.py`, `ui/views/editor/timeline.py`, `ui/views/preview_panel.py`, `ui/main_window.py`; tạo `tests/test_preview_transport.py`.

**Consumes:** `PreviewAudioEngine` Task 2, `TimeWarpService` hiện có.

**Produces:** `MpvMediaPlayerBackend.setPosition(position, timeline_pos=None, *, exact=True)` và cùng chữ ký trong Qt fallback; `timeline_position_ms()`/signal timeline riêng. `set_position(gui, position, *, exact=True)` chuyển cờ xuống backend. Timeline bổ sung `scrubStarted()` và `scrubFinished(int)`; giữ signal seek cũ cho callers không scrub.

- [ ] Viết test seek burst giữ target cuối, release gửi exact đúng một lần, seek từ selection/programmatic vẫn exact; fake mpv ghi command để test flags, không cần GPU. Test freeze mapping trước/sau điểm chèn:

```python
from app.services.time_warp_service import TimeWarpService

warps = [{"time": 2.0, "duration": 1.0}]
assert TimeWarpService.timeline_to_media_time(2.5, warps) == 2.0
assert TimeWarpService.timeline_to_media_time(3.5, warps) == 2.5
```

- [ ] Chạy transport test fail; thêm một timer 33ms giữ pending target mới nhất ở UI controller. Trong scrub gửi `absolute+keyframes`; release dừng timer, loại pending và gửi `absolute+exact` tại vị trí thả. Test actual bundle chấp nhận cú pháp, giữ cú pháp tương đương nếu DLL chỉ nhận flags tách rời.
- [ ] Pause/mute audio trong scrub, flush thế hệ seek cũ, rồi resume theo trạng thái trước scrub khi exact target sẵn sàng. Dùng sự kiện seek/frame-ready của mpv để xác nhận frame thực, không lấy `positionChanged.emit(target)` làm bằng chứng màn hình đã cập nhật.
- [ ] Clock timeline lấy từ engine. Giữ tọa độ media của public API cũ; các handler subtitle, layer, playhead và label nghe clock timeline trực tiếp ở native path. Đọc toàn bộ callers của `position_changed`, `_apply_audio_fade`, `set_position` để không ánh xạ warp/fade hai lần.
- [ ] Freeze native không dùng timer monotonic cũ làm clock thứ hai. Giữ frame bằng mpv, original silence theo time domain, TTS/music chạy tiếp. Path render mẫu có `_preview_has_warps` dùng tọa độ file đã warp, tránh thêm freeze lần nữa.
- [ ] `set_rate()` dùng PyAV `atempo`, đổi video speed tương ứng, flush/rebase clock theo từng đoạn tốc độ. Đi qua mọi control thay playback rate hiện có; kiểm tra 0.5x/1x/2x và đổi giữa lúc phát. Không thêm UI speed riêng cho clip.
- [ ] Đồng bộ mpv với clock audio: đo sai lệch theo PTS; correction nhỏ có giới hạn, seek lớn chỉ khi drift vượt ngưỡng ổn định hoặc transport thay đổi. Khởi điểm deadband 20ms, drift lớn 80ms liên tiếp 3 lần; benchmark để chỉnh, không hard-seek mỗi tick. Giữ một hằng offset hiệu chỉnh output latency đo được, không giả định phần cứng lý tưởng.
- [ ] No-device dùng monotonic transport; underrun thật dừng tiến nội dung, rebuffer rồi resume; pause/resume không tăng clock trong lúc pause. Test close khi decode đang chạy, end-of-media TTS dài hơn video, stop/start và reload không còn worker cũ phát âm.
- [ ] Chạy `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_preview_transport.py -v`; kiểm tra click/flash hoặc loopback 10 phút đạt drift mục tiêu, thêm vòng seek qua freeze. Commit `feat: synchronize native audio and timeline transport`.

**Bàn giao:** mốc audio hoàn chỉnh; nếu Task 2/3 đều đạt mới bật native audio cho kiểm thử tích hợp.

### Task 4: TTS conversion và hậu xử lý mỗi câu trong RAM

**Files:** sửa `app/tts_processor.py`, `app/capcut/tts.py`, `app/vieneu_tts.py`, `app/audio_mixer.py`, `app/engines/audio_mix_adapter.py`, `app/workflows/voice_workflow.py`; mở rộng `tests/test_media_audio.py`.

**Consumes:** `decode_audio()` Task 1; `_build_atempo_filter()` và policy fit hiện có.

**Produces:** thêm `convert_audio_to_wav_16k_mono(source: str | bytes, wav_path: str) -> str` trong `app/media_decode.py`; `change_pcm_speed(pcm: np.ndarray, sample_rate: int, speed_ratio: float) -> np.ndarray` trong `app/audio_mixer.py`. File-path API hiện có tiếp tục hoạt động và dùng native internals.

- [ ] Test Edge/CapCut single/batch bằng mock phản hồi bytes, patch `subprocess.Popen` để fail; input hỏng không publish WAV; cancel không cập nhật artifact; output mono16k và thời lượng hợp lệ. Fixture WAV được tạo bằng SoundFile/BytesIO, MP3 có thể dùng mẫu nhỏ từ `assets/background_test.mp3` hoặc fixture sinh trong test setup, không gọi dịch vụ mạng.
- [ ] Thay `_edge_tts_to_mp3_async()` bằng helper trả bytes từ stream audio của Edge TTS; giữ retry/empty-response/voice/rate/volume. CapCut dùng bytes đã nhận thay ghi `temp_mp3`; single và batch đi chung conversion. VieNeu nhận PCM thì resample trực tiếp thay encode rồi decode lại.
- [ ] Giữ một WAV câu kết quả để project/export/CapCut Draft còn dùng được. Ghi file `.part` cùng thư mục, kiểm tra bằng `_validate_generated_wav()` hoặc validation tương đương, rồi `os.replace`; lỗi không xóa bản WAV tốt đã tồn tại.
- [ ] Chuyển `change_wav_speed`, `fit_wav_to_duration`, `trim_trailing_silence` sang native helper; probe metadata WAV bằng SoundFile, không ffprobe. Giữ tiếng nói/pitch bằng `atempo`, không dùng resample như một cách thay tempo.

Lõi graph dùng API PyAV local:

```python
from fractions import Fraction
import av

graph = av.filter.Graph()
source = graph.add_abuffer(sample_rate=16000, format="fltp",
                          layout="mono", time_base=Fraction(1, 16000))
tempo = graph.add("atempo", "1.25")
sink = graph.add("abuffersink")
graph.link_nodes(source, tempo, sink).configure()
```

- [ ] Với ratio ngoài khoảng một filter hỗ trợ, tái sử dụng phân rã của `_build_atempo_filter`; push frame có sample_rate/PTS hợp lệ, drain khi có output, flush `None` ở EOF. Test đổi tốc độ 1.25x của tone 1 giây: khoảng 0.8 giây và pitch giữ trong dung sai định trước; không bỏ phần đuôi vì chưa drain.
- [ ] Đối chiếu code thực thi fit với comment hiện tại trước khi port: có comment không khớp điều kiện `fit_ratio`. Đóng băng hành vi bằng test các nhánh smart/force/timeline; không sửa policy cùng lúc chuyển engine. Silence trim giữ threshold/min_duration/padding 100ms; kiểm tra có speech sau khoảng im lặng dài để không cắt mất cuối câu.
- [ ] Trong `VoiceWorkflow`, giữ PCM của một câu qua các lần chỉnh speed/fit/trim; tạo một artifact cuối khi accept câu. Retry/cancel/drop câu giải phóng buffer. Wrapper path chỉ dùng tại biên adapter cũ; ghi nhận số intermediate còn lại theo provider để không đánh dấu toàn pipeline native sau khi chỉ đổi conversion.
- [ ] Chạy audio test rồi batch 200 câu bytes có sẵn, báo decode/resample/filter/write riêng; bảo đảm 0 FFmpeg/ffprobe trên các bước đã chuyển. Commit `feat: process TTS audio through native libraries`.

**Bàn giao:** bỏ MP3 file tạm và CLI conversion; hoàn tất native speed/fit/trim trước khi công bố TTS hậu xử lý đã chuyển hết.

### Task 5: Thumbnail và waveform chung cho launcher/editor

**Files:** sửa `app/media_decode.py`, `ui/worker_adapters/processing_workers.py`, `ui/views/launcher.py`, `ui/main_window.py`, `ui/views/editor/timeline.py`; tạo `tests/test_timeline_visuals.py`.

**Consumes:** decoder Task 1; request signature/cache invalidation hiện có.

**Produces:**

```python
iter_video_thumbnails(path: str, timestamps: list[float], *, width: int = 180)
# Yield (actual_pts_seconds: float, rgb: np.ndarray), shape (h,w,3), uint8.

build_waveform(path: str, *, bucket_count: int = 1200) -> tuple[list[float], float]
# Envelope theo peak/RMS hiện có, duration theo PTS; PCM stream, không full-array.
```

- [ ] Test frame PTS/color bằng clip ngắn có màu khác nhau theo thời điểm; video VFR không lấy frame_index/fps làm timestamp. Test waveform nhiều cỡ block cho kết quả như batch; file silence/no-audio có kết quả rỗng hoặc zero theo contract, không exception không rõ nghĩa.
- [ ] Decode video trong worker riêng; mở container một lần cho một job, seek backward theo time base rồi decode tới target. Scale RGB về thumbnail trước khi truyền signal. UI ưu tiên viewport và hiển thị batch nhỏ, không chờ 120 ảnh cuối cùng.
- [ ] Giữ ownership buffer khi chuyển Qt:

```python
from PySide6.QtGui import QImage

rgb = np.ascontiguousarray(rgb)
height, width, _ = rgb.shape
image = QImage(rgb.data, width, height, rgb.strides[0],
               QImage.Format_RGB888).copy()
# Worker emit QImage; chỉ GUI thread gọi QPixmap.fromImage(image).
```

- [ ] Peak worker cộng max/sumsq/count theo bucket, chia theo toàn thời lượng và dùng global peak để giữ scale waveform cũ. Stream có duration không biết trước: pass đầu tính duration/peak, pass sau bucket nếu cần; chạy nền, không đổ PCM vào RAM để né pass thứ hai. Resample state liên tục và xử lý timestamp gap như silence.
- [ ] Bỏ bản extraction duplicate trong `_prepare_timeline_visual_cache()` và `_extract_waveform_audio()` của launcher; `_get_video_duration()` dùng metadata PyAV ngoài UI thread. ProjectCard không gọi `_extract_thumbnail()` đồng bộ khi tạo card.
- [ ] `_on_visual_cache_done()` không còn là điều kiện mở editor. Mở sau metadata/project sẵn sàng; editor tự request visuals. Hủy launcher worker trước khi owner bị hủy hoặc bàn giao kết quả đã hoàn tất, không chuyển một QThread còn chạy sang widget đã chết. Queue jobs cũ phải bị hủy khi đổi project, không chỉ bỏ kết quả ở UI.
- [ ] Một cache thumbnail theo byte budget, tránh mỗi widget tự giữ thêm bộ QPixmap đầy đủ. Cache peaks nhỏ trên đĩa có version/source fingerprint và atomic replace; đọc JPG/manifest cũ khi hợp lệ, không bắt buộc xóa cache cũ. Đường mới không ghi JPG/WAV cho timeline.
- [ ] Chạy `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_timeline_visuals.py -v`; mở 20 lần xen kẽ hai project, có ảnh cập nhật dần và không lẫn nguồn. Native path không spawn FFmpeg/ffprobe, không tạo waveform WAV; commit `feat: decode timeline visuals in memory`.

**Bàn giao:** launcher và editor đều hết extraction CLI trên đường native, mở editor không đợi cả bộ visuals.

### Task 6: GPU color/LUT, không sinh file theo slider

**Files:** sửa `app/experimental_mpv_lut.py`, `app/video_filter_chain.py`, `ui/utils/media_backend.py`, `ui/main_window.py`; tạo `assets/shaders/preview_color.glsl`, `tests/test_preview_color.py`.

**Consumes:** capability mpv Task 0; state `final`, `lut_path`, `lut_strength`; thứ tự hiệu ứng ở spec.

**Produces:** `MpvGpuLutPrototype.apply()`/`.clear()` giữ API cũ; thêm phương thức `set_color_state(state: dict) -> None` trong cùng helper GPU nếu needed để cập nhật shader parameters. Không thêm một GPU service/factory khác.

- [ ] Trên DLL bundle, thử một shader nhỏ có parameter để xác nhận thay parameter hoạt động cả khi pause; kiểm tra shader không phải recompile mỗi tick. Nếu API không có, ghi rõ capability thiếu và dùng bản mpv bundle tương thích đã smoke-test hoặc giữ native LUT hiện có; không viết engine mới để né API.
- [ ] Test `.cube` identity 2x2x2 và một LUT hoán đổi kênh để phát hiện sai thứ tự R/G/B; strength 0/0.5/1, TITLE/comment/domain, sai size/NaN phải reject. Dùng fixture nhỏ tạo trong `TemporaryDirectory`, không thêm hàng trăm LUT test.
- [ ] Upload LUT một lần theo path/mtime; blend bằng shader parameter, không gọi `blended_path()` mỗi slider value. Gộp slider updates theo frame, gửi state mới nhất, giữ UI trả về ngay. Lõi blend là:

```glsl
vec3 apply_strength(vec3 source_color, vec3 lut_color, float strength) {
    return mix(source_color, lut_color, clamp(strength, 0.0, 1.0));
}
```

- [ ] Chuyển từng công thức màu từ `_realtime_color_graph()` sang shader trên cùng miền màu, giữ clamps và thứ tự như `build_video_filter_chain()`. Không map slider thẳng sang mpv brightness/contrast với hệ số khác. Mỗi nhóm tham số có ảnh so sánh với FFmpeg reference, decode về cùng RGB/range trước khi đo.
- [ ] Chạy reference SDR PNG/lossless để loại sai số encoder: đề xuất MAE <= 2/255, p99 sai số <= 6/255 cho mẫu test; kiểm tra bằng mắt trên gradient/skin/high contrast. Đây là ngưỡng duyệt ban đầu, không tự nới để test pass. HDR phải có reference riêng trước khi bật.
- [ ] Với blur/mask, bảo toàn `color -> blur/mask -> LUT`: đưa các stage cần thiết sang GPU trong cùng pipeline, kiểm tra vùng, opacity, pixelate, feather/timing mà UI đang hỗ trợ. Nếu tổ hợp chưa đúng, dùng lavfi in-process cho cả chuỗi liên quan và ghi capability; không reorder ngầm. Mốc chuyển toàn bộ tổ hợp GPU chưa đạt cho đến khi parity pass.
- [ ] Bật `hwdec=auto` có fallback software sau khi probe, log `hwdec-current`; kiểm tra khi lavfi CPU filter hiện diện có copy về RAM/decoder fallback. Không gọi `vo=gpu-next` là bằng chứng hw decode đã bật.
- [ ] Chạy `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_preview_color.py -v` cho parser/state; dùng benchmark GPU thực cho images/latency. Kéo 100 giá trị strength không tạo `.cube` mới, preview/export khớp. Commit `feat: update preview color through GPU parameters`.

**Bàn giao:** color/LUT phản hồi trực tiếp trên tổ hợp được xác nhận; danh sách fallback minh bạch. Không tuyên bố đã GPU hóa mọi effect nếu còn tổ hợp chưa đạt.

### Task 7: Tích hợp, fallback, đóng gói và phát hành

**Files:** sửa `CapCap.spec`, `requirements-local.txt`, `ui/controllers/preview_controller.py`, `docs/technical-stack.md`, `docs/how-to-use.md`; cập nhật benchmark và test đã tạo. Không chỉnh `CapCap_gui.spec` nếu wrapper tiếp tục dùng spec chính được.

**Consumes:** mọi milestone đã pass, snapshot export và legacy project artifacts.

- [ ] Rà lại mọi caller tạo preview tự động: `_start_video_preview()`, lịch filter preview, sync audio, launcher workers. Native live path không vô tình rơi vào render video/file WAV khi nhấn Play hoặc thay inspector. Fast Preview 5 giây và Final Export vẫn là thao tác render rõ ràng theo yêu cầu.
- [ ] Khai báo dependencies trực tiếp đã dùng; kiểm tra PyInstaller hook PyAV/SoundFile trước khi thêm hidden imports/binaries. Chỉ collect DLL thiếu thực tế, tránh `collect_all` toàn bộ môi trường. Shader theo `assets` đã được bundle từ `CapCap.spec`.
- [ ] Kiểm thử conflict native libraries của PyAV, libmpv, Qt Multimedia và ONNX trong cùng process; log đường DLL/version. Không sửa PATH toàn máy; không tái dùng DLL khác ABI để giảm kích thước bundle.
- [ ] Mở project cũ, playback từ raw tracks và từ existing mixed audio, lưu/mở lại, xuất CapCut Draft vẫn có file câu hoàn chỉnh. Không lưu buffer/generation/cache pointer vào JSON project.
- [ ] Test mất audio device/thiếu codec/mpv init fail: dừng native owner trước fallback; giữ vị trí/mute/volume, log một lần; không restart loop. QMediaPlayer fallback vẫn có thể cần file mix, ghi nhận là degraded mode và không tính vào KPI native.
- [ ] Chạy `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`; expected: các test mới và i18n cũ pass. Sau đó chạy benchmark cùng mẫu/cấu hình Task 0, so sánh p95/process/file/RAM và audio drift. Không thay mẫu giữa before/after.
- [ ] Build `.venv/Scripts/python.exe -m PyInstaller --noconfirm CapCap_gui.spec`; smoke-test bundle trên Windows không Python/không CUDA, và máy NVIDIA có driver. Chạy cả pipeline AI + preview để kiểm tra tranh tài nguyên, không load/unload AI như cách vá lỗi preview.
- [ ] Khi tất cả milestone audio/transport đạt thì bật native audio mặc định; giữ `CAPCAP_NATIVE_AUDIO=0` làm rollback cho đợt phát hành đầu. GPU theo capability/parity đã xác nhận. Ghi docs về backend, format audio hiện tại và thay đổi overload; commit `feat: enable verified native preview pipeline`.

**Bàn giao:** báo cáo trước/sau, bundle đã smoke-test, ranh giới CLI còn lại và fallback đúng. Nếu KPI/parity fail, milestone liên quan chưa hoàn thành dù DLL chạy được.

## Checklist bàn giao cuối

- [ ] Volume TTS + Music thay trực tiếp trong PCM, không sinh WAV hay process.
- [ ] Playback, freeze, speed và seek giữ đồng bộ cả subtitle/layer/audio.
- [ ] Conversion và hậu xử lý TTS chạy native; chỉ ghi câu hoàn chỉnh tại biên cần lưu.
- [ ] Launcher/editor đều decode visuals native và hiển thị dần.
- [ ] Color/LUT không sinh file theo slider; shader và export đạt parity trên tổ hợp đã bật.
- [ ] RAM/cache/queue giới hạn, close/cancel không còn nguồn audio/worker cũ.
- [ ] Project cũ, CapCut Draft, Fast Preview và Final Export vẫn dùng được.
- [ ] Đếm rõ CLI còn ở export/Fast Preview/legacy fallback/AI offline; không tuyên bố loại CLI khỏi toàn repo.

## Ước lượng để sắp công việc

Đây là ước lượng kỹ thuật, không phải cam kết lịch: baseline + decoder 1–2 ngày; audio PCM + transport 4–7 ngày; TTS 2–3 ngày; visuals 1–3 ngày; GPU/parity 3–6 ngày; đóng gói và regression 2–3 ngày. Tổng khoảng 13–24 ngày làm việc cho một người, có thể tăng nếu DLL bundle không hỗ trợ shader API hoặc tổ hợp blur/mask cần chuyển GPU sâu hơn. Mốc bàn giao đầu tiên là audio PCM + transport, không phải chờ toàn bộ kế hoạch.
