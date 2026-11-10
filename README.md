# Bing_browse

本地视觉大模型做决策、确定性脚本负责执行的网页自动化测试系统。

当前版本 **ver0**：访问网页的"手脚"层。Python 3.12 + 自写原生 CDP 驱动 Chrome（默认无头），大模型的位置暂由规则决策器代替。

## 运行

```bash
pip install -e .[test]
python -m pytest                              # 单元测试
python run_tests.py                           # 一键验收 T01–T17
python -m hands run cases/t20_bing.yaml --headed   # Bing 冒烟，有界面
```

## 文档

- `docs/ver0/spec.md` → `plan.md` → `task.md` → `checklist.md` → `acceptance.md`
- 根目录 `*_v1.txt`：整体技术方案与调通方案
