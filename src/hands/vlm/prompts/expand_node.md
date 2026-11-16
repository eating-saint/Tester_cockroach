你在为一棵关联展开任务树收集候选词。当前节点：「{{word}}」（所在的键：{{key}}）。
本页是「{{query}}」的搜索结果。请按下面的来源找出候选：
{{sources}}
关联条件（供你挑候选时参考，最终是否加入由后续判定决定）：
{{conditions}}
做法：
1. 先看本页；需要时最多打开 {{reads}} 个结果阅读（每打开一个都消耗一次次数），读完用 back 回来。
2. 用 extract 列出候选：每项 field 写「候选」，value 逐字抄页面上的词；{{field_hint}}
3. 没有任何候选时也要 extract 一项：field 写「候选」，value 写当前词「{{word}}」。
4. extract 之后用 complete 结束，summary 写一句看到了什么。
