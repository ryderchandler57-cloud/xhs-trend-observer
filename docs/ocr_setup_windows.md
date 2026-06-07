# Windows OCR 环境安装说明

本项目的 OCR 旁路会在当前项目实际使用的 Python 环境里导入 `paddle` 和 `paddleocr`。必须把 PaddleOCR 安装到这个 Python，而不是安装到系统里另一个 Python，否则 `ocr_note_media.py` 仍然会显示 `PaddleOCR 未安装`。

默认优先使用：

```powershell
$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

如果这个 Python 不存在，脚本才会退回系统 `python`。

## 检查是否可用

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_ocr_environment.ps1 -CheckOnly
```

成功时应看到：

```text
paddle importable: True
paddleocr importable: True
```

## 安装 PaddleOCR

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_ocr_environment.ps1 -Install
```

脚本会在项目实际使用的 Python 中执行：

```powershell
python -m pip install --upgrade pip
python -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install paddleocr
```

安装后会自动验证：

```powershell
python -c "import paddle; print(paddle.__version__)"
python -c "import paddleocr; print('paddleocr ok')"
```

## 重新运行 OCR

如果图片已经通过 `extract_note_media.py` 下载，并且当前运行目录里有 `media-manifest.json`，直接运行：

```powershell
$py = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $py scripts\ocr_note_media.py --manifest outputs\local-20260605-161248\media-manifest.json --cleanup-media
& $py scripts\build_effective_text.py --run-dir outputs\local-20260605-161248
& $py scripts\audit_extraction_quality.py --run-dir outputs\local-20260605-161248
```

如果是跑完整本地流程，并希望自动执行图文下载、OCR、effective text 合并和质量诊断：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_local_pipeline.ps1 -EnableMediaOCR
```

## 图片清理行为

OCR 成功后，`ocr_note_media.py` 默认会安全清理当前运行目录下的图片缓存，只保留：

- `media-manifest.json`
- `media-text.json`
- `media-text.md`
- `media-cleanup-report.json`
- `media-cleanup-report.md`

清理只会发生在当前 run 的 `outputs\<run>\media\` 下，不会删除 raw、structured、HTML、数据库或正式 request 文件。

如果想保留图片用于调试：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_local_pipeline.ps1 -EnableMediaOCR -KeepMedia
```

或单独运行 OCR 时：

```powershell
$py = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $py scripts\ocr_note_media.py --manifest outputs\local-20260605-161248\media-manifest.json --keep-media
```

## 常见失败原因

- Python 版本或平台不兼容：确认是 64-bit Python，并且安装包支持当前 Python 版本。
- 网络下载失败：检查网络、代理、证书，稍后重试。
- pip 源失败：Paddle CPU 源可能暂时不可达，可以换网络后再跑 `-Install`。
- 权限问题：使用当前用户可写的 Python/虚拟环境，必要时以管理员身份运行 PowerShell。
- 装到了错误 Python：重新运行 `setup_ocr_environment.ps1 -CheckOnly`，确认脚本输出的 Python 路径就是项目实际使用路径。
