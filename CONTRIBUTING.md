# 参与开发

## 环境

- Windows 10/11，Python 3.11，Node.js ≥ 22.19
- `python -m pip install -r requirements-dev.txt`
- 原生界面依赖上游 `dsh-dafeiyu@0.1.23` 的 desktop profile

## 测试

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s native -p "test_*.py" -v   # 709 项
npm test                                                  # 36 项
python native/realtime_probe.py                           # 本机回环，不开麦
```

`native/build.ps1` 生成 Helper；`native/run_smoke.py native/dist/dsh-dafeiyu-helper.exe` 做隔离的打包冒烟。构建产物、`native/verification/`、`native/backup/` 都不要提交。

## 约束

- 不提交真实 API Key、`realtime.json` / `cloud.json`、成长档案或截图。
- 不把云端模型输出、公开资料或语音转写接入电脑操作执行路径；电脑操作必须逐项物理确认。
- 新增网络来源必须使用固定官方 URL/版本，并登记许可与归属。
- `install.ps1` 只接受 `dsh-dafeiyu@0.1.23`；升级上游后先重新适配再合并。
- 修改安全边界（密钥、权限、审计、热键、重定向）时，同时补回归测试并更新 README/NOTICE。
