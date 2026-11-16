"""关联展开任务（spec F11–F16）：从一组「键 <值, …>」出发逐个搜索，按关联条件增减节点，直到全部成为末端叶子。

- spec.py   任务类型定义读取与校验
- tree.py   键值树（纯逻辑）
- subtask.py 节点 → 子用例（交给现有 CaseRunner 执行）
- judge.py  4b 判定增减节点
- rank.py   点餐类价位解析与排序
- run.py    ExpandRunner
"""
